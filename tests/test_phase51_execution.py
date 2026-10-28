"""Offline supervised execution regressions; no real model, audio or calls."""
import asyncio
import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import codex_tasks as tasks, codex_sandbox as sandbox, local_notifications as alerts, outcome
from core.codex_commands import command
from core.command_router import resolve
from plugins import codex_assistant, task_manager, whatsapp_control
from test_automation import Isolated


class CodexTests(Isolated):
    def setUp(self):
        super().setUp()
        self.project = self.root / 'project'
        (self.project / 'plugins').mkdir(parents=True)
        (self.project / 'plugins/demo.py').write_text('value = 1\n', encoding='utf-8')
        self.storage = self.root / 'artifacts'
        self.patches = [patch.object(tasks, 'ROOT', self.project), patch.object(tasks, 'WORK', self.storage),
                        patch.object(sandbox, 'verified_cli', return_value={'path': 'C:/approved/codex.exe', 'sha256': 'approved'}),
                        patch.object(sandbox, 'fingerprint', return_value='approved'),
                        patch.object(tasks.codex_bridge, 'diagnostic', return_value={'authenticated': True, 'authentication_mode': 'ChatGPT'}),
                        patch('core.codex_preflight.inspect', return_value={'ready': True, 'backend': 'verified_test_fixture'}),
                        patch('core.mxc_backend.checked_command', side_effect=lambda job, check, context, rt: sandbox.cli_command(job['cli']['path'], tasks.workspace_for(job['id']) / 'workspace', tasks.workspace_for(job['id']) / 'temp', '', read_only=job.get('read_only', False))),
                        patch('core.mxc_backend.source_prompt', return_value=''),
                        patch.object(alerts, 'get_plugin_config', return_value={})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def prepare(self, goal='refactor plugins/demo.py'):
        context = self.runtime.new_turn('ask Codex to ' + goal)
        return tasks.prepare(command(context['user_text']), context, self.runtime)

    def complete(self, change=True, failure=None, no_turn=False):
        job = self.prepare()
        def fake_stream(args, prompt, workspace, temp, context, rt, event, timeout):
            self.assertEqual(timeout, 300)
            self.assertNotIn('danger-full-access', ' '.join(args))
            if failure:
                if isinstance(failure, BaseException):
                    raise failure
                event({'type': 'turn.failed', 'error': {'message': failure}})
                return {'returncode': 1, 'turn_completed': False, 'seconds': .01}
            if change:
                (workspace / 'plugins/demo.py').write_text('value = 2\n', encoding='utf-8')
            event({'type': 'item.started', 'item': {'type': 'command_execution'}})
            event({'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'python -m unittest',
                  'exit_code': 0, 'aggregated_output': 'Ran 2 tests in 0.01s\nOK'}})
            event({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Reviewed or updated the source.'}})
            if not no_turn:
                event({'type': 'turn.completed'})
            return {'returncode': 0, 'turn_completed': not no_turn, 'seconds': .02}
        def synchronous(identity, worker, rt):
            context = rt.job_context(identity)
            worker(identity, rt, context)
        context = self.runtime.new_turn('run Codex task ' + job['id'])
        with patch.object(task_manager, 'launch_supervised', side_effect=synchronous), patch.object(sandbox, 'stream', side_effect=fake_stream) as run:
            tasks.run(command(context['user_text']), context, self.runtime)
        return tasks.task(job['id'], self.runtime), run

    def test_source_copy_preserves_live_dirty_files_and_preparation_is_not_execution(self):
        (self.project / 'plugins/demo.py').write_text('# uncommitted\nvalue = 7\n', encoding='utf-8')
        job = self.prepare()
        self.assertEqual(job['state'], 'prepared')
        self.assertFalse(job['start_consumed'])
        self.assertEqual((self.storage / job['id'] / 'workspace/plugins/demo.py').read_text(), '# uncommitted\nvalue = 7\n')
        self.assertEqual((self.project / 'plugins/demo.py').read_text(), '# uncommitted\nvalue = 7\n')

    def test_prepare_duplicate_reuses_identity(self):
        context = self.runtime.new_turn('ask Codex to refactor plugins/demo.py')
        args = command(context['user_text'])
        first = tasks.prepare(args, context, self.runtime)
        second = tasks.prepare(args, context, self.runtime)
        self.assertEqual(first['id'], second['id'])

    def test_private_and_parent_traversal_source_scopes_are_rejected(self):
        for name in ('../escape.py', 'config/api_keys.json', '.env', 'memory/automation.db', 'downloads/code.py'):
            with self.assertRaises(ValueError):
                sandbox.snapshot(self.project, self.root / 'copy', [name])

    def test_missing_file_scope_does_not_send_entire_project(self):
        with self.assertRaisesRegex(ValueError, 'Name the project-relative'):
            self.prepare('make things better')

    def test_no_api_login_or_authentication_fallback(self):
        tasks.codex_bridge.diagnostic.return_value = {'authenticated': True, 'authentication_mode': 'API key'}
        with self.assertRaisesRegex(ValueError, 'paid API'):
            self.prepare()

    def test_explicit_run_completes_proposal_not_live_edits(self):
        job, run = self.complete()
        self.assertEqual(job['state'], 'review_required')
        self.assertTrue(job['proposal_verified'])
        self.assertEqual(job['tests_observed'][0]['count'], 2)
        self.assertEqual((self.project / 'plugins/demo.py').read_text(), 'value = 1\n')
        self.assertEqual(job['progress'][0]['state'], 'verified')
        self.assertTrue(Path(job['result_path']).is_file())
        run.assert_called_once()

    def test_duplicate_run_and_reconnect_never_start_again(self):
        job, run = self.complete()
        context = self.runtime.new_turn('run Codex task ' + job['id'])
        with patch.object(task_manager, 'launch_supervised') as launch:
            repeated = tasks.run(command(context['user_text']), context, self.runtime)
        launch.assert_not_called()
        self.assertTrue(repeated['start_consumed'])

    def test_stale_worker_invalidated_before_start(self):
        job = self.prepare()
        context = self.runtime.new_turn('run Codex task ' + job['id'])
        self.runtime.new_turn('open Chrome')
        with patch.object(task_manager, 'launch_supervised') as launch:
            with self.assertRaises(InterruptedError):
                tasks.run(command(context['user_text']), context, self.runtime)
        launch.assert_not_called()

    def test_usage_limit_is_terminal_and_does_not_charge_or_retry(self):
        job, run = self.complete(failure='usage_limit_reached')
        self.assertEqual(job['state'], 'needs_attention')
        self.assertIn('No paid API fallback', job['error'])
        run.assert_called_once()

    def test_timeout_and_cancellation_preserve_live_project(self):
        for failure, expected in [(TimeoutError(), 'needs_attention'), (InterruptedError(), 'cancelled')]:
            job, run = self.complete(failure=failure)
            self.assertEqual(job['state'], expected)
            self.assertEqual((self.project / 'plugins/demo.py').read_text(), 'value = 1\n')
            run.assert_called_once()

    def test_missing_final_event_does_not_claim_success(self):
        job, _ = self.complete(no_turn=True)
        self.assertEqual(job['state'], 'needs_attention')
        self.assertFalse(job.get('proposal_verified', False))

    def test_observed_no_changes_is_not_fabricated_implementation(self):
        job, _ = self.complete(change=False)
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(job['changes'], [])

    def test_live_concurrent_edits_are_not_overwritten(self):
        job, _ = self.complete()
        (self.project / 'plugins/demo.py').write_text('user_edit = True\n', encoding='utf-8')
        text = 'apply Codex task ' + job['id'] + ' review ' + job['review']
        context = self.runtime.new_turn(text)
        with self.assertRaisesRegex(ValueError, 'not overwritten'):
            tasks.apply(command(text), context, self.runtime)
        self.assertEqual((self.project / 'plugins/demo.py').read_text(), 'user_edit = True\n')

    def test_reviewed_safe_apply_makes_backup_and_cannot_replay(self):
        job, _ = self.complete()
        text = 'apply Codex task ' + job['id'] + ' review ' + job['review']
        context = self.runtime.new_turn(text)
        result = tasks.apply(command(text), context, self.runtime)
        self.assertEqual(result['state'], 'applied')
        self.assertEqual((self.project / 'plugins/demo.py').read_text(), 'value = 2\n')
        backup = self.storage / job['id'] / 'apply-backup/plugins/demo.py'
        self.assertEqual(backup.read_text(), 'value = 1\n')
        with self.assertRaises(ValueError):
            tasks.apply(command(text), context, self.runtime)

    def test_stale_review_modified_proposal_and_deletion_are_rejected(self):
        job, _ = self.complete()
        file = self.storage / job['id'] / 'workspace/plugins/demo.py'
        file.write_text('value = 3\n')
        text = 'apply Codex task ' + job['id'] + ' review ' + job['review']
        context = self.runtime.new_turn(text)
        with self.assertRaisesRegex(ValueError, 'digest'):
            tasks.apply(command(text), context, self.runtime)
        file.unlink()  # isolated test proposal only, not a user file
        with self.assertRaisesRegex(ValueError, 'deletion'):
            tasks.inspect_proposal(job)

    def test_sensitive_changes_require_manual_supervision(self):
        job, _ = self.complete()
        with patch.object(tasks, 'SENSITIVE', {'plugins/demo.py'}):
            proposal = tasks.inspect_proposal(job)
            self.runtime.update('job', job['id'], {'review': proposal['review']})
            text = 'apply Codex task ' + job['id'] + ' review ' + proposal['review']
            context = self.runtime.new_turn(text)
            with self.assertRaisesRegex(ValueError, 'Sensitive'):
                tasks.apply(command(text), context, self.runtime)

    def test_partial_apply_records_written_files_and_never_replays(self):
        job, _ = self.complete()
        other = self.project / 'plugins/other.py'
        other.write_text('value = 7\n')
        directory = self.storage / job['id']
        (directory / 'workspace/plugins/other.py').write_text('value = 8\n')
        (directory / 'originals/plugins/other.py').write_text('value = 7\n')
        import hashlib
        job['manifest']['plugins/other.py'] = hashlib.sha256(other.read_bytes()).hexdigest()
        proposal = tasks.inspect_proposal(job)
        job['review'] = proposal['review']
        self.runtime.put('job', job['id'], job)
        text = 'apply Codex task ' + job['id'] + ' review ' + job['review']
        context = self.runtime.new_turn(text)
        replace = os.replace
        count = []
        def fail_second(source, target):
            count.append(1)
            if len(count) == 2:
                raise OSError('simulated write error')
            replace(source, target)
        with patch.object(os, 'replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                tasks.apply(command(text), context, self.runtime)
        receipt = tasks.task(job['id'], self.runtime)
        self.assertEqual(receipt['state'], 'needs_attention')
        self.assertEqual(receipt['applied_files'], ['plugins/demo.py'])
        self.assertEqual(other.read_text(), 'value = 7\n')
        with self.assertRaises(ValueError):
            tasks.apply(command(text), context, self.runtime)

    def test_unknown_new_file_blocks_scope_expansion(self):
        job, _ = self.complete()
        (self.storage / job['id'] / 'workspace/unapproved.py').write_text('x=1\n')
        with self.assertRaisesRegex(ValueError, 'out-of-scope'):
            tasks.inspect_proposal(job)

    def test_jobs_cannot_auto_resume_or_enter_replay_recipes(self):
        job, _ = self.complete()
        self.runtime.update('job', job['id'], {'state': 'paused'})
        result = task_manager.run({'action': 'resume', 'id': job['id']})
        self.assertIn('cannot be automatically resumed', result)
        with self.assertRaises(ValueError):
            task_manager.validate_steps([{'tool': 'codex_assistant', 'arguments_json': '{"action":"run"}'}])

    def test_foreground_command_does_not_cancel_an_explicit_background_job(self):
        job = self.prepare()
        self.runtime.update('job', job['id'], {'state': 'queued', 'start_consumed': True})
        context = self.runtime.job_context(job['id'])
        self.runtime.new_turn('show Git status')
        self.runtime.check_cancelled(context)
        self.runtime.update('job', job['id'], {'cancel_requested': True})
        with self.assertRaises(InterruptedError):
            self.runtime.check_cancelled(context)

    def test_cli_flags_force_chatgpt_and_deny_live_secrets_and_network(self):
        args = sandbox.cli_command('codex.exe', self.root / 'copy', self.root / 'tmp', 'goal')
        joined = ' '.join(args)
        for value in ('forced_login_method="chatgpt"', 'approval_policy="never"',
                      'windows.sandbox="elevated"', 'permissions.jarvis.network.enabled=false',
                      '":root"="deny"', '--ignore-user-config', '--ignore-rules', '--ephemeral'):
            self.assertIn(value, joined)
        self.assertNotIn('danger-full-access', joined)
        self.assertNotIn('--approve-for-me', joined)
        for value in ('features.image_generation=false', 'features.hooks=false',
                      'features.skill_mcp_dependency_install=false', 'features.skip_host_skill_discovery=true'):
            self.assertIn(value, joined)
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake', 'CODEX_API_KEY': 'fake',
                                     'JARVIS_TWILIO_AUTH_TOKEN': 'fake', 'OPENAI_BASE_URL': 'https://bad.invalid'}):
            env = sandbox.clean_environment(self.root)
        for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'JARVIS_TWILIO_AUTH_TOKEN', 'OPENAI_BASE_URL'):
            self.assertNotIn(name, env)

    def test_local_stt_routes_execution_without_model_planning(self):
        for text, action in [('ask Codex to review plugins/demo.py', 'prepare'),
                              ('run Codex task abcdef012345', 'run'),
                              ('cancel Codex task abcdef012345', 'cancel')]:
            result = resolve(text, [codex_assistant.PLUGIN])
            self.assertEqual(result[0][1]['action'], action)
            self.assertIsNone(resolve(text, []))

    def test_fake_test_prose_and_zero_tests_are_not_verified(self):
        for cmd, output, code in [('echo unittest', 'Ran 2 tests', 0),
                                  ('python -m unittest', 'Ran 0 tests', 0),
                                  ('python -m unittest', 'Ran 2 tests', 1),
                                  ('python -m unittest; echo OK', 'Ran 2 tests', 0)]:
            self.assertIsNone(sandbox.test_evidence({'type': 'command_execution', 'command': cmd,
                               'aggregated_output': output, 'exit_code': code}))

    def test_native_cli_publisher_must_be_verified(self):
        binary = self.root / 'codex.exe'
        binary.write_bytes(b'local test fixture')
        with patch('shutil.which', return_value=str(binary)), patch.object(sandbox, 'run_process', return_value=subprocess.CompletedProcess([], 1, 'BAD_SIGNATURE')):
            with self.assertRaisesRegex(ValueError, 'signature'):
                # setUp substitutes verified_cli; call the original function.
                self.patches[2].stop()
                sandbox.verified_cli(self.runtime.context())
        self.patches[2].start()

    def test_inherited_project_hook_configuration_is_refused(self):
        (self.project / '.codex').mkdir()
        with self.assertRaisesRegex(ValueError, 'Inherited'):
            sandbox.check_config_ancestors(self.project / 'workspace')

    def test_crash_recovery_marks_attention_without_restarting_codex(self):
        job = self.prepare()
        self.runtime.update('job', job['id'], {'state': 'queued', 'start_consumed': True})
        self.runtime.job_context(job['id'])
        with patch.object(task_manager, '_scheduler', None), patch.object(task_manager.threading, 'Thread') as thread, patch.object(sandbox, 'stream') as launch:
            task_manager.start_scheduler(self.runtime)
        self.assertEqual(tasks.task(job['id'], self.runtime)['state'], 'needs_attention')
        launch.assert_not_called()

    def test_plugin_discovery_preserves_the_single_task_worker_pool(self):
        import importlib
        from core.plugin_loader import discover_plugins
        before = importlib.import_module('plugins.task_manager')
        registry = discover_plugins(Path(__file__).resolve().parents[1] / 'plugins', set(), logger=lambda _: None)
        after = importlib.import_module('plugins.task_manager')
        self.assertIs(before, after)
        self.assertIs(before._workers, after._workers)
        self.assertTrue(registry.has('codex_assistant'))


