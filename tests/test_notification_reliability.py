"""Notification receipts and privacy checks; no UI, speech or external delivery."""
import time
from unittest.mock import Mock, patch
from tests.test_automation import Isolated
from core import local_notifications as alerts

class NotificationReliabilityTests(Isolated):
    def setUp(self):
        super().setUp()
        self.config={'routes_json':'{"completed":["dashboard","desktop","speech"]}'}
        self.cfg=patch.object(alerts,'get_plugin_config',side_effect=lambda _:self.config)
        self.cfg.start();self.addCleanup(self.cfg.stop)
        self.enabled=patch.object(alerts,'get_plugin_enabled',return_value=True)
        self.enabled.start();self.addCleanup(self.enabled.stop)
        self.runtime.dashboard_notify=Mock()
        self.runtime.desktop_notify=Mock(return_value='submitted_to_windows')
        self.runtime.local_speech=Mock(return_value=True)

    def test_untrusted_text_and_paths_never_enter_previews(self):
        result=alerts.emit('job','completed','PRIVATE SOURCE arbitrary sensitive sentence',verified=True,
            result_path='D:/private-project/secret-result.txt',rt=self.runtime)
        self.assertNotIn('PRIVATE',result['message'])
        self.assertNotIn('private-project',result['message'])
        self.assertEqual(result['result_path'],'D:/private-project/secret-result.txt')
        for cb in (self.runtime.dashboard_notify,self.runtime.desktop_notify,self.runtime.local_speech):
            self.assertNotIn('PRIVATE',str(cb.call_args))
            self.assertNotIn('secret-result',str(cb.call_args))

    def test_quiet_hours_suppress_intrusive_channels_keep_dashboard(self):
        self.config['quiet_hours']='22:00-07:00'
        with patch.object(alerts.time,'localtime',return_value=time.struct_time((2026,10,24,23,0,0,5,297,0))):
            result=alerts.emit('night','completed','done',verified=True,rt=self.runtime)
        self.runtime.dashboard_notify.assert_called_once()
        self.runtime.desktop_notify.assert_not_called();self.runtime.local_speech.assert_not_called()
        self.assertEqual(result['channels']['desktop'],'suppressed_quiet_hours')

    def test_quiet_boundary_and_daytime_interval(self):
        for setting,hour,minute,expected in [('22:00-07:00',22,0,True),('22:00-07:00',7,0,False),
                                           ('09:00-17:00',10,0,True),('09:00-17:00',18,0,False)]:
            self.config['quiet_hours']=setting
            with patch.object(alerts.time,'localtime',return_value=time.struct_time((2026,10,24,hour,minute,0,5,297,0))):
                self.assertEqual(alerts.quiet_now(),expected)

    def test_invalid_quiet_configuration_fails_closed(self):
        for value in ('25:00-07:00','22:00-22:00','invalid'):
            self.config['quiet_hours']=value
            result=alerts.emit(value,'completed','done',verified=True,rt=self.runtime)
            self.assertIn('route_error',result)
        self.runtime.desktop_notify.assert_not_called();self.runtime.local_speech.assert_not_called()

    def test_desktop_false_does_not_claim_submission(self):
        self.runtime.desktop_notify.return_value=False
        result=alerts.emit('false','completed','done',verified=True,rt=self.runtime)
        self.assertEqual(result['channels']['desktop'],'deferred_or_unavailable')

    def test_callback_payload_does_not_leak_through_receipt(self):
        self.runtime.desktop_notify.return_value='SECRET CALLBACK RESPONSE'
        result=alerts.emit('callback','completed','done',verified=True,rt=self.runtime)
        self.assertNotIn('SECRET',str(result))

    def test_interruption_is_not_retried_after_runtime_recreation(self):
        from core.automation_runtime import AutomationRuntime
        self.runtime.desktop_notify.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            alerts.emit('interrupted','completed','done',verified=True,rt=self.runtime)
        fresh=AutomationRuntime(self.runtime.path)
        fresh.desktop_notify=Mock()
        result=alerts.emit('interrupted','completed','done',verified=True,rt=fresh)
        self.assertEqual(result['channels']['desktop'],'dispatching_outcome_unverified')
        fresh.desktop_notify.assert_not_called()

    def test_zero_sequence_is_distinct_from_missing_sequence(self):
        alerts.emit('sequence','completed','done',verified=True,rt=self.runtime)
        alerts.emit('sequence','completed','done',verified=True,rt=self.runtime,sequence=0)
        self.assertEqual(self.runtime.desktop_notify.call_count,2)

    def test_legacy_receipt_does_not_replay(self):
        old={'task_id':'legacy','event':'completed','channels':{'desktop':'submitted_to_windows'}}
        self.runtime.put('notification','legacy:completed:once',old)
        self.assertEqual(alerts.emit('legacy','completed','done',verified=True,rt=self.runtime),old)
        self.runtime.desktop_notify.assert_not_called()

    def test_disabled_plugin_has_no_side_effects(self):
        with patch.object(alerts,'get_plugin_enabled',return_value=False):
            result=alerts.emit('disabled','completed','done',verified=True,rt=self.runtime)
        self.assertTrue(result['delivery_disabled'])
        self.runtime.desktop_notify.assert_not_called()
        self.runtime.dashboard_notify.assert_not_called()

    def test_legacy_task_id_with_colon_does_not_replay(self):
        old={'task_id':'project:job','event':'completed','channels':{'desktop':'submitted_to_windows'}}
        self.runtime.put('notification','project:job:completed:once',old)
        self.assertEqual(alerts.emit('project:job','completed','done',verified=True,rt=self.runtime),old)
        self.runtime.desktop_notify.assert_not_called()

    def test_legacy_key_collision_does_not_suppress_another_task(self):
        self.runtime.put('notification','project:completed:once',
            {'task_id':'different','event':'completed','channels':{}})
        result=alerts.emit('project','completed','done',verified=True,rt=self.runtime)
        self.assertEqual(result['task_id'],'project')
        self.runtime.desktop_notify.assert_called_once()
