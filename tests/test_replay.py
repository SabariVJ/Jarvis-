"""Lifecycle regressions: all launches, closes and WhatsApp clicks are mocked."""
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import outcome
from core.command_router import plan
from plugins import task_manager
from test_automation import Isolated
import test_integration as integration


class ReplayTests(Isolated):
    def execute(self, name, args, invoke, context=None):
        return self.runtime.execute(name, args, invoke, context or self.runtime.context())

    def test_open_finish_close_open_chrome_without_whatsapp_relaunch(self):
        from actions import open_app, computer_control
        state, launches, closes = {}, [], []

        def match(app):
            return SimpleNamespace(pid=100 if app == 'WhatsApp' else 200, label=app,
                                   score=100, is_self=False)

        def running(app, **_):
            return [match(app)] if state.get(app) else []

        def launch(app):
            launches.append(app)
            state[app] = True
            return True

        def close(target):
            closes.append(target.label)
            state[target.label] = False
            return True, ''

        with patch.object(open_app, '_SYSTEM', 'Windows'), \
             patch.dict(open_app._LAUNCHERS, {'Windows': launch}), \
             patch.object(open_app.appidentity, 'running', side_effect=running), \
             patch.object(open_app.appidentity, 'snapshot', return_value=set()), \
             patch.object(open_app.appidentity, 'launcher_name', side_effect=lambda app: app), \
             patch.object(open_app.appidentity, 'wait_for_launch', side_effect=lambda app, *_a, **_k: match(app)), \
             patch.object(open_app.appidentity, 'focus', return_value=True), \
             patch.object(open_app.appidentity, 'request_close', side_effect=close):
            old = self.runtime.new_turn('Open WhatsApp')
            self.execute('open_app', {'app_name': 'WhatsApp'}, lambda: open_app.open_app({'app_name': 'WhatsApp'}), old)
            self.runtime.finish(old)
            current = self.runtime.new_turn('Close WhatsApp and open Google Chrome')
            results = []
            for name, args in plan(current['user_text']):
                invoke = (lambda a=args: computer_control.computer_control(a)) if name == 'computer_control' else (
                    lambda a=args: open_app.open_app(a))
                results.append(self.execute(name, args, invoke, current))
            # A restored model invents another WhatsApp open with a NEW ID.
            stale_launch = Mock()
            rejected = self.execute('open_app', {'app_name': 'WhatsApp'}, stale_launch,
                                    {**current, 'request_id': 'reconnected-new-id'})
            stale_launch.assert_not_called()
            self.assertIn('status=failed', rejected)
            self.assertTrue(all(r.startswith('status=done ') for r in results), results)
            self.assertEqual(closes, ['WhatsApp'])
            self.assertEqual(launches, ['WhatsApp', 'Google Chrome'])
            self.assertFalse(state['WhatsApp'])
            self.assertTrue(state['Google Chrome'])

    def test_failed_close_is_reported_and_chrome_still_opens(self):
        from actions import computer_control
        context = self.runtime.new_turn('Close WhatsApp and open Chrome')
        target = SimpleNamespace(pid=100, label='WhatsApp', score=100, is_self=False)
        chrome = Mock(return_value=outcome.done('open_app', app='Chrome'))
        with patch.object(computer_control.appidentity, 'running', return_value=[target]), \
             patch.object(computer_control.appidentity, 'request_close', return_value=(False, 'still running')):
            result = self.execute('computer_control', {'action': 'close_app', 'app_name': 'WhatsApp'},
                                  lambda: computer_control._close_app('WhatsApp'), context)
        self.assertIn('status=failed', result)
        self.assertNotIn('status=done', result)
        self.execute('open_app', {'app_name': 'Chrome'}, chrome, context)
        chrome.assert_called_once()

    def test_recreated_process_is_not_reported_closed(self):
        from actions import computer_control
        target = SimpleNamespace(pid=100, label='WhatsApp', score=100, is_self=False)
        with patch.object(computer_control.appidentity, 'running', return_value=[target]), \
             patch.object(computer_control.appidentity, 'request_close', return_value=(True, '')):
            result = computer_control._close_app('WhatsApp')
        self.assertIn('status=unverified', result)

    def test_completed_or_cancelled_execution_rejects_new_call_ids(self):
        for terminal in ['completed', 'cancelled']:
            context = self.runtime.new_turn('Open WhatsApp')
            invoke = Mock(return_value=outcome.done('open_app'))
            self.execute('open_app', {'app_name': 'WhatsApp'}, invoke, context)
            self.runtime.finish(context, terminal)
            with self.assertRaises(InterruptedError):
                self.execute('open_app', {'app_name': 'WhatsApp'}, invoke,
                             {**context, 'request_id': 'new-id'})
            invoke.assert_called_once()

    def test_delayed_worker_is_rechecked_after_waiting_for_desktop(self):
        context = self.runtime.new_turn('Open WhatsApp')
        invoke = Mock(return_value=outcome.done('open_app'))
        waiting = threading.Event()
        original = self.runtime.authorize
        def authorize(*args):
            waiting.set()
            return original(*args)
        with self.runtime._desktop, patch.object(self.runtime, 'authorize', side_effect=authorize), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.execute, 'open_app', {'app_name': 'WhatsApp'}, invoke, context)
            # Receipt means scheduling has happened, but the desktop lock is held.
            for _ in range(100):
                with self.runtime.db() as db:
                    if db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]:
                        break
                threading.Event().wait(.005)
            self.runtime.new_turn('Open Chrome')
            self.runtime._desktop.release()
            try:
                result = future.result(timeout=3)
            finally:
                self.runtime._desktop.acquire()
        self.assertIn('status=failed', result)
        invoke.assert_not_called()

    def test_inflight_action_checks_before_actual_popen(self):
        from actions import open_app
        context = self.runtime.new_turn('Open WhatsApp')
        def resolver(_):
            self.runtime.new_turn('Open Chrome')
            return 'C:/fake/WhatsApp.exe'
        with patch('core.windows_apps.executable', side_effect=resolver), \
             patch.object(open_app.subprocess, 'Popen') as popen, \
             patch.object(open_app, '_PYAUTOGUI', False):
            self.execute('open_app', {'app_name': 'WhatsApp'}, lambda: open_app._launch_windows('WhatsApp'), context)
        popen.assert_not_called()

    def test_canonical_args_dedupe_without_provider_id_and_with_new_id(self):
        context = self.runtime.new_turn('Open Google Chrome')
        invoke = Mock(return_value=outcome.done('open_app'))
        first = self.execute('open_app', {'app_name': 'chrome'}, invoke, context)
        second = self.execute('open_app', {'app_name': ' Google  Chrome '}, invoke,
                              {**context, 'request_id': 'different'})
        self.assertEqual(first, second)
        invoke.assert_called_once()

    def test_multistep_order_and_explicit_open_close_open(self):
        context = self.runtime.new_turn('Open WhatsApp and close WhatsApp and open WhatsApp')
        seen = []
        for name, args in plan(context['user_text']):
            result = self.execute(name, args, lambda n=name: (seen.append(n), outcome.done(n))[1], context)
            self.assertIn('status=done', result)
        self.assertEqual(seen, ['open_app', 'computer_control', 'open_app'])

    def test_job_survives_new_command_but_completed_worker_cannot_replay(self):
        steps = [{'tool': 'open_app', 'arguments_json': '{"app_name":"Notepad"}'}] * 2
        self.runtime.put('job', 'long', {'name': 'explicit job', 'steps': steps, 'next': 0,
                         'active_step': None, 'results': [], 'state': 'queued'})
        seen = []
        def dispatch(name, args, context):
            if not seen:
                self.runtime.new_turn('Open Chrome')
            return self.execute(name, args, lambda: (seen.append(context['step_id']), outcome.done(name))[1], context)
        self.runtime.dispatch = dispatch
        context = self.runtime.job_context('long')
        task_manager._run_job('long', self.runtime, context)
        self.assertEqual(seen, ['0', '1'])
        self.assertEqual(self.runtime.get('job', 'long')['state'], 'completed')
        task_manager._run_job('long', self.runtime, context)
        self.assertEqual(seen, ['0', '1'])

    def test_completed_command_does_not_cancel_explicit_job(self):
        foreground = self.runtime.context()
        self.runtime.put('job', 'j', {'state': 'queued'})
        job = self.runtime.job_context('j')
        self.runtime.finish(foreground)
        self.runtime.new_turn('What time is it?')
        self.runtime.check_cancelled(job)
        self.runtime.update('job', 'j', {'cancel_requested': True})
        with self.assertRaises(InterruptedError):
            self.runtime.check_cancelled(job)

    def test_turn_end_drains_admitted_batch_and_rejects_late_batch(self):
        context = self.runtime.context()
        batch = self.runtime.admit_batch(context)
        self.runtime.end_turn(context)
        self.runtime.check_cancelled(batch)
        with self.assertRaises(InterruptedError):
            self.runtime.admit_batch(context)
        self.runtime.release_batch(batch)
        with self.assertRaises(InterruptedError):
            self.runtime.check_cancelled(batch)

    def test_legacy_database_migrates_without_resetting_receipts_or_guards(self):
        from core.automation_runtime import AutomationRuntime
        legacy = self.root / 'legacy.db'
        with closing(sqlite3.connect(legacy)) as db:
            db.executescript('CREATE TABLE receipts(id TEXT PRIMARY KEY,name TEXT,status TEXT,result TEXT,started REAL,elapsed REAL);'
                             'CREATE TABLE guards(id TEXT PRIMARY KEY,at REAL);'
                             'CREATE TABLE records(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));'
                             "INSERT INTO receipts VALUES('old','open_app','done','original',1,1);"
                             "INSERT INTO guards VALUES('last_call',100);")
        migrated = AutomationRuntime(legacy)
        with migrated.db() as db:
            self.assertEqual(db.execute("SELECT result FROM receipts WHERE id='old'").fetchone()[0], 'original')
            self.assertEqual(db.execute("SELECT at FROM guards WHERE id='last_call'").fetchone()[0], 100)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='automation_events'").fetchone())

    def test_old_job_worker_cannot_overwrite_resumed_execution(self):
        self.runtime.put('job', 'j', {'state': 'queued', 'name': 'job', 'steps': [], 'next': 0, 'results': []})
        old = self.runtime.job_context('j')
        new = self.runtime.job_context('j', resume=True)
        task_manager._run_job('j', self.runtime, old)
        self.assertEqual(self.runtime.get('job', 'j')['execution_id'], new['execution_id'])
        self.assertEqual(self.runtime.get('job', 'j')['state'], 'queued')

    def test_recovery_pauses_jobs_without_dispatch(self):
        self.runtime.put('job', 'checkpoint', {'state': 'checkpoint', 'active_step': None})
        self.runtime.put('job', 'uncertain', {'state': 'running', 'active_step': 0})
        self.runtime.dispatch = Mock()
        with patch.object(task_manager, '_scheduler', None), patch.object(task_manager.threading, 'Thread') as thread:
            task_manager.start_scheduler(self.runtime)
        self.runtime.dispatch.assert_not_called()
        self.assertEqual(self.runtime.get('job', 'checkpoint')['state'], 'paused')
        self.assertEqual(self.runtime.get('job', 'uncertain')['state'], 'needs_attention')

    def test_process_runner_refuses_stale_command_before_popen(self):
        from core.process_runner import run_process
        context = self.runtime.context()
        self.runtime.new_turn('New command')
        with patch('core.process_runner.subprocess.Popen') as popen:
            with self.assertRaises(InterruptedError):
                run_process(['fake.exe'], context=context)
        popen.assert_not_called()

    def test_call_cannot_be_replayed_by_reconnect_or_unrelated_turn(self):
        context = self.runtime.new_turn('Voice call Ganesh on WhatsApp')
        with patch('core.automation_runtime.time.time', return_value=100):
            self.assertIsNone(self.runtime.reserve_call('Ganesh', context))
        with patch('core.automation_runtime.time.time', return_value=150):
            self.assertIn('already attempted', self.runtime.reserve_call('Ganesh', {**context, 'request_id': 'reconnect'}))
            self.runtime.finish(context)
            with self.assertRaises(InterruptedError):
                self.runtime.reserve_call('Ganesh', context)
            unrelated = self.runtime.new_turn('Open Chrome')
            with self.assertRaises(InterruptedError):
                self.runtime.reserve_call('Ganesh', unrelated)
            fresh = self.runtime.new_turn('Voice call Ganesh on WhatsApp')
            self.assertIsNone(self.runtime.reserve_call('Ganesh', fresh))

    def test_trace_has_execution_source_and_state_transitions(self):
        context = self.runtime.new_turn('Open Chrome', source='typed')
        self.execute('open_app', {'app_name': 'Chrome'}, lambda: outcome.done('open_app'), context)
        self.runtime.finish(context)
        with self.runtime.db() as db:
            rows = db.execute('SELECT * FROM automation_events WHERE execution_id=?', (context['execution_id'],)).fetchall()
        self.assertTrue({'command_started', 'tool_received', 'tool_running', 'tool_finished', 'execution_completed'} <= {r['event'] for r in rows})
        self.assertTrue(all(r['task_id'] and r['turn_id'] and r['source'] for r in rows))


