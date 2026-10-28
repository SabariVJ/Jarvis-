"""Offline integration/regression tests. Never places calls or drives the GUI."""
import asyncio
import ast
from concurrent.futures import ThreadPoolExecutor
import csv
import importlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core import automation_runtime, outcome
from core.automation_runtime import AutomationRuntime
from plugins import whatsapp_control as whatsapp
from plugins import task_manager, knowledge_library, document_studio, plugin_workshop


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = AutomationRuntime(self.root / "automation.db")
        self.runtime.new_turn('test command')
        self.old_runtime = automation_runtime._instance
        automation_runtime._instance = self.runtime

    def tearDown(self):
        automation_runtime._instance = self.old_runtime
        self.tmp.cleanup()


class RuntimeTests(Isolated):
    def test_receipt_survives_new_runtime_and_reconnect(self):
        context = self.runtime.context("id1")
        invoke = Mock(return_value=outcome.done("test", verified=True))
        self.runtime.execute("test", {}, invoke, context)
        reloaded = AutomationRuntime(self.runtime.path)
        with self.assertRaises(InterruptedError):
            reloaded.execute("test", {}, invoke, reloaded.context("id1"))
        self.assertEqual(invoke.call_count, 1)

    def test_reused_provider_id_in_a_new_user_turn_is_allowed(self):
        invoke = Mock(return_value=outcome.done("test"))
        self.runtime.execute("test", {}, invoke, self.runtime.context("fixed"))
        self.runtime.new_turn("do it again")
        self.runtime.execute("test", {}, invoke, self.runtime.context("fixed"))
        self.assertEqual(invoke.call_count, 2)

    def test_simultaneous_duplicate_executes_once(self):
        count = []
        def invoke():
            count.append(1)
            time.sleep(.03)
            return outcome.done("test")
        ctx = self.runtime.context("same")
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.runtime.execute("test", {}, invoke, ctx), range(2)))
        self.assertEqual(len(count), 1)
        self.assertTrue(any("status=done" in r for r in results))

    def test_call_cooldown_and_new_ids_in_same_turn(self):
        self.runtime.new_turn('voice call Ganesh on WhatsApp')
        ctx = self.runtime.context("id1")
        with patch("core.automation_runtime.time.time", return_value=100):
            self.assertIsNone(self.runtime.reserve_call("Ganesh", ctx))
        with patch("core.automation_runtime.time.time", return_value=131):
            self.assertIn("already attempted", self.runtime.reserve_call("Ganesh", {**ctx, "request_id": "id2"}))
            self.runtime.new_turn("call Ganesh on WhatsApp")
            self.assertIsNone(self.runtime.reserve_call("Ganesh", self.runtime.context()))
        with patch("core.automation_runtime.time.time", return_value=150):
            self.runtime.new_turn("call Appa on WhatsApp")
            self.assertIn("cooldown", self.runtime.reserve_call("Appa", self.runtime.context()))

    def test_cancel_prevents_queued_side_effect(self):
        ctx = self.runtime.context("queued")
        self.runtime.cancel()
        invoke = Mock()
        with self.assertRaises(InterruptedError):
            self.runtime.execute("test", {}, invoke, ctx)
        invoke.assert_not_called()

    def test_metrics_and_context(self):
        self.runtime.execute("test", {"contact": "Ganesh"}, lambda: outcome.done("test"))
        self.assertEqual(self.runtime.context()["last_contact"], "Ganesh")
        self.assertEqual(self.runtime.stats()[0]["calls"], 1)


