"""Offline channel awareness: no desktop, network, microphone or real calls."""
import json
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tests.test_automation import Isolated
from core import calling_intents as ci
from core.command_router import resolve
from core.automation_runtime import AutomationRuntime
from plugins import calling_assistant, whatsapp_control


class CallingAwarenessTests(Isolated):
    def setUp(self):
        super().setUp()
        self.declarations = [calling_assistant.PLUGIN, whatsapp_control.PLUGIN]
        self.runtime.catalog_provider = lambda: self.declarations

    def ask(self, text='Can you call someone?'):
        context = self.runtime.new_turn(text)
        action = ci.route(text, self.declarations)[0][1]['action']
        return ci.respond(self.runtime, context, action)

    def reply(self, text, source='user'):
        context = self.runtime.new_turn(text, source=source)
        return context, resolve(text, self.declarations, self.runtime, context)

    def test_question_does_not_dial(self):
        with patch.object(whatsapp_control, 'run') as dial:
            data = self.ask()
        dial.assert_not_called()
        self.assertFalse(data['call_initiated'])
        self.assertTrue(data['capabilities']['whatsapp_voice'])
        self.assertIn('WhatsApp', data['message'])
        self.assertFalse(data['capabilities']['telephone_dialing_permitted'])

    def test_exact_recipient_followup(self):
        self.ask()
        self.runtime.finish(self.runtime.context())
        context, steps = self.reply('Ganesh')
        self.assertEqual(steps, [('whatsapp_control', {'action': 'voice_call', 'contact': 'Ganesh'})])
        self.runtime.authorize(*steps[0], context)
        with self.assertRaises((ValueError, InterruptedError)):
            self.runtime.authorize('whatsapp_control', {'action': 'voice_call', 'contact': 'Appa'}, context)

    def test_bare_call_then_video(self):
        data = self.ask('Call Ganesh')
        self.assertEqual(data['state'], 'awaiting_channel')
        context, steps = self.reply('Video')
        self.assertEqual(steps, [('whatsapp_control', {'action': 'video_call', 'contact': 'Ganesh'})])
        self.runtime.authorize(*steps[0], context)
        self.assertEqual(context['user_text'], 'Video')

    def test_bare_call_cannot_directly_dial(self):
        context = self.runtime.new_turn('Call Ganesh')
        with self.assertRaises((ValueError, InterruptedError)):
            self.runtime.authorize('whatsapp_control', {'action': 'voice_call', 'contact': 'Ganesh'}, context)

    def test_explicit_whatsapp_routes_directly(self):
        for phrase, action in [('Voice call Ganesh on WhatsApp', 'voice_call'), ('Video call Ganesh on WhatsApp', 'video_call')]:
            context, steps = self.reply(phrase)
            self.assertEqual(steps, [('whatsapp_control', {'action': action, 'contact': 'Ganesh'})])
            self.runtime.authorize(*steps[0], context)

    def test_expiry(self):
        with patch.object(ci.time, 'monotonic', return_value=100):
            self.ask()
        with patch.object(ci.time, 'monotonic', return_value=161):
            self.assertIsNone(self.reply('Ganesh')[1])

    def test_topic_change_consumes_pending(self):
        self.ask()
        self.reply('What is the weather')
        self.assertIsNone(self.reply('Ganesh')[1])

    def test_cancel_consumes_pending(self):
        self.ask()
        self.runtime.cancel()
        self.assertIsNone(self.reply('Ganesh')[1])

    def test_reconnect_consumes_pending(self):
        self.ask()
        self.runtime.clear_calling_context()
        self.assertIsNone(self.reply('Ganesh')[1])

    def test_restart_does_not_restore_grant(self):
        self.ask()
        restored = AutomationRuntime(self.runtime.path)
        restored.catalog_provider = lambda: self.declarations
        context = restored.new_turn('Ganesh')
        self.assertIsNone(ci.followup(restored, context))

    def test_question_not_executed_cannot_arm_followup(self):
        self.runtime.new_turn('Can you call someone?')
        self.assertIsNone(self.reply('Ganesh')[1])

    def test_old_callback_cannot_arm_pending(self):
        old = self.runtime.new_turn('Can you call someone?')
        self.runtime.new_turn('Open Chrome')
        with self.assertRaises((ValueError, InterruptedError)):
            ci.respond(self.runtime, old, 'capabilities')
        self.assertIsNone(self.runtime._calling_pending)

    def test_partial_native_reply_never_authorizes(self):
        self.ask()
        context, steps = self.reply('', source='speech_live')
        self.runtime.update_turn_text('Ganesh', final=False)
        self.assertIsNone(ci.followup(self.runtime, self.runtime.context()))
        self.runtime.update_turn_text('Ganesh', final=True)
        self.assertEqual(ci.followup(self.runtime, self.runtime.context())[0][1]['contact'], 'Ganesh')

    def test_native_partial_question_does_not_arm(self):
        context = self.runtime.new_turn('Can you call someone?', source='speech_live')
        ci.respond(self.runtime, context, 'capabilities')
        self.assertIsNone(self.runtime._calling_pending)

    def test_partial_explicit_native_call_requires_finished_transcript(self):
        context = self.runtime.new_turn('Call Ganesh on WhatsApp', source='speech_live')
        args = {'action': 'voice_call', 'contact': 'Ganesh'}
        with self.assertRaises(InterruptedError):
            self.runtime.authorize('whatsapp_control', args, context)
        self.runtime.update_turn_text(context['user_text'], final=True)
        self.runtime.authorize('whatsapp_control', args, self.runtime.context())

    def test_enabled_tools_not_assumed(self):
        self.declarations = [calling_assistant.PLUGIN]
        data = self.ask()
        self.assertFalse(data['capabilities']['whatsapp_voice'])
        self.assertEqual(data['state'], 'informational')
        self.assertIsNone(self.reply('Ganesh')[1])

    def test_video_only_does_not_claim_voice(self):
        declaration = json.loads(json.dumps(whatsapp_control.PLUGIN))
        declaration['parameters']['properties']['action']['enum'] = ['video_call']
        self.declarations = [calling_assistant.PLUGIN, declaration]
        data = self.ask()
        self.assertIn('voice calling is not enabled', data['message'])
        self.assertEqual(data['state'], 'informational')

    def test_disable_tool_after_question_invalidates_reply(self):
        self.ask()
        self.declarations = [calling_assistant.PLUGIN]
        self.assertIsNone(self.reply('Ganesh')[1])

    def test_phone_status_end_and_microphone_are_truthful(self):
        for text in ['Call Ganesh by telephone', 'Call status', 'End the call', 'Can you answer calls?']:
            data = self.ask(text)
            self.assertFalse(data['call_initiated'])
            self.assertEqual(data['state'], 'informational')
        self.assertIn('microphone is separate', data['message'])

    def test_informational_specific_name_not_call(self):
        context, steps = self.reply('Can you call Ganesh on WhatsApp?')
        self.assertEqual(steps, [('calling_assistant', {'action': 'capabilities'})])
        with self.assertRaises((ValueError, InterruptedError)):
            self.runtime.authorize('whatsapp_control', {'action': 'voice_call', 'contact': 'Ganesh'}, context)

    def test_model_invented_question_action_rejected(self):
        context = self.runtime.new_turn('Open Chrome')
        with self.assertRaises((ValueError, InterruptedError)):
            ci.respond(self.runtime, context, 'capabilities')

    def test_unrecognized_informational_text_cannot_authorize_call(self):
        for text in ['Tell me about a call to Ganesh', 'Could you explain how to call Ganesh',
                     'Can you make a call to Ganesh?', 'Do not call Ganesh on WhatsApp']:
            context = self.runtime.new_turn(text)
            with self.assertRaises((ValueError, InterruptedError)):
                self.runtime.authorize('whatsapp_control', {'action': 'voice_call', 'contact': 'Ganesh'}, context)

    def test_followup_same_turn_and_persistent_cooldown(self):
        self.ask()
        context, steps = self.reply('Ganesh')
        self.assertIsNone(self.runtime.reserve_call('Ganesh', context))
        self.assertIn('already attempted', self.runtime.reserve_call('Ganesh', context))
        self.runtime.new_turn('Call Appa on WhatsApp')
        self.assertIn('cooldown', self.runtime.reserve_call('Appa', self.runtime.context()))

    def test_completed_followup_cannot_replay(self):
        self.ask()
        context, steps = self.reply('Ganesh')
        self.runtime.finish(context)
        self.assertIsNone(ci.followup(self.runtime, context))
        invoke = Mock()
        with self.assertRaises((ValueError, InterruptedError)):
            self.runtime.execute('whatsapp_control', steps[0][1], invoke, context)
        invoke.assert_not_called()

    def test_unknown_call_state_summary_not_connected(self):
        payload = 'status=unverified tool=whatsapp_control retry_allowed=false'
        message = whatsapp_control.summarize(payload)
        self.assertIn('not independently verified', message)
        self.assertIn('not retry', message)

    def test_dynamic_policy_reports_inventory(self):
        policy = ci.capability_policy(self.declarations)
        self.assertIn('"whatsapp_voice": true', policy)
        self.assertIn('"telephone_dialing_permitted": false', policy)
        failure = 'status=failed tool=calling_assistant reason=cancelled'
        self.assertIn(failure, ci.summarize(failure))
        from providers.engine_pipeline import PipelineEngine
        engine = PipelineEngine(Mock(caps={'tools': True, 'tool_format': 'openai', 'max_tools': 1}),
            config=SimpleNamespace(system_instruction='', tools=[{'function_declarations': self.declarations}]))
        engine._runtime = self.runtime
        engine._refresh_tool_policy()
        self.assertIn('"whatsapp_voice": true', engine._system)
        self.assertEqual(len(engine._declarations), 1)

    def test_no_second_followup_without_fresh_prompt(self):
        self.ask()
        self.reply('Ganesh')
        self.assertIsNone(self.reply('Ganesh')[1])

    def test_no_and_cancel_are_not_recipient_authorization(self):
        for text in ['No', 'Cancel', "Don't call Ganesh", 'Never mind']:
            self.ask()
            self.assertIsNone(ci.followup(self.runtime, self.reply(text)[0]))

    def test_repeated_question_tool_does_not_extend_ttl(self):
        with patch.object(ci.time, 'monotonic', return_value=100):
            self.ask()
        original = dict(self.runtime._calling_pending)
        with patch.object(ci.time, 'monotonic', return_value=140):
            ci.respond(self.runtime, self.runtime.context(), 'capabilities')
        self.assertEqual(original, self.runtime._calling_pending)

    def test_question_routes_without_misleading_model(self):
        from providers.engine_pipeline import PipelineEngine
        async def exercise():
            model = Mock(caps={'tools': True, 'tool_format': 'openai'})
            model.chat.side_effect = AssertionError('Must not invent unavailable calling')
            engine = PipelineEngine(model, config=SimpleNamespace(system_instruction='',
                tools=[{'function_declarations': self.declarations}]), fast_commands=False)
            engine._runtime = self.runtime
            context = self.runtime.new_turn('Can you call someone?')
            engine._history = [{'role': 'user', 'content': context['user_text']}]
            await engine._generate(context=context)
            self.assertEqual(list(engine._awaiting.values()), ['calling_assistant'])
            model.chat.assert_not_called()
            await engine.close()
        asyncio.run(exercise())

    def test_verified_evidence_not_rewritten_by_model(self):
        from providers.engine_pipeline import PipelineEngine
        from plugins._automation_common import report
        async def exercise():
            model = Mock(caps={'tools': True, 'tool_format': 'openai'})
            model.chat.side_effect = AssertionError('Must not falsely claim connection or unavailability')
            engine = PipelineEngine(model, config=SimpleNamespace(system_instruction='',
                tools=[{'function_declarations': self.declarations}]))
            engine._runtime = self.runtime
            data = self.ask()
            for name, payload in [('calling_assistant', report('calling_assistant', data)),
                                  ('whatsapp_control', 'status=unverified tool=whatsapp_control retry_allowed=false')]:
                engine._history = [{'role': 'tool', 'name': name, 'content': payload}]
                await engine._generate(context=self.runtime.context())
                self.assertEqual(engine._history[-1]['role'], 'assistant')
                if name == 'whatsapp_control':
                    self.assertIn('not independently verified', engine._history[-1]['content'])
            model.chat.assert_not_called()
            await engine.close()
        asyncio.run(exercise())
