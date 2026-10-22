import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch
from core.automation_metrics import timed_stream
from core.command_router import git_route
from plugins import whatsapp_control as whatsapp
from test_automation import Isolated


class PerformanceTests(Isolated):
    def test_control_scan_requests_only_needed_types(self):
        window = Mock()
        window.descendants.return_value = [Mock()]
        items = whatsapp._items(window, ('Edit', 'ComboBox'))
        self.assertEqual(len(items), 2)
        self.assertEqual([c.kwargs['control_type'] for c in window.descendants.call_args_list], ['Edit', 'ComboBox'])
        with self.runtime.db() as db:
            row = db.execute("SELECT details FROM automation_events WHERE event='performance_span'").fetchone()
        self.assertIn('accessibility_scan', row[0])

    def test_window_detection_wraps_only_actual_whatsapp_process_handles(self):
        desktop = Mock()
        window = Mock()
        window.is_visible.return_value = True
        window.process_id.return_value = 123
        window.window_text.return_value = 'WhatsApp'
        desktop.window.return_value.wrapper_object.return_value = window
        proc = SimpleNamespace(info={'pid': 123, 'name': 'WhatsApp.exe'})
        with patch('psutil.process_iter', return_value=[proc]), patch('core.appidentity._hwnds_for', return_value=[456]), \
             patch.object(whatsapp, '_ui', return_value=desktop):
            self.assertIs(whatsapp._window(), window)
        desktop.windows.assert_not_called()
        desktop.window.assert_called_once_with(handle=456)

    def test_editor_window_cannot_be_detected_as_whatsapp(self):
        proc = SimpleNamespace(info={'pid': 123, 'name': 'Code.exe'})
        with patch('psutil.process_iter', return_value=[proc]), patch.object(whatsapp, '_ui') as ui:
            self.assertIsNone(whatsapp._window())
        ui.assert_not_called()

    def test_model_timing_excludes_consumer_speech_time(self):
        recorded = []
        runtime = Mock()
        runtime.trace.side_effect = lambda *args, **kw: recorded.append(kw['seconds'])
        async def stream():
            await asyncio.sleep(.005)
            yield {'text': 'hello'}
        async def exercise():
            async for _ in timed_stream(stream(), self.runtime.context(), runtime):
                await asyncio.sleep(.15)  # Simulated speech synthesis, outside model iteration.
        asyncio.run(exercise())
        self.assertEqual(len(recorded), 1)
        self.assertLess(recorded[0], .12)

    def test_readonly_git_routes_never_route_push_or_commit(self):
        self.assertEqual(git_route('Jarvis, show git status')[1]['action'], 'status')
        self.assertEqual(git_route('show git diff')[1]['action'], 'diff')
        for command in ('push everything', 'git commit', 'force push main', 'git status and push'):
            self.assertIsNone(git_route(command))
