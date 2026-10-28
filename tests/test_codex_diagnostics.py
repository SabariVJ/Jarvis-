"""Real local Python subprocess fixtures only. Never invokes Codex inference."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
from test_automation import Isolated
import test_phase51_execution as phase51
from core import codex_diagnostics as diag, codex_sandbox as sandbox, codex_tasks as tasks
from plugins import codex_assistant
from plugins._automation_common import report


class DiagnosticStreamTests(Isolated):
    def setUp(self):
        super().setUp()
        self.workspace = self.root / 'workspace'
        self.temp = self.root / 'temp'
        self.workspace.mkdir()
        self.temp.mkdir()

    def run_stream(self, code, callback=None, timeout=2):
        return sandbox.stream([sys.executable, '-u', '-c', code], 'fixture', self.workspace,
            self.temp, self.runtime.context(), self.runtime, callback or (lambda _: None), timeout)

    def json_code(self, events, exit_code=0):
        return 'import json,sys; ' + '; '.join('print(json.dumps(' + repr(e) + '),flush=True)' for e in events) + '; sys.exit(' + str(exit_code) + ')'

    def completed(self):
        return [{'type': 'turn.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Read-only explanation.'}}, {'type': 'turn.completed'}]

    def test_nonzero_before_completion_keeps_exact_code_and_stderr(self):
        result = self.run_stream('import sys; print("fatal example",file=sys.stderr); sys.exit(17)')
        self.assertEqual(result['returncode'], 17)
        self.assertEqual(result['diagnostics']['stderr'].strip(), 'fatal example')
        self.assertFalse(result['completion_verified'])
        self.assertEqual(result['diagnostics']['event_types'], {})

    def test_zero_without_completion_not_success(self):
        result = self.run_stream('print("{\\\"type\\\":\\\"turn.started\\\"}")')
        self.assertFalse(result['completion_verified'])
        self.assertEqual(result['diagnostics']['failure_category'], 'missing_completion')

    def test_completed_without_result_message_not_success(self):
        result = self.run_stream(self.json_code([{'type': 'turn.completed'}]))
        self.assertFalse(result['completion_verified'])

    def test_completed_with_nonzero_still_failure(self):
        result = self.run_stream(self.json_code(self.completed(), 3))
        self.assertTrue(result['turn_completed'])
        self.assertFalse(result['completion_verified'])
        self.assertEqual(result['returncode'], 3)

    def test_malformed_line_then_completion_fails_closed(self):
        result = self.run_stream('print("not JSON"); ' + self.json_code(self.completed()))
        self.assertFalse(result['completion_verified'])
        self.assertEqual(result['diagnostics']['malformed_lines'], 1)

    def test_truncated_last_event_retains_exit_and_last_valid(self):
        with self.assertRaises(ValueError) as caught:
            self.run_stream('print("{\\\"type\\\":\\\"turn.started\\\"}"); print("{bad",end="",flush=True)')
        evidence = caught.exception.diagnostics
        self.assertEqual(evidence['returncode'], 0)
        self.assertEqual(evidence['failure_category'], 'malformed_jsonl')
        self.assertEqual(evidence['last_valid_event']['type'], 'turn.started')

    def test_valid_final_json_without_newline_accepted(self):
        code = self.json_code(self.completed()[:-1]).rsplit('; sys.exit', 1)[0]
        result = self.run_stream(code + '; print("{\\\"type\\\":\\\"turn.completed\\\"}",end="",flush=True)')
        self.assertTrue(result['completion_verified'])

    def test_cli_argument_rejection_and_config_classification(self):
        for message in ["error: unexpected argument '--unsupported'", 'Error loading config: unknown field bad_policy']:
            result = self.run_stream('import sys; print(' + repr(message) + ',file=sys.stderr); sys.exit(2)')
            self.assertEqual(result['diagnostics']['failure_category'], 'cli_configuration')
            self.assertIn(message, diag.summary(result['diagnostics']))

    def test_windows_sandbox_failure_is_not_auth_failure(self):
        message = 'Windows sandbox initialization failed: native MXC unavailable'
        result = self.run_stream('import sys; print(' + repr(message) + ',file=sys.stderr); sys.exit(1)')
        self.assertEqual(result['diagnostics']['failure_category'], 'windows_sandbox')
        self.assertFalse(result['completion_verified'])

    def test_auth_quota_and_network_failure_events(self):
        for message, expected in [('authentication failed 401', 'authentication'), ('usage_limit_reached', 'usage_limit'), ('network connection refused', 'network')]:
            result = self.run_stream(self.json_code([{'type': 'turn.failed', 'error': {'message': message}}], 1))
            evidence = result['diagnostics']
            self.assertEqual(evidence['failure_category'], expected)
            self.assertIn(message, evidence['last_valid_event']['message'])
            self.assertFalse(result['completion_verified'])

    def test_interruption_is_terminal_even_if_completion_follows(self):
        result = self.run_stream(self.json_code([{'type': 'turn.interrupted'}, *self.completed()]))
        self.assertEqual(result['diagnostics']['failure_category'], 'interrupted')
        self.assertFalse(result['completion_verified'])

    def test_error_then_completed_is_failure(self):
        result = self.run_stream(self.json_code([{'type': 'error', 'message': 'fatal error'}, *self.completed()]))
        self.assertFalse(result['completion_verified'])

    def test_process_launch_failure_has_no_return_code(self):
        with patch.object(sandbox.subprocess, 'Popen', side_effect=OSError('executable unavailable')):
            with self.assertRaises(OSError) as caught:
                self.run_stream('pass')
        self.assertFalse(caught.exception.diagnostics['launched'])
        self.assertIsNone(caught.exception.diagnostics['returncode'])
        self.assertEqual(caught.exception.diagnostics['failure_category'], 'process_launch')

    def test_process_job_isolation_failure_is_separate(self):
        with patch.object(sandbox, 'OwnedWindowsJob', side_effect=OSError('job isolation unavailable')):
            with self.assertRaises(OSError) as caught:
                self.run_stream('import time; time.sleep(5)')
        self.assertEqual(caught.exception.diagnostics['failure_category'], 'process_isolation')
        self.assertTrue(caught.exception.diagnostics['launched'])

    def test_timeout_preserves_termination_code(self):
        with self.assertRaises(TimeoutError) as caught:
            self.run_stream('import time; time.sleep(5)', timeout=.1)
        self.assertEqual(caught.exception.diagnostics['failure_category'], 'timeout')
        self.assertIsInstance(caught.exception.diagnostics['returncode'], int)

    def test_cancel_preserves_termination_code(self):
        with self.assertRaises(InterruptedError) as caught:
            self.run_stream('import time; print("{\\\"type\\\":\\\"turn.started\\\"}",flush=True); time.sleep(5)', callback=lambda _: self.runtime.cancel())
        self.assertEqual(caught.exception.diagnostics['failure_category'], 'cancelled')
        self.assertIsInstance(caught.exception.diagnostics['returncode'], int)

    def test_valid_result_records_types_not_private_items(self):
        result = self.run_stream(self.json_code(self.completed()))
        self.assertTrue(result['completion_verified'])
        self.assertEqual(result['diagnostics']['event_types']['turn.completed'], 1)
        self.assertNotIn('Read-only explanation', json.dumps(result['diagnostics']))
        self.assertEqual(result['diagnostics']['last_valid_event'], {'type': 'turn.completed'})

    def test_secrets_redacted_in_stderr(self):
        sample_value = 'private-example-value-12345'
        message = '\n'.join(['Authorization: Bearer ' + sample_value,
            json.dumps({'access_token': sample_value, 'password': sample_value}),
            'https://user:' + sample_value + '@example.test/?' + 'token' + '=' + sample_value])
        # Keep secrets in stdin in production. Here the fixture subprocess only prints to stderr.
        result = self.run_stream('import sys; print(' + repr(message) + ',file=sys.stderr); sys.exit(1)')
        self.assertNotIn(sample_value, json.dumps(result['diagnostics']))
        self.assertIn('REDACTED', result['diagnostics']['stderr'])

    def test_diagnostic_only_stream_never_success(self):
        for code in (0, 7):
            result = self.run_stream(self.json_code([{'type': 'diagnostic', 'message': 'startup warning'}], code))
            self.assertFalse(result['completion_verified'])
            self.assertEqual(result['returncode'], code)

    def test_stderr_bounded_and_initial_error_preserved(self):
        result = self.run_stream('import sys; print("unexpected argument --bad\\n" + "x"*20000 + "\\ntail",file=sys.stderr); sys.exit(2)')
        evidence = result['diagnostics']
        self.assertTrue(evidence['stderr_truncated'])
        self.assertIn('unexpected argument', evidence['stderr'])
        self.assertLess(len(evidence['stderr']), 4200)

    def test_metadata_never_stores_session_identifiers_or_unknown_text(self):
        sample_value = 'private-session-test-12345'
        data = diag.event_metadata({'type': sample_value, 'thread_id': sample_value, 'item': {'type': sample_value, 'status': sample_value, 'text': sample_value}})
        self.assertNotIn(sample_value, json.dumps(data))
        self.assertNotIn(sample_value, diag.sanitize('session_id=' + sample_value))

    def test_explicit_read_only_scope_keeps_source_unwritable(self):
        args = sandbox.cli_command('codex.exe', self.workspace, self.temp, 'not logged', read_only=True)
        profile = next(x for x in args if x.startswith('permissions.jarvis.filesystem='))
        self.assertIn(json.dumps(str(self.workspace)) + '="read"', profile)
        self.assertIn(json.dumps(str(self.temp)) + '="write"', profile)
        self.assertIn('permissions.jarvis.network.enabled=false', args)


class DiagnosticWorkerTests(Isolated):
    def fixture(self):
        fixture = phase51.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def test_failed_worker_saves_sanitized_events_and_diagnostics(self):
        fixture = self.fixture()
        job, runner = fixture.complete(failure='authentication failed 401; access_token=very-private-test-token')
        self.assertEqual(job['state'], 'needs_attention')
        self.assertIn('authentication', job['diagnostic_summary'])
        saved = Path(job['diagnostic_path']).read_text()
        events = (Path(job['diagnostic_path']).parent / 'events.json').read_text()
        self.assertNotIn('very-private-test-token', saved + events + job['error'])
        self.assertIn('authentication failed', events)
        self.assertIn('exit=1', job['error'])
        self.assertEqual(job['cli_result']['returncode'], 1)
        runner.assert_called_once()

    def test_status_and_narration_preserve_failure(self):
        fixture = self.fixture()
        job, _ = fixture.complete(failure='usage_limit_reached')
        status = tasks.task_status(job['id'], fixture.runtime)
        text = codex_assistant.summarize(report('codex_assistant', status))
        self.assertIn('usage_limit', text)
        self.assertIn('diagnostics.json', text)

    def test_legacy_status_identifies_missing_evidence_without_relaunch(self):
        fixture = self.fixture()
        job, _ = fixture.complete(no_turn=True)
        fixture.runtime.update('job', job['id'], {'execution_diagnostics': None, 'cli_result': None})
        before = fixture.runtime.get('job', job['id'])
        status = tasks.task_status(job['id'], fixture.runtime)
        self.assertIn('did not retain', status['diagnostic_summary'])
        self.assertEqual(before, fixture.runtime.get('job', job['id']))

    def test_success_still_requires_final_message_and_completion(self):
        fixture = self.fixture()
        job, _ = fixture.complete(change=False)
        self.assertEqual(job['state'], 'completed')
        self.assertTrue(job['execution_diagnostics']['completion_verified'])

    def test_worker_exception_preserves_start_failure_evidence(self):
        fixture = self.fixture()
        error = OSError('start failed')
        error.diagnostics = {'phase': 'process_launch', 'failure_category': 'process_launch', 'returncode': None, 'launched': False}
        job, _ = fixture.complete(failure=error)
        self.assertFalse(job['execution_diagnostics']['launched'])
        self.assertIn('process_launch', job['diagnostic_summary'])
