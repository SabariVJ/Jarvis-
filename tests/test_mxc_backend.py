"""No model inference. Mocked attestations plus local Python process fixtures."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import Mock, patch
from test_automation import Isolated
import test_phase51_execution as phase51
from core import mxc_backend as mxc, codex_sandbox as sandbox, codex_tasks as tasks, codex_preflight
from core.codex_commands import command
from plugins import task_manager

REAL_CHECKED = mxc.checked_command
REAL_SOURCE_PROMPT = mxc.source_prompt


class MxcTests(Isolated):
    def fixture(self, goal='read-only explain plugins/demo.py'):
        fixture = phase51.CodexTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        job = fixture.prepare(goal)
        return fixture, job, fixture.runtime.context()

    def proof(self, job, context):
        return {'ready': True, 'backend': 'mxc', 'checks': {key: True for key in mxc.CHECKS},
            'execution_id': context['execution_id'], 'checked_at': time.monotonic(), 'binding': mxc.binding(job, context)}

    def simulated(self, fixture, job, context, features='shell_tool stable false\nunified_exec stable false', broker=True):
        def scope(job, args, directory, env, context, rt, checks):
            for key in mxc.SCOPE_CHECKS:
                checks[key] = True
        def account(job, directory, context, rt, checks):
            checks.update(chatgpt_login=True, provider_http=broker, provider_websocket=True)
        with patch.object(mxc, 'managed_shell_probe', return_value=True), patch.object(mxc, 'scope_probe', side_effect=scope), patch.object(mxc, 'cancellation_probe', return_value=True), patch.object(mxc, 'broker_probe', side_effect=account), patch.object(mxc, 'spawn', side_effect=[(0, '[{"type":"message"}]'), (0, features)]):
            return mxc.verify(job, context, fixture.runtime)

    def test_managed_shell_startup_failure_rejects_readiness(self):
        fixture, job, context = self.fixture()
        with patch.object(mxc, 'spawn', return_value=(4294901760, '')):
            self.assertFalse(mxc.managed_shell_probe(job, mxc.candidate_command(job),
                fixture.storage / job['id'], sandbox.clean_environment(fixture.storage), context, fixture.runtime))

    def test_managed_shell_nonzero_with_success_text_is_not_success(self):
        fixture, job, context = self.fixture()
        with patch.object(mxc, 'spawn', return_value=(1, 'JARVIS_MXC_MANAGED_OK')):
            self.assertFalse(mxc.managed_shell_probe(job, mxc.candidate_command(job),
                fixture.storage / job['id'], sandbox.clean_environment(fixture.storage), context, fixture.runtime))

    def test_candidate_retains_privacy_and_disables_requested_shells(self):
        fixture, job, context = self.fixture()
        args = mxc.candidate_command(job)
        joined = ' '.join(args)
        for expected in ('windows.sandbox="mxc"', '":root"="deny"', 'permissions.jarvis.network.enabled=false', 'approval_policy="never"', 'features.shell_tool=false'):
            self.assertIn(expected, joined)
        self.assertNotIn('danger-full-access', joined)

    def test_non_read_only_task_cannot_select_mxc(self):
        fixture, job, context = self.fixture('refactor plugins/demo.py')
        with self.assertRaises(ValueError):
            mxc.candidate_command(job)

    def test_reparse_task_temp_is_rejected_before_any_probe(self):
        fixture, job, context = self.fixture()
        with patch.object(Path, 'is_symlink', autospec=True, side_effect=lambda path: path.name == 'temp'):
            with self.assertRaises(ValueError):
                tasks.workspace_for(job['id'])

    def test_all_checks_and_binding_required_for_command(self):
        fixture, job, context = self.fixture()
        check = self.proof(job, context)
        args = REAL_CHECKED(job, check, context, fixture.runtime)
        self.assertIn('windows.sandbox="mxc"', args)

    def test_one_failed_check_blocks_command(self):
        fixture, job, context = self.fixture()
        for name in mxc.CHECKS:
            check = self.proof(job, context)
            check['checks'][name] = False
            with self.assertRaises(ValueError):
                REAL_CHECKED(job, check, context, fixture.runtime)

    def test_different_execution_cannot_reuse_proof(self):
        fixture, job, context = self.fixture()
        check = self.proof(job, context)
        check['execution_id'] = 'old-execution'
        with self.assertRaises(ValueError):
            REAL_CHECKED(job, check, context, fixture.runtime)

    def test_expired_and_future_proofs_rejected(self):
        fixture, job, context = self.fixture()
        for delta in (-61, 10):
            check = self.proof(job, context)
            check['checked_at'] += delta
            with self.assertRaises(ValueError):
                REAL_CHECKED(job, check, context, fixture.runtime)

    def test_environment_change_invalidates_binding(self):
        fixture, job, context = self.fixture()
        check = self.proof(job, context)
        with patch.dict(os.environ, {'PATH': os.environ['PATH'] + ';changed-fixture'}):
            with self.assertRaises(ValueError):
                REAL_CHECKED(job, check, context, fixture.runtime)

    def test_policy_change_invalidates_binding(self):
        fixture, job, context = self.fixture()
        check = self.proof(job, context)
        args = mxc.candidate_command(job)
        with patch.object(mxc, 'candidate_command', return_value=args + ['changed-policy']):
            with self.assertRaises(ValueError):
                REAL_CHECKED(job, check, context, fixture.runtime)

    def test_source_change_invalidates_binding(self):
        fixture, job, context = self.fixture()
        check = self.proof(job, context)
        (fixture.storage / job['id'] / 'workspace/plugins/demo.py').write_text('changed = True\n')
        with self.assertRaises(ValueError):
            REAL_CHECKED(job, check, context, fixture.runtime)

    def test_binary_change_invalidates_binding(self):
        fixture, job, context = self.fixture()
        check = self.proof(job, context)
        with patch.object(sandbox, 'fingerprint', return_value='changed-binary'):
            with self.assertRaises(ValueError):
                REAL_CHECKED(job, check, context, fixture.runtime)

    def test_input_contains_only_approved_copied_source(self):
        fixture, job, context = self.fixture()
        text = REAL_SOURCE_PROMPT(job)
        self.assertIn('value = 1', text)
        self.assertIn('Shell tools are disabled', text)
        self.assertNotIn('automation.db', text)

    def test_future_verified_runtime_can_be_ready_without_inference(self):
        fixture, job, context = self.fixture()
        result = self.simulated(fixture, job, context)
        self.assertTrue(result['ready'])
        self.assertEqual(result['backend'], 'mxc')
        self.assertFalse(result['inference_submitted'])

    def test_unified_engine_on_does_not_override_disabled_command_gate(self):
        fixture, job, context = self.fixture()
        result = self.simulated(fixture, job, context, features='shell_tool stable false\nunified_exec stable true')
        self.assertTrue(result['ready'])
        self.assertEqual(result['observed_shell_features']['unified_exec'], 'true')

    def test_enabled_command_gate_keeps_backend_disabled(self):
        fixture, job, context = self.fixture()
        result = self.simulated(fixture, job, context, features='shell_tool stable true\nunified_exec stable false')
        self.assertFalse(result['ready'])
        self.assertIn('shell_tools_disabled', result['error'])

    def test_provider_failure_keeps_backend_disabled(self):
        fixture, job, context = self.fixture()
        result = self.simulated(fixture, job, context, broker=False)
        self.assertFalse(result['ready'])
        self.assertIn('provider_http', result['error'])

    def test_grouped_doctor_checks_are_read_without_private_details(self):
        fixture, job, context = self.fixture()
        checks = {key: False for key in mxc.CHECKS}
        data = {'checks': {'network': [{'id': 'network.provider_reachability', 'status': 'ok'},
                                     {'id': 'network.websocket_reachability', 'status': 'ok'}]}}
        with patch.object(mxc, 'spawn', return_value=(0, json.dumps(data))):
            mxc.broker_probe(job, fixture.storage / job['id'], context, fixture.runtime, checks)
        self.assertTrue(checks['provider_http'])
        self.assertTrue(checks['provider_websocket'])

    def test_contradictory_doctor_rows_fail_closed(self):
        fixture, job, context = self.fixture()
        checks = {key: False for key in mxc.CHECKS}
        data = {'checks': [{'id': 'network.provider_reachability', 'status': 'ok'},
                           {'id': 'network.provider_reachability', 'status': 'error'}]}
        with patch.object(mxc, 'spawn', return_value=(0, json.dumps(data))):
            mxc.broker_probe(job, fixture.storage / job['id'], context, fixture.runtime, checks)
        self.assertFalse(checks['provider_http'])

    def test_cancelled_context_cannot_use_ready_proof(self):
        fixture, job, context = self.fixture()
        check = self.proof(job, context)
        fixture.runtime.cancel()
        with self.assertRaises(InterruptedError):
            REAL_CHECKED(job, check, context, fixture.runtime)

    def test_fresh_read_only_worker_uses_bound_mxc_and_source_input_once(self):
        fixture, job, context = self.fixture()
        def verified(job, context, rt):
            return {'ready': True, 'backend': 'mxc', 'mxc': self.proof(job, context)}
        def synchronous(identity, worker, rt):
            worker(identity, rt, rt.job_context(identity))
        def stream(args, prompt, workspace, temp, context, rt, event, timeout):
            self.assertIn('windows.sandbox="mxc"', args)
            self.assertIn('features.shell_tool=false', args)
            self.assertIn('value = 1', prompt)
            self.assertIn('Do not edit files, run tests/commands', prompt)
            event({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Explanation of supplied source.'}})
            event({'type': 'turn.completed'})
            return {'returncode': 0, 'turn_completed': True, 'completion_verified': True}
        context = fixture.runtime.new_turn('run Codex task ' + job['id'])
        with patch.object(codex_preflight, 'inspect', side_effect=verified), patch.object(mxc, 'checked_command', side_effect=REAL_CHECKED), patch.object(mxc, 'source_prompt', side_effect=REAL_SOURCE_PROMPT), patch.object(task_manager, 'launch_supervised', side_effect=synchronous), patch.object(sandbox, 'stream', side_effect=stream) as execution:
            result = tasks.run(command(context['user_text']), context, fixture.runtime)
            self.assertEqual(result['state'], 'completed')
            later = fixture.runtime.new_turn('run Codex task ' + job['id'])
            tasks.run(command(later['user_text']), later, fixture.runtime)
            execution.assert_called_once()
        self.assertEqual((fixture.project / 'plugins/demo.py').read_text(), 'value = 1\n')


class MxcProcessTests(Isolated):
    def test_stderr_does_not_corrupt_json_metadata(self):
        env = sandbox.clean_environment(self.root)
        code, text = mxc.spawn([sys.executable, '-c', 'import sys; print("{\\\"ok\\\":true}"); print("fixture warning",file=sys.stderr)'],
            self.root, env, self.runtime.context(), self.runtime)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(text)['ok'])

    def test_cancellation_kills_only_spawned_probe(self):
        timer = threading.Timer(.2, self.runtime.cancel)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                mxc.spawn([sys.executable, '-c', 'import time; time.sleep(10)'], self.root,
                    sandbox.clean_environment(self.root), self.runtime.context(), self.runtime)
        finally:
            timer.join()

    def test_probe_output_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            mxc.spawn([sys.executable, '-c', 'print("x"*1100000)'], self.root,
                sandbox.clean_environment(self.root), self.runtime.context(), self.runtime)

    def test_large_source_input_cannot_block_timeout(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            sandbox.stream([sys.executable, '-c', 'import time; time.sleep(10)'], 'x' * 200000,
                self.root, self.root, self.runtime.context(), self.runtime, lambda _: None, timeout=.15)
        self.assertLess(time.monotonic() - started, 3)

    def test_full_source_input_is_delivered_without_command_line_payload(self):
        code = 'import sys,json; n=len(sys.stdin.read()); print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":str(n)}})); print(json.dumps({"type":"turn.completed"}))'
        events = []
        result = sandbox.stream([sys.executable, '-c', code], 'x' * 100000,
            self.root, self.root, self.runtime.context(), self.runtime, events.append)
        self.assertTrue(result['completion_verified'])
        self.assertEqual(events[0]['item']['text'], '100000')
