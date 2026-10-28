"""Synthetic UI/voice regressions; no microphone, real UI or calls."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from tests.test_automation import Isolated
from core import calling_intents as ci, outcome
from core.command_router import resolve
from plugins import whatsapp_control as wa, calling_assistant

class CallRepairTests(Isolated):
    def setUp(self):
        super().setUp()
        self.declarations=[wa.PLUGIN,calling_assistant.PLUGIN]
        self.runtime.catalog_provider=lambda:self.declarations

    def test_requested_forms_route_and_authorize_without_confirmation(self):
        for text,who in [('Jarvis, call Appa on WhatsApp.','Appa'),
                         ('Jarvis, WhatsApp voice call my mother.','my mother'),
                         ('Jarvis, call Ganesh on WhatsApp.','Ganesh')]:
            ctx=self.runtime.new_turn(text)
            steps=resolve(text,self.declarations,self.runtime,ctx)
            self.assertEqual(steps,[('whatsapp_control',{'action':'voice_call','contact':who})])
            self.runtime.authorize(*steps[0],ctx)

    def test_questions_negation_and_ambiguous_names_do_not_authorize(self):
        for text in ['Do not call Appa on WhatsApp','Can you call Appa on WhatsApp?',
                     'WhatsApp voice call Appa and Ganesh','WhatsApp voice call anyone']:
            ctx=self.runtime.new_turn(text)
            with self.assertRaises((InterruptedError,ValueError)):
                self.runtime.authorize('whatsapp_control',{'action':'voice_call','contact':'Appa'},ctx)

    def test_model_source_cannot_bypass_unfinished_native_transcript(self):
        ctx=self.runtime.new_turn('Call Appa on WhatsApp',source='speech_live')
        with self.assertRaises(InterruptedError):
            self.runtime.authorize('whatsapp_control',{'action':'voice_call','contact':'Appa'},
                                   {**ctx,'source':'model','command_source':'speech_live'})

    def test_partial_then_final_claim_once(self):
        ctx=self.runtime.new_turn('Call Appa on WhatsApp',source='speech_live')
        self.assertIsNone(ci.claim_live_call(self.runtime,ctx,self.declarations))
        self.runtime.update_turn_text(ctx['user_text'],final=True)
        ctx=self.runtime.context()
        self.assertTrue(ci.claim_live_call(self.runtime,ctx,self.declarations))
        self.assertIsNone(ci.claim_live_call(self.runtime,ctx,self.declarations))

    def test_premature_native_model_call_does_not_poison_final_receipt(self):
        from tests.test_integration import DispatchTests
        from google.genai import types
        async def run():
            host=DispatchTests.host(self)
            self.runtime.catalog_provider=lambda:self.declarations
            ctx=self.runtime.new_turn('Call Appa on WhatsApp',source='speech_live')
            args={'action':'voice_call','contact':'Appa'}
            fc=types.FunctionCall(id='premature',name='whatsapp_control',args=args)
            result=await host._execute_tool(fc,{**ctx,'source':'model','command_source':'speech_live'})
            self.assertIn('state=transcript_incomplete',result.response['result'])
            host._plugin_registry.run.assert_not_called()
            self.assertIsNone(self.runtime.lookup_receipt('whatsapp_control',args,{**ctx,'step_id':'0'}))
            self.runtime.update_turn_text(ctx['user_text'],final=True)
            final=self.runtime.context()
            self.assertTrue(ci.claim_live_call(self.runtime,final,self.declarations))
            self.runtime.execute('whatsapp_control',args,lambda:host._plugin_registry.run(),final)
            host._plugin_registry.run.assert_called_once()
        asyncio.run(run())

    def test_stale_cancelled_and_completed_claims_refused(self):
        for operation in ['finish','cancel','new']:
            ctx=self.runtime.new_turn('Call Appa on WhatsApp',source='speech_live')
            self.runtime.update_turn_text(ctx['user_text'],final=True);ctx=self.runtime.context()
            if operation=='new':self.runtime.new_turn('Open Chrome')
            elif operation=='finish':self.runtime.finish(ctx)
            else:self.runtime.cancel()
            with self.assertRaises(InterruptedError):ci.claim_live_call(self.runtime,ctx,self.declarations)

    def host(self):
        path=Path(__file__).resolve().parents[1]/'main.py'
        cls=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='JarvisLive')
        methods=[n for n in cls.body if getattr(n,'name','') in {'_route_final_whatsapp_call','_dispatch_automation'}]
        module=ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='Host',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[]))
        env={'asyncio':asyncio};exec(compile(module,str(path),'exec'),env)
        host=env['Host']();host._automation=self.runtime;host.session=object();host._awake=True
        host._tool_tasks=set();host.ui=Mock();host.plugin_say=Mock();host.speak=Mock()
        host._action_registry=Mock();host._action_registry.has.return_value=False
        host._plugin_registry=Mock();host._plugin_registry.run.return_value=outcome.unverified('whatsapp_control','click only',retry_allowed=False)
        return host

    def test_native_dispatch_once_and_model_duplicate_reuses_receipt(self):
        async def run():
            host=self.host()
            ctx=self.runtime.new_turn('WhatsApp voice call Appa',source='speech_live')
            self.runtime.update_turn_text(ctx['user_text'],final=True)
            self.assertTrue(host._route_final_whatsapp_call())
            self.assertFalse(host._route_final_whatsapp_call())
            await asyncio.gather(*list(host._tool_tasks))
            self.runtime.execute('whatsapp_control',{'action':'voice_call','contact':'Appa'},
                                 lambda:host._plugin_registry.run(),self.runtime.context())
            self.assertEqual(host._plugin_registry.run.call_count,1)
        asyncio.run(run())

    def test_session_change_before_dispatch_does_not_click(self):
        async def run():
            host=self.host();ctx=self.runtime.new_turn('Call Appa on WhatsApp',source='speech_live')
            self.runtime.update_turn_text(ctx['user_text'],final=True)
            host._route_final_whatsapp_call();host.session=object()
            await asyncio.gather(*list(host._tool_tasks))
            host._plugin_registry.run.assert_not_called()
        asyncio.run(run())

    def test_sleep_or_disabled_plugin_does_not_dispatch(self):
        host=self.host();ctx=self.runtime.new_turn('Call Appa on WhatsApp',source='speech_live')
        self.runtime.update_turn_text(ctx['user_text'],final=True)
        host._awake=False;self.assertFalse(host._route_final_whatsapp_call())
        host._awake=True;self.declarations=[];self.assertFalse(host._route_final_whatsapp_call())

    def dialog(self,title='Microphone access',pid=8):
        dialog=Mock();dialog.process_id.return_value=pid;dialog.is_visible.return_value=True
        dialog.element_info=SimpleNamespace(name=title,control_type='Window')
        allow=Mock();allow.element_info=SimpleNamespace(name='Allow',control_type='Button')
        deny=Mock();deny.element_info=SimpleNamespace(name="Don't allow",control_type='Button')
        return dialog,allow,deny

    def test_permission_requires_owned_visible_dialog_and_choices(self):
        window=Mock();window.process_id.return_value=8
        dialog,allow,deny=self.dialog()
        with patch.object(wa,'_items',side_effect=lambda root,kinds:[dialog] if root is window else [allow,deny]):
            self.assertIsNotNone(wa._permission_evidence(window))
            dialog.process_id.return_value=9;self.assertIsNone(wa._permission_evidence(window))
            dialog.process_id.return_value=8;dialog.is_visible.return_value=False
            self.assertIsNone(wa._permission_evidence(window))
        allow.click_input.assert_not_called();deny.click_input.assert_not_called()

    def test_chat_text_or_unknown_dialog_not_permission_evidence(self):
        window=Mock();window.process_id.return_value=8
        dialog,allow,deny=self.dialog('Microphone access');dialog.element_info.control_type='Text'
        with patch.object(wa,'_items',side_effect=lambda root,kinds:[dialog] if root is window else [allow,deny]):
            self.assertIsNone(wa._permission_evidence(window))
            dialog.element_info.control_type='Window';dialog.element_info.name='Settings'
            self.assertIsNone(wa._permission_evidence(window))

    def plugin_run(self,**overrides):
        ctx=self.runtime.new_turn('Voice call Appa on WhatsApp')
        values={'_active_call':False,'_ensure_window':Mock(),'_search':[Mock()], '_open_chat':None,
                '_header_matches':True,'_call_button':(Mock(),False),'_permission_evidence':None}
        values.update(overrides)
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name,value in values.items():stack.enter_context(patch.object(wa,name,return_value=value))
            stack.enter_context(patch.object(wa.time,'sleep'))
            result=wa.run({'action':'voice_call','contact':'Appa'},session_memory=ctx)
        return result,values

    def test_dialog_before_call_does_not_reserve_or_click(self):
        button=Mock()
        with patch.object(self.runtime,'reserve_call') as reserve:
            result,_=self.plugin_run(_permission_evidence={'title':'microphone access'},_call_button=(button,False))
            reserve.assert_not_called()
        button.click_input.assert_not_called();self.assertIn('permission_ui_observed=true',result)

    def test_ambiguous_contact_not_reported_as_permission(self):
        result,_=self.plugin_run(_search=[Mock(),Mock()],_open_chat='Multiple results match')
        self.assertIn('state=contact_ambiguous',result)
        self.assertNotIn('permission_ui_observed',result)

    def test_menu_without_option_is_not_claimed_as_call_attempt(self):
        with patch.object(wa,'_call_menu_option',return_value=None):
            result,_=self.plugin_run(_call_button=(Mock(),True))
        self.assertIn('call_initiation_attempted=false',result)
        self.assertIn('options opened',wa.summarize(result))

    def test_click_error_stays_uncertain_and_consumes_cooldown(self):
        button=Mock();button.click_input.side_effect=RuntimeError('synthetic click failure')
        result,_=self.plugin_run(_call_button=(button,False))
        self.assertIn('state=call_attempt_uncertain',result)
        button.click_input.assert_called_once()
        self.assertIn('already attempted',self.runtime.reserve_call('Appa',self.runtime.context()))

    def test_permission_summary_requires_observation_flag(self):
        text='status=failed state=external_permission_required'
        self.assertNotIn('displayed a microphone',wa.summarize(text))
        self.assertIn('displayed a microphone',wa.summarize(text+' permission_ui_observed=true'))