class PipelineReplayTests(Isolated):
    def test_duplicate_and_old_tool_responses_cannot_start_generation(self):
        from providers._tests import FakeLLM, Cfg, FR
        from providers.engine_pipeline import PipelineEngine
        async def exercise():
            engine = PipelineEngine(FakeLLM([]), config=Cfg())
            engine._runtime = self.runtime
            engine._awaiting = {'old': 'open_app'}
            engine._pending_sigs = {'old': 'sig'}
            start = Mock()
            with patch.object(engine, '_start_generation', start):
                response = FR('old', 'open_app', 'status=done action=open_app')
                await engine.send_tool_response(function_responses=[response, response])
                await engine.send_tool_response(function_responses=[response])
                self.assertEqual(start.call_count, 1)
                self.assertEqual(sum(h['role'] == 'tool' for h in engine._history), 1)
                self.runtime.new_turn('Close WhatsApp and open Chrome')
                await engine.send_tool_response(function_responses=[response])
                self.assertEqual(start.call_count, 1)
            await engine.close()
        asyncio.run(exercise())

    def test_pipeline_close_then_open_is_bound_to_original_command(self):
        from providers._tests import FakeLLM, Cfg, FR
        from providers.engine_pipeline import PipelineEngine
        config = SimpleNamespace(system_instruction='JARVIS', tools=[{'function_declarations': [
            *Cfg.tools[0]['function_declarations'], {'name': 'computer_control', 'description': 'Close app',
                'parameters': {'type': 'OBJECT', 'properties': {'action': {'type': 'STRING'}, 'app_name': {'type': 'STRING'}}}}]}])
        async def exercise():
            engine = PipelineEngine(FakeLLM([[{'text': 'Finished.'}]]), config=config, fast_commands=True)
            engine._runtime = self.runtime
            original = self.runtime.new_turn('Close WhatsApp and open Chrome')
            await engine.send_client_content(turns={'role': 'user', 'parts': [{'text': original['user_text']}]})
            async for event in engine.receive():
                if event.tool_call:
                    self.assertEqual([c.name for c in event.tool_call.function_calls], ['computer_control', 'open_app'])
                    self.assertEqual(event.automation_context['execution_id'], original['execution_id'])
                    # The host executes every call in this batch in list order.
                    responses = []
                    for c in event.tool_call.function_calls:
                        result = self.runtime.execute(c.name, c.args, lambda n=c.name: outcome.done(n), event.automation_context)
                        self.assertIn('status=done', result)
                        responses.append(FR(c.id, c.name, result))
                    await engine.send_tool_response(function_responses=responses)
                if event.server_content and event.server_content.turn_complete:
                    break
            await engine.close()
        asyncio.run(asyncio.wait_for(exercise(), 5))

    def test_stale_provider_event_is_not_emitted(self):
        from providers._tests import FakeLLM, Cfg
        from providers.engine_pipeline import PipelineEngine, _EMISSION_CONTEXT
        from providers.base import Msg, FunctionCall
        async def exercise():
            engine = PipelineEngine(FakeLLM([]), config=Cfg())
            token = _EMISSION_CONTEXT.set((0, self.runtime.context()))
            try:
                engine._command_epoch = 1
                await engine._emit(Msg.calls([FunctionCall('old', 'open_app', {'app_name': 'WhatsApp'})]))
                self.assertTrue(engine._queue.empty())
            finally:
                _EMISSION_CONTEXT.reset(token)
            await engine.close()
        asyncio.run(exercise())


