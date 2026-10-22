"""Exercise actual dispatch, provider fast routing, MCP and media adapters offline."""
import asyncio
import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from core import outcome
from test_automation import Isolated


class DispatchTests(Isolated):
    def host(self):
        # Compile the real methods without starting microphones or a GUI.
        source = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        jarvis = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "JarvisLive")
        names = {"_dispatch_automation", "_execute_tool", "_handle_tool_batch", "_on_text_command"}
        methods = [n for n in jarvis.body if getattr(n, "name", "") in names]
        cls = ast.ClassDef(name="Host", bases=[], keywords=[], body=methods, decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
        from google.genai import types
        import traceback
        env = dict(asyncio=asyncio, types=types, outcome=outcome, traceback=traceback, print=lambda *args, **kw: None)
        exec(compile(module, str(source), "exec"), env)
        host = env["Host"]()
        host._automation = self.runtime
        host.ui = Mock(muted=True, current_file=None)
        host.speak = Mock()
        host.speak_error = Mock()
        host._action_registry = Mock()
        host._action_registry.has.return_value = False
        host._action_registry.scheduling.return_value = None
        host._plugin_registry = Mock()
        host._plugin_registry.has.return_value = True
        host._plugin_registry.scheduling.return_value = None
        host._plugin_registry.run.return_value = outcome.done("fake", verified=True)
        return host

    def test_actual_dispatch_passes_context_and_reuses_receipt(self):
        host = self.host()
        from google.genai import types
        fc = types.FunctionCall(id="same", name="fake", args={"value": 1})
        async def exercise():
            first = await host._execute_tool(fc)
            second = await host._execute_tool(fc)
            self.assertEqual(first.response, second.response)
        asyncio.run(exercise())
        host._plugin_registry.run.assert_called_once()
        context = host._plugin_registry.run.call_args.kwargs["session_memory"]
        self.assertEqual(context["request_id"], "same")
        self.assertIn("turn_id", context)

    def test_old_session_does_not_receive_late_results(self):
        host = self.host()
        host.session = object()
        old = Mock()
        from google.genai import types
        fc = types.FunctionCall(id="old", name="fake", args={})
        asyncio.run(host._handle_tool_batch([fc], old, self.runtime.context()))
        old.send_tool_response.assert_not_called()


class FastPipelineTests(unittest.TestCase):
    def test_open_app_routes_before_llm_and_alias_replay_is_suppressed(self):
        from providers._tests import FakeLLM, FakeTTS, Cfg, FR
        from providers.engine_pipeline import PipelineEngine
        from providers.base import FunctionCall
        llm = FakeLLM([[{"tool_calls": [FunctionCall("duplicate", "open_app", {"app_name": "chrome"})]}],
                       [{"text": "Chrome is open."}]])
        tts = FakeTTS()
        async def exercise():
            engine = PipelineEngine(llm, tts=tts, config=Cfg(), fast_commands=True)
            runs = []
            await engine.send_client_content(turns={"role": "user", "parts": [{"text": "open chrome"}]})
            async for event in engine.receive():
                if event.tool_call:
                    self.assertEqual(len(llm.seen), 0)
                    self.assertTrue(tts.said)
                    for call in event.tool_call.function_calls:
                        runs.append(call)
                        await engine.send_tool_response(function_responses=[FR(call.id, call.name, "status=done action=open_app")])
                if event.server_content and event.server_content.turn_complete:
                    break
            await engine.close()
            self.assertEqual(len(runs), 1)
        asyncio.run(asyncio.wait_for(exercise(), 10))


class AdapterTests(Isolated):
    def test_media_conversion_with_real_ffmpeg(self):
        from plugins import media_assistant
        source = self.root / "input.wav"
        with wave.open(str(source), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 1600)
        target = self.root / "output.flac"
        result = media_assistant.run({"action": "convert", "input": str(source), "output": str(target)})
        self.assertIn("status=done", result, result)
        self.assertGreater(target.stat().st_size, 0)

    def test_public_research_rejects_loopback(self):
        from plugins.research_assistant import public_url
        with self.assertRaises(ValueError):
            public_url("http://127.0.0.1/secrets")

    def test_mcp_against_in_process_server(self):
        from mcp.server.fastmcp import FastMCP
        from plugins.integration_hub import _request
        import httpx
        server = FastMCP("test", json_response=True, stateless_http=True)
        @server.tool()
        def add(a: int, b: int) -> int:
            return a + b
        app = server.streamable_http_app()
        real_client = httpx.AsyncClient
        async def exercise():
            async with server.session_manager.run():
                def factory(**kwargs):
                    return real_client(transport=httpx.ASGITransport(app=app), **kwargs)
                with patch("httpx.AsyncClient", side_effect=factory):
                    result = await _request({"url": "http://127.0.0.1:8000/mcp"}, "call", "add", {"a": 2, "b": 3})
                self.assertFalse(result.get("isError"))
                self.assertIn("5", json.dumps(result))
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
