"""Offline compatibility/authorization tests; no inference or owner file probes."""
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_automation import Isolated
import test_phase51_execution as phase51
from core import codex_preflight as preflight, codex_tasks as tasks, codex_sandbox as sandbox
from core.codex_commands import command
from core.command_router import resolve
from plugins import codex_assistant, task_manager
from plugins._automation_common import report

REAL_INSPECT = preflight.inspect


class PolicyTests(Isolated):
    def fixture(self):
        fixture = phase51.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        return fixture

    def help(self, args, **kwargs):
        if '--version' in args:
            output = 'codex-cli 0.163.0'
        elif 'sandbox' in args:
            output = '--permission-profile --include-managed-config'
        else:
            output = '--strict-config --ignore-user-config --ignore-rules --ephemeral --json'
        return SimpleNamespace(returncode=0, stdout=output)

    def inspect(self, fixture, job):
        with patch.object(preflight, 'run_process', side_effect=self.help), patch.object(preflight, 'host_build', return_value=(26200, 9711)), patch('core.mxc_backend.verify', return_value={'ready': False, 'error': 'Unverified fixture isolation'}):
            return REAL_INSPECT(job, fixture.runtime.context(), fixture.runtime)

    def test_elevated_root_deny_is_blocked_without_broadening(self):
        fixture = self.fixture()
        job = fixture.prepare('read-only explain plugins/demo.py')
        check = self.inspect(fixture, job)
        self.assertFalse(check['ready'])
        self.assertEqual(check['failure_category'], 'mxc_runtime_unverified')
        self.assertEqual(check['requested_policy']['root'], 'deny')
        self.assertEqual(check['requested_policy']['backend'], 'elevated')
        self.assertEqual(check['requested_policy']['source'], 'read')
        self.assertFalse(check['requested_policy']['network_enabled'])

    def test_supported_mxc_is_only_a_candidate(self):
        result = preflight.assess('codex-cli 0.163.0', (26200, 9711), '--permission-profile --include-managed-config')
        self.assertTrue(result['mxc_candidate'])
        self.assertFalse(result['backend_enabled'])
        self.assertFalse(result['isolation_verified'])

    def test_unsupported_windows_cannot_select_mxc(self):
        for build in [(26200, 9277), (26100, 9277), (19045, 9999), None]:
            self.assertFalse(preflight.assess('codex-cli 0.163.0', build, '--permission-profile --include-managed-config')['mxc_candidate'])

    def test_cli_features_not_inferred_from_os(self):
        for version, help_text in [('codex-cli 0.161.0', '--permission-profile --include-managed-config'), ('codex-cli 0.163.0', '--help')]:
            self.assertFalse(preflight.assess(version, (26200, 9711), help_text)['mxc_candidate'])

    def test_all_required_unknown_checks_fail_closed(self):
        fixture = self.fixture()
        check = self.inspect(fixture, fixture.prepare())
        for key in ('effective_os_filesystem_isolation', 'private_path_denial', 'source_write_restrictions',
                    'network_isolation', 'authentication_connectivity', 'native_cancellation'):
            self.assertFalse(check['checks'][key])
        self.assertFalse(check['ready'])

    def test_private_source_path_rejected(self):
        fixture = self.fixture()
        job = fixture.prepare()
        with patch.object(sandbox, 'source_bytes', side_effect=ValueError('Private/generated directory excluded')):
            check = self.inspect(fixture, job)
        self.assertFalse(check['ready'])
        self.assertIn('Private', check['error'])

    def test_changed_source_copy_rejected(self):
        fixture = self.fixture()
        job = fixture.prepare()
        with patch.object(sandbox, 'source_bytes', return_value=b'changed'):
            check = self.inspect(fixture, job)
        self.assertFalse(check['checks']['source_copy'])
        self.assertFalse(check['ready'])

    def test_read_only_inspection_does_not_mutate_job(self):
        fixture = self.fixture()
        job = fixture.prepare()
        before = fixture.runtime.get('job', job['id'])
        self.inspect(fixture, job)
        self.assertEqual(before, fixture.runtime.get('job', job['id']))

    def test_help_startup_failure_is_specific(self):
        fixture = self.fixture()
        job = fixture.prepare()
        with patch.object(preflight, 'run_process', side_effect=OSError('CLI help startup failed')):
            check = REAL_INSPECT(job, fixture.runtime.context(), fixture.runtime)
        self.assertEqual(check['failure_category'], 'preflight_failed')
        self.assertIn('startup failed', check['error'])

    def test_blocked_run_never_queues_consumes_or_submits(self):
        fixture = self.fixture()
        job = fixture.prepare()
        context = fixture.runtime.new_turn('run Codex task ' + job['id'])
        before = fixture.runtime.get('job', job['id'])
        with patch.object(preflight, 'inspect', side_effect=REAL_INSPECT), patch.object(preflight, 'run_process', side_effect=self.help), patch.object(preflight, 'host_build', return_value=(26200, 9711)), patch.object(task_manager, 'launch_supervised') as launch, patch.object(sandbox, 'stream') as inference:
            result = tasks.run(command(context['user_text']), context, fixture.runtime)
        self.assertEqual(result['state'], 'blocked')
        self.assertFalse(result['start_consumed'])
        self.assertEqual(before, fixture.runtime.get('job', job['id']))
        launch.assert_not_called()
        inference.assert_not_called()

    def test_no_replay_for_consumed_failed_task(self):
        fixture = self.fixture()
        job, _ = fixture.complete(failure='windows sandbox requires effective :root read access')
        context = fixture.runtime.new_turn('run Codex task ' + job['id'])
        with patch.object(preflight, 'inspect') as inspect, patch.object(task_manager, 'launch_supervised') as launch:
            result = tasks.run(command(context['user_text']), context, fixture.runtime)
        self.assertEqual(result['state'], 'needs_attention')
        inspect.assert_not_called()
        launch.assert_not_called()

    def test_stale_execution_authorization_still_blocks(self):
        fixture = self.fixture()
        job = fixture.prepare()
        context = fixture.runtime.new_turn('run Codex task ' + job['id'])
        fixture.runtime.new_turn('Open Chrome')
        with patch.object(preflight, 'inspect') as inspect:
            with self.assertRaises(InterruptedError):
                tasks.run(command(context['user_text']), context, fixture.runtime)
        inspect.assert_not_called()

    def test_terminal_evidence_corrects_stale_narration_without_mutation(self):
        fixture = self.fixture()
        job = fixture.prepare()
        diagnostic = {'returncode': 1, 'failure_category': 'windows_sandbox', 'stderr': 'elevated Windows sandbox requires effective :root read access'}
        fixture.runtime.update('job', job['id'], {'state': 'running', 'execution_diagnostics': diagnostic, 'diagnostic_path': 'task/diagnostics.json'})
        before = fixture.runtime.get('job', job['id'])
        view = tasks.task_status(job['id'], fixture.runtime)
        self.assertEqual(view['state'], 'needs_attention')
        text = codex_assistant.summarize(report('codex_assistant', view))
        self.assertIn(':root read access', text)
        self.assertIn('diagnostics.json', text)
        self.assertEqual(before, fixture.runtime.get('job', job['id']))

    def test_cached_queued_receipt_refreshes_actual_failed_state(self):
        fixture = self.fixture()
        job, _ = fixture.complete(failure='windows sandbox startup failed')
        queued = {**job, 'state': 'queued', 'error': None, 'message': 'queued', 'execution_diagnostics': None, 'cli_result': None}
        text = codex_assistant.summarize(report('codex_assistant', queued))
        self.assertIn('needs_attention', text)
        self.assertIn('sandbox', text)

    def test_preflight_command_routes_without_model_or_inference(self):
        text = 'check Codex sandbox abcdef012345'
        self.assertEqual(command(text), {'action': 'preflight', 'id': 'abcdef012345'})
        self.assertEqual(resolve(text, [codex_assistant.PLUGIN]), [('codex_assistant', command(text))])

    def test_systemdrive_preserved_without_api_credentials(self):
        with patch.dict(os.environ, {'SystemDrive': 'C:', 'OPENAI_API_KEY': 'not-a-real-key'}):
            env = sandbox.clean_environment(self.root)
        self.assertTrue(any(k.upper() == 'SYSTEMDRIVE' and v == 'C:' for k, v in env.items()))
        self.assertNotIn('OPENAI_API_KEY', env)

    def test_login_is_not_reported_as_execution_readiness(self):
        from core import codex_bridge
        with patch.object(codex_bridge.shutil, 'which', return_value='C:/approved/codex.exe'), patch.object(codex_bridge, 'run_process', side_effect=[
                SimpleNamespace(returncode=0, stdout='codex-cli 0.163.0'),
                SimpleNamespace(returncode=0, stdout='Logged in using ChatGPT')]):
            result = codex_bridge.diagnostic(self.runtime.context())
        self.assertTrue(result['authenticated'])
        self.assertFalse(result['execution_ready'])
        self.assertIn('blocked', result['message'])