class StreamTests(Isolated):
    def setUp(self):
        super().setUp()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.temp = self.root / 'temp'
        self.temp.mkdir()

    def stream(self, code, event, timeout=2):
        return sandbox.stream([sys.executable, '-u', '-c', code], 'local test only', self.workspace,
                              self.temp, self.runtime.context(), self.runtime, event, timeout=timeout)

    def test_actual_local_subprocess_stream_is_incremental_without_model_access(self):
        events = []
        result = self.stream('import json,time; print(json.dumps({"type":"turn.started"}),flush=True); time.sleep(.1); print(json.dumps({"type":"turn.completed"}),flush=True)', events.append)
        self.assertTrue(result['turn_completed'])
        self.assertEqual([e['type'] for e in events], ['turn.started', 'turn.completed'])

    def test_timeout_terminates_only_the_spawned_local_process(self):
        with self.assertRaises(TimeoutError):
            self.stream('import time; time.sleep(10)', lambda _: None, timeout=.08)

    def test_cancel_while_process_running_stops_without_replay(self):
        def cancel(_):
            self.runtime.cancel()
        with self.assertRaises(InterruptedError):
            self.stream('import json,time; print(json.dumps({"type":"turn.started"}),flush=True); time.sleep(10)', cancel)

    def test_truncated_json_stream_is_not_verified(self):
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.stream('print("{truncated",end="",flush=True)', lambda _: None)

    def test_oversized_event_stops_transport(self):
        with self.assertRaisesRegex(ValueError, 'safe size'):
            self.stream('print("x"*300000,end="",flush=True)', lambda _: None)


