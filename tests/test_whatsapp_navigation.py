"""Ordinary navigation never grants calling authority. All desktop effects mocked."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tests.test_automation import Isolated
from core import command_router as router, outcome
from actions import open_app
from plugins import whatsapp_control, calling_assistant, github_git

CATALOG=[open_app.TOOL,whatsapp_control.PLUGIN,calling_assistant.PLUGIN,github_git.PLUGIN]
OPEN=('open_app',{'app_name':'WhatsApp'})

class WhatsAppNavigationTests(Isolated):
    def test_navigation_phrases_share_route_and_authorization(self):
        for text in ('Open WhatsApp.','Hop on WhatsApp.','Launch WhatsApp.','Switch to WhatsApp.'):
            with self.subTest(text=text):
                ctx=self.runtime.new_turn(text)
                self.assertEqual(router.resolve(text,CATALOG),[OPEN])
                self.assertEqual(router.route(text),OPEN)
                self.assertEqual(router.authorization_plan(text),[OPEN])
                self.runtime.authorize(*OPEN,ctx)

    def test_hop_request_can_focus_existing_window_without_calling(self):
        ctx=self.runtime.new_turn('Hop on WhatsApp.')
        candidate=SimpleNamespace(pid=100,label='WhatsApp',score=100)
        with patch.object(open_app.appidentity,'running',return_value=[candidate]), \
             patch.object(open_app.appidentity,'focus',return_value=True) as focus, \
             patch('core.desktop_ownership.can_open',return_value=True), \
             patch('core.desktop_ownership.record_open',return_value=True), \
             patch.object(open_app,'_launch_and_verify') as launch, \
             patch.object(whatsapp_control,'run') as call:
            result=self.runtime.execute(*OPEN,lambda:open_app.open_app(OPEN[1],session_memory=ctx),ctx)
        self.assertTrue(outcome.is_verified(result),result)
        focus.assert_called_once_with(candidate);launch.assert_not_called();call.assert_not_called()

    def test_navigation_does_not_authorize_voice_or_video_calls(self):
        for text in ('Open WhatsApp','Hop on WhatsApp','Launch WhatsApp','Switch to WhatsApp'):
            ctx=self.runtime.new_turn(text)
            for action in ('voice_call','video_call'):
                with self.assertRaises(InterruptedError):
                    self.runtime.authorize('whatsapp_control',{'action':action,'contact':'Appa'},ctx)

    def test_polite_and_case_variations(self):
        for text in ('Jarvis, hop on WhatsApp please.','Hey Jarvis, please switch to WhatsApp.',
                     '  HOP   ON   WHATSAPP  '):
            self.assertEqual(router.resolve(text,CATALOG),[OPEN])

    def test_disabled_open_tool_or_unsupported_app_not_invented(self):
        self.assertIsNone(router.resolve('Hop on WhatsApp',[]))
        for text in ('Hop on unknown software','Switch to some app'):
            self.assertIsNone(router.resolve(text,CATALOG))

    def test_unrelated_negated_and_unclear_phrases_not_routed(self):
        for text in ('I spoke on WhatsApp yesterday','Do not hop on WhatsApp',
                     'Should I switch to WhatsApp','Maybe hop on WhatsApp later',
                     'Hop on WhatsApp and call Appa'):
            self.assertIsNone(router.resolve(text,CATALOG),text)

    def test_ambiguous_call_stays_clarification_and_cannot_dial(self):
        for text in ('Call Appa','Can you call Appa on WhatsApp?','Call someone on WhatsApp'):
            ctx=self.runtime.new_turn(text)
            steps=router.resolve(text,CATALOG,self.runtime,ctx)
            self.assertTrue(not steps or steps[0][0]=='calling_assistant')
            with self.assertRaises(InterruptedError):
                self.runtime.authorize('whatsapp_control',{'action':'voice_call','contact':'Appa'},ctx)

    def test_fresh_explicit_call_still_requires_exact_contact(self):
        ctx=self.runtime.new_turn('WhatsApp voice call Appa')
        steps=router.resolve(ctx['user_text'],CATALOG,self.runtime,ctx)
        self.assertEqual(steps,[('whatsapp_control',{'action':'voice_call','contact':'Appa'})])
        self.runtime.authorize(*steps[0],ctx)
        with self.assertRaises(InterruptedError):
            self.runtime.authorize('whatsapp_control',{'action':'voice_call','contact':'Someone Else'},ctx)

    def test_duplicate_native_or_model_responses_dispatch_once(self):
        ctx=self.runtime.new_turn('Hop on WhatsApp')
        invoke=Mock(return_value=outcome.done('open_app',verified=True))
        first=self.runtime.execute(*OPEN,invoke,{**ctx,'request_id':'first'})
        second=self.runtime.execute('open_app',{'app_name':'whatsapp.exe'},invoke,{**ctx,'request_id':'second'})
        self.assertEqual(first,second);invoke.assert_called_once()

    def test_completed_navigation_cannot_replay_in_new_turn(self):
        ctx=self.runtime.new_turn('Hop on WhatsApp')
        invoke=Mock(return_value=outcome.done('open_app',verified=True))
        self.runtime.execute(*OPEN,invoke,ctx)
        self.runtime.finish(ctx);self.runtime.new_turn('What time is it?')
        with self.assertRaises(InterruptedError):self.runtime.execute(*OPEN,invoke,ctx)
        invoke.assert_called_once()

    def test_guardian_watch_command_remains_exact_separate_selection(self):
        self.assertEqual(router.resolve('watch github SabariVJ/Jarvis-',CATALOG),
            [('github_git',{'action':'guardian_watch','repository':'sabarivj/jarvis-'})])
        self.assertIsNone(router.resolve('watch my repositories',CATALOG))
