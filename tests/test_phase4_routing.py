import json
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from core import outcome
from core.command_router import resolve, recognize
from core.performance_report import distribution, performance_report
from plugins import github_git, whatsapp_control, automation_status, task_manager
from actions.open_app import TOOL as OPEN
from actions.computer_control import TOOL as CONTROL
from test_automation import Isolated

CATALOG = [OPEN, CONTROL, github_git.PLUGIN, whatsapp_control.PLUGIN, automation_status.PLUGIN, task_manager.PLUGIN]


class RoutingTests(Isolated):
    def test_enabled_inventory_and_schema_are_authoritative(self):
        self.assertIsNone(resolve('open Chrome', []))
        bad = {**OPEN, 'parameters': {'properties': {'different': {}}, 'required': ['different']}}
        self.assertIsNone(resolve('open Chrome', [bad]))
        self.assertEqual(resolve('open Chrome', CATALOG)[0][0], 'open_app')

    def test_supported_git_whatsapp_and_automation(self):
        for text, tool, action in [('show git status', 'github_git', 'status'),
                ('open chat with Appa on WhatsApp', 'whatsapp_control', 'open_chat'),
                ('voice call Ganesh on WhatsApp', 'whatsapp_control', 'voice_call'),
                ('find Ganesh on WhatsApp', 'whatsapp_control', 'search'),
                ('show enabled plugins', 'automation_status', 'capabilities'),
                ('list workflows', 'task_manager', 'list')]:
            routed = resolve(text, CATALOG)
            self.assertEqual((routed[0][0], routed[0][1]['action']), (tool, action))

    def test_ambiguous_contacts_and_compound_requests_fall_back(self):
        for text in ('call Ganesh', 'call Ganesh on WhatsApp and open Chrome',
                     'find Appa or Ganesh on WhatsApp', 'open Chrome and go to YouTube', 'open unknown software'):
            self.assertIsNone(resolve(text, CATALOG))

    def test_no_publication_shortcut_bypasses_approval(self):
        for text in ('git push', 'git commit'):
            self.assertEqual(resolve(text, CATALOG)[0][1]['action'], 'publication_plan')

    def test_fresh_inventory_is_read_on_each_capability_query(self):
        self.runtime.catalog_provider = Mock(side_effect=[[OPEN], []])
        first = automation_status.run({'action': 'capabilities'})
        second = automation_status.run({'action': 'capabilities'})
        self.assertIn('open_app', first)
        self.assertNotIn('open_app', second)

    def test_failed_foreground_step_blocks_dependency(self):
        context = self.runtime.new_turn('close WhatsApp and open Chrome')
        steps = recognize(context['user_text'], CATALOG, self.runtime, context)
        self.runtime.execute(*steps[0], lambda: outcome.unverified('close_app', 'Window still present'), context)
        launch = Mock(return_value=outcome.done('open_app'))
        result = self.runtime.execute(*steps[1], launch, context)
        launch.assert_not_called()
        self.assertIn('preceding step was not verified', result)

    def test_router_does_not_restore_completed_execution(self):
        context = self.runtime.context()
        self.runtime.finish(context)
        with self.assertRaises(InterruptedError):
            recognize('open Chrome', CATALOG, self.runtime, context)

    def test_routed_call_still_requires_fresh_explicit_request(self):
        context = self.runtime.new_turn('show git status')
        with self.assertRaises(InterruptedError):
            self.runtime.authorize('whatsapp_control', {'action': 'voice_call', 'contact': 'Ganesh'}, context)

    def test_routed_video_reservation_preserves_mode_and_cooldown(self):
        context = self.runtime.new_turn('video call Ganesh on WhatsApp')
        recognize(context['user_text'], CATALOG, self.runtime, context)
        self.assertIsNone(self.runtime.reserve_call('ganesh', context))
        self.assertIsNotNone(self.runtime.reserve_call('ganesh', context))

    def test_explicit_unverified_fact_does_not_satisfy_dependency(self):
        self.assertFalse(outcome.is_verified(outcome.done('test', verified=False)))

    def test_local_stt_promotes_enabled_tool_without_exceeding_budget(self):
        from providers.engine_pipeline import PipelineEngine
        async def exercise():
            for request, name in [('find Ganesh on WhatsApp', 'whatsapp_control'),
                                  ('show enabled plugins', 'automation_status')]:
                model = Mock(caps={'tools': True, 'tool_format': 'openai', 'max_tools': 1})
                model.chat.side_effect = AssertionError('Unambiguous enabled tool needs no model planning')
                engine = PipelineEngine(model, config=SimpleNamespace(system_instruction='',
                    tools=[{'function_declarations': [OPEN, whatsapp_control.PLUGIN]}]), fast_commands=True)
                self.runtime.catalog_provider = lambda: CATALOG
                engine._runtime = self.runtime
                engine._command_context = self.runtime.new_turn(request)
                engine._history = [{'role': 'user', 'content': request}]
                await engine._generate(context=engine._command_context)
                self.assertEqual(engine.tool_names(), [name])
                self.assertEqual(list(engine._awaiting.values()), [name])
                model.chat.assert_not_called()
                await engine.close()
        asyncio.run(exercise())

    def test_metrics_have_samples_and_do_not_invent_percentiles(self):
        self.assertIsNone(distribution([1, 2, 3])['p95_seconds'])
        self.assertEqual(distribution(list(range(1, 21)))['p95_seconds'], 19)
        self.assertEqual(performance_report(self.root / 'missing.db')['measurements'], {})
        context = self.runtime.new_turn('show git status')
        recognize(context['user_text'], CATALOG, self.runtime, context)
        self.runtime.execute('github_git', {'action': 'status', 'project': str(__import__('pathlib').Path(__file__).resolve().parents[1])},
                             lambda: outcome.done('github_git'), context)
        self.runtime.report_outcome(context, 'test')
        metrics = performance_report(self.runtime.path)['measurements']
        self.assertIn('inclusive_phase:intent_recognition', metrics)
        self.assertIn('command_to_verified_report:test', metrics)