class NotificationTests(Isolated):
    def test_local_stt_execution_request_never_needs_a_planning_call(self):
        from providers.engine_pipeline import PipelineEngine
        async def exercise():
            model = Mock(caps={'tools': True, 'tool_format': 'openai', 'max_tools': 1})
            model.chat.side_effect = AssertionError('Exact supported coding request needs no planning model')
            engine = PipelineEngine(model, config=SimpleNamespace(system_instruction='',
                tools=[{'function_declarations': [codex_assistant.PLUGIN]}]), fast_commands=True)
            engine._runtime = self.runtime
            request = 'ask Codex to explain core/command_router.py'
            context = self.runtime.new_turn(request)
            engine._history = [{'role': 'user', 'content': request}]
            await engine._generate(context=context)
            self.assertEqual(list(engine._awaiting.values()), ['codex_assistant'])
            model.chat.assert_not_called()
            await engine.close()
        asyncio.run(exercise())

    def test_native_desktop_signal_uses_installed_qt_not_a_cloud_service(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'ui.py').read_text(encoding='utf-8'))
        method = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == '_desktop_notice')
        tray = Mock()
        factory = Mock(return_value=tray)
        factory.isSystemTrayAvailable.return_value = True
        factory.supportsMessages.return_value = True
        host = SimpleNamespace(_notice_tray=None, style=Mock(return_value=Mock()), _log_sig=Mock())
        env = dict(QSystemTrayIcon=factory, QStyle=Mock())
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'desktop-ui', 'exec'), env)
        env['_desktop_notice'](host, 'Task complete', 'Proposal ready; not applied.')
        tray.showMessage.assert_called_once()
        self.assertIn('not applied', tray.showMessage.call_args.args[1])

    def test_task_dashboard_is_read_only_and_uses_local_job_receipts(self):
        from PyQt6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton
        app = QApplication.instance() or QApplication([])
        self.runtime.put('job', 'demo', {'kind': 'codex', 'state': 'review_required', 'result_path': 'D:/results/result.txt'})
        source = ast.parse((Path(__file__).resolve().parents[1] / 'ui.py').read_text(encoding='utf-8'))
        method = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == '_show_tasks')
        env = dict(QDialog=QDialog, QVBoxLayout=QVBoxLayout, QLabel=QLabel, QTextEdit=QTextEdit,
                   QPushButton=QPushButton, C=SimpleNamespace(DARK='#001122', TEXT='#eeeeee'))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'task-ui', 'exec'), env)
        host = QWidget()
        def inspect(dialog):
            display = dialog.findChild(QTextEdit, 'task_alerts_status')
            self.assertTrue(display.isReadOnly())
            self.assertIn('review_required', display.toPlainText())
            return 0
        with patch.object(QDialog, 'exec', inspect):
            env['_show_tasks'](host)
        host.close()

    def test_offline_announcement_can_be_interrupted_without_model_or_audio(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8'))
        method = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == '_announce_local')
        host = SimpleNamespace(_awake=True, ui=SimpleNamespace(muted=False), _speaking_lock=threading.Lock(), _is_speaking=False)
        host.set_speaking = lambda value: setattr(host, '_is_speaking', value)
        voice = Mock()
        voice.Status.RunningState = 2
        voice.Speak.side_effect = lambda *args: host._local_speech_cancel.set()
        env = dict(threading=threading, time=time)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'offline-speech', 'exec'), env)
        import win32com.client
        with patch.object(win32com.client, 'Dispatch', return_value=voice), patch.dict(sys.modules, {'pythoncom': Mock()}):
            result = env['_announce_local'](host, 'Task completed. Results: D:/results')
        self.assertFalse(result)
        self.assertFalse(host._is_speaking)
        self.assertEqual(voice.Speak.call_args.args, ('', 3))

    def test_free_routes_and_verified_results_only(self):
        self.runtime.dashboard_notify = Mock()
        self.runtime.local_speech = Mock(return_value=True)
        self.runtime.desktop_notify = Mock(return_value='queued_to_ui; visibility_not_verified')
        routes = {'routes_json': '{"completed":["dashboard","desktop","speech"]}'}
        with patch.object(alerts, 'get_plugin_config', return_value=routes):
            result = alerts.emit('job', 'completed', 'Verified execution; proposal not applied.', verified=True,
                                 result_path='D:/results/result.txt', rt=self.runtime)
        self.assertTrue(result['verified'])
        self.assertIn('Results:', result['message'])
        self.assertEqual(result['channels']['speech'], 'announced_locally')
        self.assertIn('visibility_not_verified', result['channels']['desktop'])

    def test_uncertain_completion_does_not_announce_success(self):
        self.runtime.dashboard_notify = Mock()
        with patch.object(alerts, 'get_plugin_config', return_value={}):
            result = alerts.emit('job', 'completed', 'Unknown outcome', rt=self.runtime)
        self.assertEqual(result['event'], 'needs_attention')
        self.assertFalse(result['verified'])

    def test_duplicate_notifications_and_unavailable_speech_do_not_repeat(self):
        self.runtime.local_speech = Mock(return_value=False)
        with patch.object(alerts, 'get_plugin_config', return_value={'routes_json': '{"completed":["speech"]}'}):
            first = alerts.emit('job', 'completed', 'Done', verified=True, rt=self.runtime)
            second = alerts.emit('job', 'completed', 'Done', verified=True, rt=self.runtime)
        self.runtime.local_speech.assert_called_once()
        self.assertEqual(first, second)
        self.assertEqual(first['channels']['speech'], 'deferred_or_unavailable')

    def test_invalid_paid_messaging_channel_is_rejected(self):
        with patch.object(alerts, 'get_plugin_config', return_value={'routes_json': '{"completed":["phone"]}'}):
            with self.assertRaises(ValueError):
                alerts.preferences()
            result = alerts.emit('job', 'completed', 'Done', verified=True, rt=self.runtime)
        self.assertIn('route_error', result)
        self.assertNotIn('phone', result['channels'])

    def test_failed_notification_is_specific_not_fake_delivery(self):
        self.runtime.desktop_notify = Mock(side_effect=OSError('No notification service'))
        with patch.object(alerts, 'get_plugin_config', return_value={'routes_json': '{"failed":["desktop"]}'}):
            result = alerts.emit('job', 'failed', 'Task failed', rt=self.runtime)
        self.assertEqual(result['channels']['desktop'], 'failed:OSError')

    def test_same_account_whatsapp_call_is_rejected_before_window_search(self):
        for contact in ('me', 'my phone', 'Name (You)'):
            context = self.runtime.new_turn('voice call ' + contact + ' on WhatsApp')
            with patch.object(whatsapp_control, '_ensure_window') as window:
                result = whatsapp_control.run({'action': 'voice_call', 'contact': contact}, session_memory=context)
            self.assertIn('Cannot call this WhatsApp account from itself', result)
            window.assert_not_called()