class WhatsappTests(Isolated):
    def setUp(self):
        super().setUp()
        self.runtime.new_turn('voice call Ganesh on WhatsApp')
        self.button = Mock()
        self.window = Mock()
        self.patches = [patch.object(whatsapp, "_active_call", return_value=False),
                        patch.object(whatsapp, "_ensure_window", return_value=self.window),
                        patch.object(whatsapp, "_search", return_value=[Mock()]),
                        patch.object(whatsapp, "_open_chat", return_value=None),
                        patch.object(whatsapp, "_header_matches", return_value=True),
                        patch.object(whatsapp, "_call_button", return_value=(self.button, False))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def call(self):
        return whatsapp.run({"action": "voice_call", "contact": "Ganesh"}, session_memory=self.runtime.context("call"))

    def test_single_call_no_confirmation_and_duplicate_block(self):
        result = self.call()
        self.assertIn("status=unverified", result)
        self.call()
        self.button.click_input.assert_called_once()
        self.assertNotIn("_confirm_call", whatsapp._run.__code__.co_names)

    def test_wrong_header_never_clicks(self):
        whatsapp._header_matches.return_value = False
        self.assertIn("No call", self.call())
        self.button.click_input.assert_not_called()

    def test_header_changes_during_menu_never_clicks_option(self):
        whatsapp._call_button.return_value = (self.button, True)
        whatsapp._header_matches.side_effect = [True, True, False]
        option = Mock()
        with patch.object(whatsapp, "_call_menu_option", return_value=option), patch.object(whatsapp.time, "sleep"):
            self.assertIn("Chat changed", self.call())
        option.click_input.assert_not_called()

    def test_exception_after_click_does_not_retry(self):
        self.button.click_input.side_effect = RuntimeError("uncertain click")
        self.call()
        self.call()
        self.button.click_input.assert_called_once()

    def test_active_call_blocks_all_clicks(self):
        whatsapp._active_call.return_value = True
        self.assertIn("already active", self.call())
        whatsapp._ensure_window.assert_not_called()

    def test_closed_loading_error_no_retry(self):
        whatsapp._ensure_window.side_effect = RuntimeError("not ready")
        self.assertIn("not ready", self.call())
        self.button.click_input.assert_not_called()


class WorkflowTests(Isolated):
    def steps(self):
        return [{"tool": "open_app", "arguments_json": '{"app_name":"Notepad"}'}]

    def test_disallow_call_replay(self):
        with self.assertRaises(ValueError):
            task_manager.validate_steps([{"tool": "whatsapp_control", "arguments_json": '{"action":"voice_call"}'}])

    def job(self, key="j", count=1):
        self.runtime.put("job", key, {"name": "demo", "steps": self.steps() * count,
            "next": 0, "active_step": None, "results": [], "state": "queued"})

    def test_completion_and_checkpoint(self):
        self.job(count=2)
        self.runtime.dispatch = Mock(return_value=outcome.done("open_app", verified=True))
        task_manager._run_job("j", self.runtime, self.runtime.context())
        self.assertEqual(self.runtime.get("job", "j")["state"], "completed")
        self.assertEqual(self.runtime.get("job", "j")["next"], 2)

    def test_uncertain_result_stops_next_step(self):
        self.job(count=2)
        self.runtime.dispatch = Mock(return_value=outcome.unverified("open_app", "not seen"))
        task_manager._run_job("j", self.runtime, self.runtime.context())
        self.runtime.dispatch.assert_called_once()
        self.assertEqual(self.runtime.get("job", "j")["state"], "needs_attention")

    def test_resume_does_not_repeat_completed_step(self):
        self.job(count=2)
        job = self.runtime.get("job", "j")
        job.update(next=1, state="paused")
        self.runtime.put("job", "j", job)
        self.runtime.dispatch = Mock(return_value=outcome.done("open_app"))
        task_manager._run_job("j", self.runtime, self.runtime.context())
        self.runtime.dispatch.assert_called_once()

    def test_cancel_between_steps(self):
        self.job(count=2)
        def dispatch(*_):
            task_manager.run({"action": "cancel", "id": "j"})
            return outcome.done("open_app")
        self.runtime.dispatch = Mock(side_effect=dispatch)
        task_manager._run_job("j", self.runtime, self.runtime.context())
        self.assertEqual(self.runtime.dispatch.call_count, 1)
        self.assertEqual(self.runtime.get("job", "j")["state"], "paused")

    def test_schedule_consumed_once(self):
        self.runtime.put("trigger", "t", {"type": "schedule", "at": 1, "enabled": True, "name": "r"})
        with patch.object(task_manager, "_start", return_value="job") as start:
            task_manager.tick(self.runtime)
            task_manager.tick(self.runtime)
            start.assert_called_once()

    def test_recording_saves_verified_actions_only(self):
        self.runtime.new_turn('open Notepad and open Chrome')
        self.runtime.record_start()
        self.runtime.execute("open_app", {'app_name': 'Notepad'}, lambda: outcome.done("open_app"))
        self.runtime.execute("open_app", {'app_name': 'Chrome'}, lambda: outcome.failed("open_app", "not installed"))
        self.assertEqual(len(self.runtime.record_finish()), 1)


class DocumentTests(Isolated):
    def test_index_search_forget_and_exclude_credentials(self):
        (self.root / "notes.md").write_text("Ganesh project architecture and Windows automation", encoding="utf-8")
        (self.root / "secret.txt").write_text("SECRET_TOKEN", encoding="utf-8")
        result = knowledge_library.run({"action": "index", "path": str(self.root)})
        self.assertIn("status=done", result)
        self.assertIn("notes.md", knowledge_library.run({"action": "search", "query": "architecture"}))
        self.assertNotIn("SECRET_TOKEN", knowledge_library.run({"action": "search", "query": "SECRET_TOKEN"}))
        knowledge_library.run({"action": "forget", "path": str(self.root)})
        self.assertNotIn("notes.md", knowledge_library.run({"action": "search", "query": "architecture"}))

    def test_create_word_and_never_overwrite(self):
        target = self.root / "report.docx"
        args = {"action": "report", "output": str(target), "title": "Test", "content": "Verified content"}
        self.assertIn("status=done", document_studio.run(args))
        from docx import Document
        self.assertIn("Verified content", [p.text for p in Document(target).paragraphs])
        before = target.read_bytes()
        self.assertIn("status=failed", document_studio.run(args))
        self.assertEqual(before, target.read_bytes())

    def test_csv_merge_and_formula_text(self):
        paths = []
        for index in range(2):
            path = self.root / f"in{index}.csv"
            path.write_text("name,value\nexample,=1+1\n", encoding="utf-8")
            paths.append(str(path))
        output = self.root / "out.xlsx"
        result = document_studio.run({"action": "combine_csv", "inputs": paths, "output": str(output)})
        self.assertIn("status=done", result)
        from openpyxl import load_workbook
        book = load_workbook(output)
        self.assertEqual(book.active.max_row, 3)
        self.assertEqual(book.active["B2"].data_type, "s")
        book.close()

    def test_workshop_static_check_never_executes(self):
        source = "PLUGIN={'name':'demo','description':'example','parameters':{'type':'OBJECT'}}\ndef run(parameters): return 'ok'\nraise RuntimeError('must not execute')"
        self.assertTrue(plugin_workshop.check(source)["syntax_valid"])


class RoutingTests(unittest.TestCase):
    def test_exact_commands_only(self):
        from core.command_router import route
        self.assertEqual(route("Jarvis, open VS Code please"), ("open_app", {"app_name": "Visual Studio Code"}))
        for text in ("don't open Chrome", "open Chrome and go to YouTube", "call Ganesh", "open chrome.exe & whoami"):
            self.assertIsNone(route(text))

    def test_launcher_rejects_editor_title_match(self):
        from actions import open_app
        fake = SimpleNamespace(score=20, label="VS Code", pid=10)
        with patch.object(open_app.appidentity, "running", return_value=[fake]), \
             patch.object(open_app.appidentity, "snapshot", return_value=set()), \
             patch.object(open_app, "_launch_and_verify", return_value=outcome.done("open_app")) as launch, \
             patch.object(open_app.appidentity, "focus") as focus:
            open_app.open_app({"app_name": "WhatsApp"})
        launch.assert_called_once()
        focus.assert_not_called()

    def test_plugin_discovery_and_provider_schema(self):
        from core.plugin_loader import discover_plugins
        from google.genai import types
        registry = discover_plugins(Path(__file__).resolve().parents[1] / "plugins", set(), logger=lambda _: None)
        records = registry.list_for_ui()
        self.assertEqual(len(records), 17)
        self.assertTrue(all(row["valid"] for row in records), records)
        for declaration in registry.get_tool_declarations():
            types.FunctionDeclaration(**{k: v for k, v in declaration.items() if k not in {"tier", "brief"}})


if __name__ == "__main__":
    unittest.main()