class HostReplayTests(Isolated):
    host = integration.DispatchTests.host

    def test_delayed_typed_callback_does_not_acquire_newer_command_identity(self):
        host = self.host()
        host._loop = Mock()
        host.session = object()
        host._wake_enabled = False
        host.plugin_say = Mock()
        pending = []
        with patch('asyncio.run_coroutine_threadsafe', side_effect=lambda coro, loop: pending.append(coro)):
            host._on_text_command('Open WhatsApp')
            host._on_text_command('Close WhatsApp and open Chrome')
        async def exercise():
            for coroutine in pending:
                await coroutine
        asyncio.run(exercise())
        self.assertEqual([c.args[0] for c in host._plugin_registry.run.call_args_list], ['computer_control', 'open_app'])
        self.assertEqual(host._plugin_registry.run.call_args.args[1]['app_name'], 'Google Chrome')

    def test_native_old_call_id_is_not_rebound_to_new_command(self):
        host = self.host()
        from google.genai import types
        call = types.FunctionCall(id='old-model-id', name='fake', args={'value': 1})
        asyncio.run(host._execute_tool(call))
        self.runtime.finish(self.runtime.context())
        self.runtime.new_turn('An unrelated command')
        result = asyncio.run(host._execute_tool(call))
        host._plugin_registry.run.assert_called_once()
        self.assertIn('previous command', result.response['result'])

    def test_reused_model_id_is_allowed_for_fresh_explicit_app_command(self):
        host = self.host()
        from google.genai import types
        call = types.FunctionCall(id='fixed-model-id', name='open_app', args={'app_name': 'Chrome'})
        self.runtime.new_turn('Open Chrome')
        asyncio.run(host._execute_tool(call))
        self.runtime.new_turn('Open Chrome')
        asyncio.run(host._execute_tool(call))
        self.assertEqual(host._plugin_registry.run.call_count, 2)
    def test_stale_session_cannot_execute_any_tool(self):
        host = self.host()
        host.session = object()
        from google.genai import types
        asyncio.run(host._handle_tool_batch([types.FunctionCall(id='old', name='fake', args={})], Mock(), self.runtime.context()))
        host._plugin_registry.run.assert_not_called()

    def test_host_executes_one_batch_in_order(self):
        host = self.host()
        session = SimpleNamespace(send_tool_response=Mock())
        from unittest.mock import AsyncMock
        session.send_tool_response = AsyncMock()
        host.session = session
        host._flush_pending_vision = AsyncMock()
        from google.genai import types
        calls = [types.FunctionCall(id=str(i), name='fake', args={'index': i}) for i in range(3)]
        asyncio.run(host._handle_tool_batch(calls, session, self.runtime.admit_batch(self.runtime.context())))
        self.assertEqual([c.args[1]['index'] for c in host._plugin_registry.run.call_args_list], [0, 1, 2])

