"""Actual read-only tool -> real worker -> real emit, synthetic DB/callbacks only."""
import json
import time
from unittest.mock import Mock, patch
from test_automation import Isolated
from core import outcome, local_notifications as alerts
from plugins import task_manager as tasks, automation_status

RECIPE = {'action':'save','name':'notification-smoke-readonly-6b4k',
          'steps':[{'id':'read_metrics','tool':'automation_status',
                    'arguments_json':'{"action":"performance"}',
                    'depends_on':[],'max_attempts':1}]}


class NotificationWorkflowSmokeTests(Isolated):
    def setUp(self):
        super().setUp()
        self.runtime.catalog = [automation_status.PLUGIN, tasks.PLUGIN]
        self.config={'routes_json':json.dumps({'completed':['dashboard','desktop','speech']})}
        config_patch=patch.object(alerts,'get_plugin_config',side_effect=lambda _:self.config)
        config_patch.start();self.addCleanup(config_patch.stop)
        enable_patch=patch.object(alerts,'get_plugin_enabled',return_value=True)
        enable_patch.start();self.addCleanup(enable_patch.stop)
        self.runtime.dashboard_notify=Mock(return_value=True)
        self.runtime.desktop_notify=Mock(return_value='queued_to_ui; visibility_not_verified')
        self.runtime.local_speech=Mock(return_value=True)
        def dispatch(name,args,context):
            self.assertEqual(name,'automation_status')
            self.assertEqual(args,{'action':'performance'})
            return self.runtime.execute(name,args,lambda:automation_status.run(args,session_memory=context),context)
        self.runtime.dispatch=dispatch

    def start_recipe(self):
        context=self.runtime.new_turn('Save the explicit read-only notification recipe; no retries')
        result=tasks.run(RECIPE,session_memory=context)
        self.assertTrue(outcome.is_verified(result),result)
        self.assertEqual(self.runtime.records('job'),{})
        self.assertEqual(self.runtime.records('notification'),{})
        # Do not create background threads. Run the actual worker synchronously.
        with patch.object(tasks,'_launch'):
            job_id=tasks._start(RECIPE['name'],self.runtime)
        ctx=self.runtime.job_context(job_id)
        tasks._run_job(job_id,self.runtime,ctx)
        return job_id,ctx

    def test_exact_recipe_accepted_and_only_readonly_tool_runs(self):
        job_id,ctx=self.start_recipe()
        job=self.runtime.get('job',job_id)
        self.assertEqual(job['state'],'completed')
        self.assertEqual(job['progress'][0]['state'],'verified')
        self.assertTrue(outcome.is_verified(job['results'][0]['result']))
        self.assertEqual(len(self.runtime.stats()),1)
        self.assertEqual(self.runtime.stats()[0]['name'],'automation_status')

    def test_actual_worker_generates_real_notification_dispatch(self):
        job_id,ctx=self.start_recipe()
        rows=list(self.runtime.records('notification').values())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['task_id'],job_id)
        self.assertEqual(rows[0]['event'],'completed')
        self.assertTrue(rows[0]['verified'])
        self.runtime.dashboard_notify.assert_called_once()
        self.runtime.desktop_notify.assert_called_once()
        self.runtime.local_speech.assert_called_once()
        self.assertEqual(rows[0]['channels']['desktop'],'queued_to_ui; visibility_not_verified')

    def test_completed_worker_cannot_replay_or_redispatch(self):
        job_id,ctx=self.start_recipe()
        tasks._run_job(job_id,self.runtime,ctx)
        self.runtime.dashboard_notify.assert_called_once()
        self.runtime.desktop_notify.assert_called_once()
        self.assertEqual(self.runtime.stats()[0]['calls'],1)

    def test_quiet_hours_keep_dashboard_and_suppress_intrusive_channels(self):
        self.config['quiet_hours']='22:00-07:00'
        with patch.object(alerts.time,'localtime',return_value=time.struct_time((2026,10,24,23,0,0,5,297,0))):
            self.start_recipe()
        self.runtime.dashboard_notify.assert_called_once()
        self.runtime.desktop_notify.assert_not_called()
        self.runtime.local_speech.assert_not_called()

    def test_supported_actions_and_capabilities_restriction(self):
        for action in ['status','context','performance']:
            steps=tasks.validate_steps([{'tool':'automation_status','arguments_json':json.dumps({'action':action})}])
            self.assertEqual(steps[0]['max_attempts'],1)
        with self.assertRaisesRegex(ValueError,'cannot be replayed'):
            tasks.validate_steps([{'tool':'automation_status','arguments_json':'{"action":"capabilities"}'}])

    def test_recording_intentionally_excludes_automation_status(self):
        self.runtime.record_start()
        context=self.runtime.new_turn('Show performance')
        result=self.runtime.execute('automation_status',{'action':'performance'},
            lambda:automation_status.run({'action':'performance'},session_memory=context),context)
        self.assertTrue(outcome.is_verified(result))
        self.assertEqual(self.runtime.record_finish(),[])

    def test_prohibited_actions_remain_rejected(self):
        for tool,action in [('desktop_agent','arbitrary_action'),('whatsapp_control','voice_call'),('github_git','push')]:
            with self.assertRaises(ValueError):
                tasks.validate_steps([{'tool':tool,'arguments_json':json.dumps({'action':action})}])

    def test_missing_name_does_not_save_or_emit(self):
        result=tasks.run({**RECIPE,'name':''},session_memory=self.runtime.context())
        self.assertIn('Provide a recipe name',result)
        self.assertEqual(self.runtime.records('recipe'),{})
        self.assertEqual(self.runtime.records('notification'),{})

    def test_disabled_tool_rejected_before_saving(self):
        self.runtime.catalog=[]
        result=tasks.run(RECIPE,session_memory=self.runtime.context())
        self.assertIn('disabled or unavailable',result)
        self.assertEqual(self.runtime.records('recipe'),{})
        self.assertEqual(self.runtime.records('notification'),{})

    def test_unavailable_speech_is_not_success_or_automatically_retried(self):
        self.runtime.local_speech.return_value=False
        job_id,ctx=self.start_recipe()
        row=next(iter(self.runtime.records('notification').values()))
        self.assertEqual(row['channels']['speech'],'deferred_or_unavailable')
        tasks._run_job(job_id,self.runtime,ctx)
        self.runtime.local_speech.assert_called_once()
