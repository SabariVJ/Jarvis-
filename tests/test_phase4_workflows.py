import json
from unittest.mock import Mock, patch
from core import outcome
from plugins import task_manager as tasks
from test_automation import Isolated


def step(tool='automation_status', action='performance', **fields):
    return {'tool': tool, 'arguments_json': json.dumps({'action': action}), **fields}


class WorkflowTests(Isolated):
    def job(self, steps, approved=False):
        self.runtime.put('job', 'demo', {'name': 'demo', 'steps': tasks.validate_steps(steps, approved),
            'next': 0, 'active_step': None, 'results': [], 'state': 'queued', 'retry_approved': approved})
        return self.runtime.job_context('demo')

    def dispatch(self, invoke):
        self.runtime.dispatch = lambda name, args, ctx: self.runtime.execute(name, args, invoke, ctx)

    def test_ordered_dependencies_and_visible_verified_progress(self):
        ctx = self.job([step(id='a'), step(id='b', depends_on=['a'])])
        invoke = Mock(return_value=outcome.done('automation_status'))
        self.dispatch(invoke)
        tasks._run_job('demo', self.runtime, ctx)
        job = self.runtime.get('job', 'demo')
        self.assertEqual(job['state'], 'completed')
        self.assertEqual([s['state'] for s in job['progress']], ['verified', 'verified'])
        self.assertEqual(invoke.call_count, 2)

    def test_forward_dependencies_and_duplicate_ids_rejected(self):
        for steps in ([step(id='a', depends_on=['b']), step(id='b')],
                      [step(id='a'), step(id='a')], [step(id='a', depends_on=['a'])]):
            with self.assertRaises(ValueError):
                tasks.validate_steps(steps)

    def test_failed_or_unverified_step_blocks_following_work(self):
        for result in (outcome.failed('automation_status', 'offline'), outcome.unverified('automation_status', 'unknown')):
            ctx = self.job([step(), step()])
            invoke = Mock(return_value=result)
            self.dispatch(invoke)
            tasks._run_job('demo', self.runtime, ctx)
            invoke.assert_called_once()
            self.assertEqual(self.runtime.get('job', 'demo')['progress'][0]['state'], 'failed')
            # Use a different job receipt identity for the next scenario.
            with self.runtime.db() as db:
                db.execute('DELETE FROM receipts')  # isolated test database only

    def test_read_retry_requires_approval_and_is_bounded(self):
        with self.assertRaises(ValueError):
            tasks.validate_steps([step(max_attempts=2)])
        ctx = self.job([step(max_attempts=2)], approved=True)
        invoke = Mock(side_effect=[outcome.failed('automation_status', 'temporary failure'), outcome.done('automation_status')])
        self.dispatch(invoke)
        tasks._run_job('demo', self.runtime, ctx)
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(self.runtime.get('job', 'demo')['state'], 'completed')
        self.assertEqual(self.runtime.get('job', 'demo')['progress'][0]['attempts'], 2)

    def test_gui_calls_messages_and_publishing_never_retry(self):
        for item in (step('open_app', max_attempts=2), step('whatsapp_control', 'voice_call', max_attempts=2),
                     step('github_git', 'push', max_attempts=2), step('browser_control', 'click', max_attempts=2)):
            with self.assertRaises(ValueError):
                tasks.validate_steps([item], allow_retries=True)

    def test_uncertain_read_is_not_retried(self):
        ctx = self.job([step(max_attempts=2)], approved=True)
        invoke = Mock(return_value=outcome.unverified('automation_status', 'unknown'))
        self.dispatch(invoke)
        tasks._run_job('demo', self.runtime, ctx)
        invoke.assert_called_once()
        self.assertEqual(self.runtime.get('job', 'demo')['state'], 'needs_attention')

    def test_cancel_between_verified_steps_and_new_command_is_independent(self):
        ctx = self.job([step(), step()])
        def invoke():
            self.runtime.new_turn('Open Chrome')
            self.runtime.update('job', 'demo', {'cancel_requested': True})
            return outcome.done('automation_status')
        self.dispatch(invoke)
        tasks._run_job('demo', self.runtime, ctx)
        job = self.runtime.get('job', 'demo')
        self.assertEqual(job['state'], 'paused')
        self.assertEqual([s['state'] for s in job['progress']], ['verified', 'cancelled'])

    def test_completed_receipt_is_reused_without_reinvoking_tool(self):
        ctx = self.job([step()])
        args = {'action': 'performance'}
        self.runtime.execute('automation_status', args, lambda: outcome.done('automation_status'),
                             {**ctx, 'step_id': '0'})
        invoke = Mock(return_value=outcome.done('automation_status'))
        self.dispatch(invoke)
        tasks._run_job('demo', self.runtime, ctx)
        invoke.assert_not_called()
        self.assertEqual(self.runtime.get('job', 'demo')['state'], 'completed')

    def test_recovery_uses_verified_receipt_but_never_dispatches(self):
        ctx = self.job([step(), step()])
        self.runtime.execute('automation_status', {'action': 'performance'}, lambda: outcome.done('automation_status'),
                             {**ctx, 'step_id': '0'})
        self.runtime.update('job', 'demo', {'state': 'running', 'active_step': 0})
        self.runtime.dispatch = Mock()
        with patch.object(tasks, '_scheduler', None), patch.object(tasks.threading, 'Thread'):
            tasks.start_scheduler(self.runtime)
        self.runtime.dispatch.assert_not_called()
        job = self.runtime.get('job', 'demo')
        self.assertEqual(job['next'], 1)
        self.assertEqual(job['state'], 'paused')
        self.assertEqual(job['progress'][0]['state'], 'verified')

    def test_uncertain_recovery_needs_attention_without_replay(self):
        self.job([step()])
        self.runtime.update('job', 'demo', {'state': 'running', 'active_step': 0})
        self.runtime.dispatch = Mock()
        with patch.object(tasks, '_scheduler', None), patch.object(tasks.threading, 'Thread'):
            tasks.start_scheduler(self.runtime)
        self.assertEqual(self.runtime.get('job', 'demo')['state'], 'needs_attention')
        self.runtime.dispatch.assert_not_called()

    def test_disabled_tool_is_rechecked_before_dispatch(self):
        ctx = self.job([step()])
        self.runtime.catalog_provider = lambda: []
        self.runtime.dispatch = Mock()
        tasks._run_job('demo', self.runtime, ctx)
        self.runtime.dispatch.assert_not_called()
        self.assertEqual(self.runtime.get('job', 'demo')['state'], 'needs_attention')

    def test_negated_retry_request_cannot_authorize_retry_recipe(self):
        context = self.runtime.new_turn('Save this workflow; do not allow retries')
        result = tasks.run({'action': 'save', 'name': 'no-retry', 'steps': [step(max_attempts=2)]}, session_memory=context)
        self.assertIn('status=failed', result)
        self.assertIsNone(self.runtime.get('recipe', 'no-retry'))

    def test_foreground_interrupt_does_not_cancel_independent_job(self):
        ctx = self.job([step()])
        self.runtime.cancel()
        self.runtime.check_cancelled(ctx)
        self.assertFalse(self.runtime.get('job', 'demo').get('cancel_requested', False))

    def test_explicit_stop_all_cancels_jobs_at_effect_boundary(self):
        ctx = self.job([step()])
        self.runtime.cancel(all_jobs=True)
        with self.assertRaises(InterruptedError):
            self.runtime.check_cancelled(ctx)
