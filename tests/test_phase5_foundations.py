"""All telephone transport mocked. Never dial or run paid model inference."""
import asyncio
import ast
import json
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import calling_manager as manager, codex_bridge
from core.command_router import resolve
from plugins import calling_manager, codex_assistant, task_manager
from test_automation import Isolated


class FoundationTests(Isolated):
    def setUp(self):
        super().setUp()
        self.text = 'call Appa on phone and say "The task is ready."'
        self.args = manager.command(self.text)
        self.config = {'provider': 'twilio', 'outgoing_enabled': True, 'costs_and_consent_approved': True,
                       'caller_id': '+12025550101', 'contacts': [
                           {'name': 'Appa', 'number': '+12025550102', 'authorized': True},
                           {'name': 'Owner', 'number': '+12025550103', 'authorized': True, 'owner': True}]}
        self.backend = Mock()
        self.backend.create.return_value = {'sid': 'CA' + 'a' * 32, 'state': 'queued'}
        self.context = self.runtime.new_turn(self.text)

    def notify(self, context=None):
        return manager.notify(self.args, context or self.context, self.runtime, self.config, self.backend)

    def test_exact_saved_name_owner_and_explicit_number(self):
        self.assertEqual(manager.resolve_recipient('appa', self.config), '+12025550102')
        self.assertEqual(manager.resolve_recipient('my phone', self.config), '+12025550103')
        self.assertEqual(manager.resolve_recipient('+12025550102', self.config), '+12025550102')
        for value in ('Unknown', 'App', '+12025550199', '911', '2025550102'):
            with self.assertRaises(ValueError):
                manager.resolve_recipient(value, self.config)

    def test_ambiguous_and_unauthorized_contacts_fail_closed(self):
        self.config['contacts'].append(dict(self.config['contacts'][0]))
        with self.assertRaises(ValueError):
            self.notify()
        self.config['contacts'].pop()
        self.config['contacts'][0]['authorized'] = False
        with self.assertRaises(ValueError):
            self.notify()
        self.backend.create.assert_not_called()

    def test_requested_is_not_connected_and_no_extra_confirmation(self):
        result = self.notify()
        self.assertEqual(result['state'], 'queued')
        self.assertTrue(result['verified'])
        self.backend.verify_caller.assert_called_once_with('+12025550101')
        self.backend.create.assert_called_once_with('+12025550102', '+12025550101', 'The task is ready.')

    def test_cost_approval_and_provider_verification_required(self):
        self.config['costs_and_consent_approved'] = False
        with self.assertRaises(ValueError):
            self.notify()
        self.config['costs_and_consent_approved'] = True
        self.backend.verify_caller.side_effect = ValueError('Caller ID not verified')
        with self.assertRaises(ValueError):
            self.notify()
        self.backend.create.assert_not_called()

    def test_same_turn_duplicates_and_persistent_global_cooldown(self):
        self.notify()
        with self.assertRaises(ValueError):
            self.notify()
        self.context = self.runtime.new_turn(self.text)
        with self.assertRaises(ValueError):
            self.notify()
        self.backend.create.assert_called_once()

    def test_whatsapp_and_phone_share_the_existing_global_cooldown(self):
        self.runtime.new_turn('voice call Ganesh on WhatsApp')
        self.assertIsNone(self.runtime.reserve_call('Ganesh', self.runtime.context()))
        self.context = self.runtime.new_turn(self.text)
        with self.assertRaisesRegex(ValueError, 'cooldown'):
            self.notify()
        self.backend.create.assert_not_called()

    def test_uncertain_network_is_reserved_and_never_retried(self):
        self.backend.create.side_effect = TimeoutError('sensitive transport details')
        result = self.notify()
        self.assertEqual(result['state'], 'uncertain')
        self.assertNotIn('sensitive', result['failure'])
        with self.assertRaises(ValueError):
            self.notify()
        with self.assertRaises(ValueError):
            manager.refresh(result['id'], self.runtime, self.config, self.backend)
        self.backend.create.assert_called_once()

    def test_cancelled_and_completed_calls_cannot_dispatch(self):
        self.runtime.cancel()
        with self.assertRaises(InterruptedError):
            self.notify()
        self.context = self.runtime.new_turn(self.text)
        self.runtime.finish(self.context)
        with self.assertRaises(InterruptedError):
            self.notify()
        self.backend.create.assert_not_called()

    def test_delayed_provider_preflight_cancellation_prevents_call(self):
        self.backend.verify_caller.side_effect = lambda _: self.runtime.cancel()
        with self.assertRaises(InterruptedError):
            self.notify()
        self.backend.create.assert_not_called()

    def test_new_turn_rejects_old_model_call_or_changed_message(self):
        self.runtime.new_turn('show calling status')
        with self.assertRaises(InterruptedError):
            self.notify()
        context = self.runtime.new_turn(self.text)
        changed = {**self.args, 'message': 'not requested'}
        with self.assertRaises(ValueError):
            manager.notify(changed, context, self.runtime, self.config, self.backend)
        self.backend.create.assert_not_called()

    def test_jobs_schedules_and_general_delegation_never_authorize_calls(self):
        with self.assertRaises((ValueError, InterruptedError)):
            self.notify({**self.context, 'lifecycle': 'job'})
        for tool, action in [('calling_manager', 'notify'), ('codex_assistant', 'plan')]:
            with self.assertRaises(ValueError):
                task_manager.validate_steps([{'tool': tool, 'arguments_json': json.dumps({'action': action})}])
        self.assertIsNone(manager.command('call me when the coding task finishes'))
        self.backend.create.assert_not_called()

    def test_reconnect_never_replays_phone_attempt(self):
        self.notify()
        from core.automation_runtime import AutomationRuntime
        restored = AutomationRuntime(self.runtime.path)
        with self.assertRaises(InterruptedError):
            manager.notify(self.args, self.context, restored, self.config, self.backend)
        self.backend.create.assert_called_once()

    def test_refresh_reads_known_sid_and_reports_actual_provider_state(self):
        result = self.notify()
        self.backend.fetch.return_value = {'sid': result['sid'], 'state': 'busy', 'duration': '0'}
        updated = manager.refresh(result['id'], self.runtime, self.config, self.backend)
        self.assertEqual(updated['state'], 'busy')
        self.assertEqual(updated['duration'], '0')
        self.backend.create.assert_called_once()

    def test_authentication_failure_redacts_provider_error(self):
        error = RuntimeError('credentials that must not leak')
        error.status = 401
        self.assertIn('HTTP 401', manager.safe_failure(error))
        self.assertNotIn('credentials that', manager.safe_failure(error))

    def test_status_is_local_and_accurately_disabled(self):
        with patch.object(manager, 'provider', side_effect=AssertionError('No network')):
            result = manager.snapshot(self.runtime, {})
        self.assertEqual(result['remote_access'], 'not_checked')
        self.assertFalse(result['incoming_enabled'])
        self.assertFalse(result['recording_enabled'])
        self.assertFalse(result['proactive_calls_enabled'])
        self.assertEqual(result['scheduled_calls'], [])

    def test_unsigned_incoming_does_not_reach_model(self):
        with self.assertRaises(ValueError):
            manager.validated_webhook('http://bad', {}, '', 'https://example.com/incoming', 'x')

    def test_official_sdk_adapter_has_no_retry_or_recording_and_discloses_ai(self):
        client = Mock()
        client.incoming_phone_numbers.list.return_value = [SimpleNamespace(phone_number='+12025550101')]
        client.calls.create.return_value = SimpleNamespace(sid='CA' + 'a' * 32, status='queued')
        http = Mock()
        factory = Mock(return_value=http)
        modules = {'twilio.rest': SimpleNamespace(Client=Mock(return_value=client)),
                   'twilio.http.http_client': SimpleNamespace(TwilioHttpClient=factory)}
        with patch.dict('sys.modules', modules), patch.dict('os.environ', {
                'JARVIS_TWILIO_ACCOUNT_SID': 'AC' + 'a' * 32, 'JARVIS_TWILIO_AUTH_TOKEN': 'fake'}):
            backend = manager.TwilioProvider()
            backend.verify_caller('+12025550101')
            result = backend.create('+12025550102', '+12025550101', '<private>')
        factory.assert_called_once_with(timeout=10, max_retries=0)
        self.assertFalse(http.session.trust_env)
        self.assertEqual(result['state'], 'queued')
        args = client.calls.create.call_args.kwargs
        self.assertFalse(args['record'])
        self.assertIn('an AI assistant', args['twiml'])
        self.assertIn('&lt;private&gt;', args['twiml'])
        self.assertNotIn('<Record', args['twiml'])

    def test_signed_incoming_stays_rejected_and_bad_signature_fails(self):
        validator = Mock()
        validator.validate.return_value = True
        with patch.dict('sys.modules', {'twilio.request_validator': SimpleNamespace(RequestValidator=Mock(return_value=validator))}), patch.dict('os.environ', {'JARVIS_TWILIO_AUTH_TOKEN': 'fake'}):
            url = 'https://example.com/incoming'
            form = {'AccountSid': 'expected', 'NewFutureField': 'value'}
            result = manager.validated_webhook(url, form, 'signed', url, 'expected')
            self.assertIn('<Reject', result['response'])
            self.assertFalse(result['incoming_enabled'])
            validator.validate.assert_called_once_with(url, form, 'signed')
            validator.validate.return_value = False
            with self.assertRaises(ValueError):
                manager.validated_webhook(url, form, 'bad', url, 'expected')

    def test_owner_number_requires_provider_verification(self):
        text = 'call my phone on phone and say "Ready"'
        args = manager.command(text)
        context = self.runtime.new_turn(text)
        manager.notify(args, context, self.runtime, self.config, self.backend)
        self.assertEqual(self.backend.verify_caller.call_count, 2)
        self.backend.verify_caller.assert_any_call('+12025550103')

    def test_concurrent_duplicate_transport_dispatches_once(self):
        def attempt(_):
            try:
                return self.notify()['state']
            except ValueError:
                return 'blocked'
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(results), ['blocked', 'queued'])
        self.backend.create.assert_called_once()

    def test_malformed_provider_acknowledgement_is_uncertain_not_retried(self):
        self.backend.create.return_value = {'sid': 'wrong', 'state': 'connected'}
        self.assertEqual(self.notify()['state'], 'uncertain')
        self.backend.create.assert_called_once()

    def test_repeated_tool_receipts_do_not_dispatch_phone_twice(self):
        invoke = Mock(side_effect=lambda: calling_manager.report('calling_manager', self.notify()))
        for _ in range(2):
            self.runtime.execute('calling_manager', self.args, invoke, self.context)
        invoke.assert_called_once()
        self.backend.create.assert_called_once()

    def test_enabled_tools_determine_routing_and_whatsapp_stays_separate(self):
        from plugins import whatsapp_control
        declarations = [calling_manager.PLUGIN, codex_assistant.PLUGIN, whatsapp_control.PLUGIN]
        for text, name in [('show calling status', 'calling_manager'),
                           ('are you connected to Codex?', 'codex_assistant'), (self.text, 'calling_manager'),
                           ('video call Ganesh on WhatsApp', 'whatsapp_control')]:
            self.assertEqual(resolve(text, declarations)[0][0], name)
            self.assertIsNone(resolve(text, []))
        self.assertIsNone(resolve('call Appa', declarations))

    def test_missing_cli_and_failed_login_report_specific_state(self):
        with patch.object(codex_bridge.shutil, 'which', return_value=None):
            self.assertFalse(codex_bridge.diagnostic()['installed'])
        with patch.object(codex_bridge.shutil, 'which', return_value='C:/bin/codex.exe'), patch.object(
                codex_bridge, 'run_process', side_effect=[
                    subprocess.CompletedProcess([], 0, 'codex-cli 1.2.3'),
                    subprocess.CompletedProcess([], 1, 'Not logged in')]):
            result = codex_bridge.diagnostic(self.context)
        self.assertFalse(result['authenticated'])
        self.assertEqual(result['remote_inference'], 'not_checked')

    def test_codex_login_not_inference_or_unlimited_api(self):
        with patch.object(codex_bridge.shutil, 'which', return_value='C:/bin/codex.exe'), patch.object(
                codex_bridge, 'run_process', side_effect=[
                    subprocess.CompletedProcess([], 0, 'codex-cli 1.2.3'),
                    subprocess.CompletedProcess([], 0, 'Logged in using ChatGPT')]):
            result = codex_bridge.diagnostic(self.context)
        self.assertTrue(result['authenticated'])
        self.assertEqual(result['authentication_mode'], 'ChatGPT')
        self.assertEqual(result['quota'], 'unknown')
        self.assertEqual(result['remote_inference'], 'not_checked')
        self.assertNotIn('Logged in', json.dumps(result))

    def test_codex_project_scope_plan_does_not_execute(self):
        with patch.object(codex_bridge, 'run_process', side_effect=AssertionError('No coding execution')):
            result = codex_bridge.review_plan(str(codex_bridge.PROJECT), 'Review tests')
            self.assertFalse(result['executed'])
            self.assertFalse(result['publishing_allowed'])
            with self.assertRaises(ValueError):
                codex_bridge.review_plan(str(self.root), 'Review elsewhere')

    def test_no_model_call_for_status_and_truthful_failure_narration(self):
        from providers.engine_pipeline import PipelineEngine
        async def exercise():
            for plugin, text in [(calling_manager, 'show calling status'), (codex_assistant, 'show codex status')]:
                model = Mock(caps={'tools': True, 'tool_format': 'openai', 'max_tools': 1})
                model.chat.side_effect = AssertionError('No model planning needed')
                engine = PipelineEngine(model, config=SimpleNamespace(system_instruction='',
                    tools=[{'function_declarations': [plugin.PLUGIN]}]), fast_commands=True)
                self.runtime.catalog_provider = lambda: [plugin.PLUGIN]
                engine._runtime = self.runtime
                context = self.runtime.new_turn(text)
                engine._history = [{'role': 'user', 'content': text}]
                await engine._generate(context=context)
                self.assertEqual(list(engine._awaiting.values()), [plugin.PLUGIN['name']])
                engine._history = [{'role': 'tool', 'name': plugin.PLUGIN['name'],
                                    'content': 'status=failed tool=test reason=auth_denied'}]
                engine._emit = Mock(side_effect=lambda *a: asyncio.sleep(0))
                engine._speak = Mock(side_effect=lambda *a: asyncio.sleep(0))
                await engine._generate(context=context)
                self.assertIn('auth_denied', engine._history[-1]['content'])
                model.chat.assert_not_called()
                await engine.close()
        asyncio.run(exercise())

    def test_calling_panel_is_read_only_and_refresh_does_not_call_provider(self):
        from PyQt6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton
        app = QApplication.instance() or QApplication([])
        source = ast.parse((Path(__file__).resolve().parents[1] / 'ui.py').read_text(encoding='utf-8'))
        method = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == '_show_calling')
        env = dict(QDialog=QDialog, QVBoxLayout=QVBoxLayout, QLabel=QLabel, QTextEdit=QTextEdit,
                   QPushButton=QPushButton, C=SimpleNamespace(DARK='#001122', TEXT='#eeeeee'))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'calling-ui', 'exec'), env)
        host = QWidget()
        def inspect(dialog):
            display = dialog.findChild(QTextEdit, 'calling_status')
            self.assertTrue(display.isReadOnly())
            self.assertIn('unconfigured', display.toPlainText())
            dialog.findChild(QPushButton, 'calling_refresh').click()
            return 0
        with patch.object(manager, 'configuration', return_value={}), patch.object(manager, 'provider', side_effect=AssertionError('No network')), patch.object(QDialog, 'exec', inspect):
            env['_show_calling'](host)
        host.close()
