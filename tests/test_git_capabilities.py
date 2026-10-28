"""Connectivity is read-only; remote traffic and model responses are mocked."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from core.command_router import git_route
from core.git_capabilities import capability_policy, diagnostic_summary
from core.git_workflow import GitWorkflow
from core.plugin_loader import discover_plugins
from plugins import github_git
from providers.engine_pipeline import PipelineEngine
from test_automation import Isolated


class CapabilityTests(Isolated):
    def test_questions_route_to_real_diagnostic(self):
        for text in ('Jarvis, are you connected to GitHub?', 'is github integrated',
                     'can you use github', 'what github tools do you have'):
            self.assertEqual(git_route(text)[1]['action'], 'connectivity')

    def test_repository_queries_route_readonly(self):
        for text, action in [('show git branches', 'branches'), ('git remotes', 'remotes'),
                             ('show recent commits', 'log'), ('show git status', 'status')]:
            self.assertEqual(git_route(text)[1]['action'], action)
        for text in ('git status and push', 'force push', 'commit everything', 'delete remote'):
            self.assertIsNone(git_route(text))

    def test_publication_intent_routes_to_plan_not_side_effect(self):
        for text in ('git commit', 'git push', 'commit my changes', 'push my changes', 'publish changes to github'):
            self.assertEqual(git_route(text)[1]['action'], 'publication_plan')
        workflow = Mock()
        from threading import RLock
        workflow.lock = RLock()
        workflow.status.return_value = {'branch': 'main'}
        with patch.object(github_git, 'GitWorkflow', return_value=workflow):
            result = github_git.run({'action': 'publication_plan', 'operation': 'push'},
                                    session_memory=self.runtime.context())
        self.assertIn('fresh exact repository/branch/review confirmation', result)
        workflow.push.assert_not_called()
        workflow.commit.assert_not_called()

    def test_discovery_and_enable_state_are_real_evidence(self):
        from pathlib import Path
        with patch('core.plugin_loader.get_plugin_enabled', return_value=True):
            registry = discover_plugins(Path(__file__).resolve().parents[1] / 'plugins', core_tool_names=set(), logger=lambda _: None)
            self.assertIn('github_git', [d['name'] for d in registry.get_tool_declarations()])
        with patch('core.plugin_loader.get_plugin_enabled', return_value=False):
            self.assertNotIn('github_git', [d['name'] for d in registry.get_tool_declarations()])
        self.assertIn('not offered', capability_policy([]))
        self.assertIn('enabled and callable', capability_policy([github_git.PLUGIN]))

    def test_git_tool_survives_small_model_budget(self):
        from providers.schema import budget
        declarations = [{'name': name, 'description': 'test', 'parameters': {}}
                        for name in ('open_app', 'computer_settings', 'computer_control', 'file_controller', 'web_search')]
        declarations.append(github_git.PLUGIN)
        self.assertIn('github_git', [d['name'] for d in budget(declarations, max_tools=4)])

    def diagnostic_workflow(self):
        workflow = object.__new__(GitWorkflow)
        workflow.root = self.root
        workflow.branches = Mock(return_value={'current_branch': 'main', 'detached_head': False})
        workflow.remotes = Mock(return_value={'remotes': 'origin https://github.com/SabariVJ/Jarvis-'})
        workflow.destination = Mock(return_value=('https://github.com/SabariVJ/Jarvis-', 'SabariVJ/Jarvis-'))
        workflow.remote_head = Mock(return_value='a' * 40)
        return workflow

    def test_read_access_is_not_claimed_as_authentication(self):
        workflow = self.diagnostic_workflow()
        result = workflow.connectivity()
        self.assertTrue(result['remote_read_access_verified'])
        self.assertEqual(result['authentication'], 'not_verified')
        self.assertEqual(result['push_permission'], 'not_verified')
        self.assertEqual(result['repository'], 'SabariVJ/Jarvis-')
        workflow.remote_head.assert_called_once_with('https://github.com/SabariVJ/Jarvis-', 'main')

    def test_remote_listing_redacts_legacy_url_credentials_and_queries(self):
        workflow = object.__new__(GitWorkflow)
        workflow.git = Mock(return_value=SimpleNamespace(stdout=
            'origin ssh://username:example-password@github.com/owner/repo (fetch)\n'
            'other https://github.com/owner/repo?credential=example-value (push)'))
        result = workflow.remotes()['remotes']
        self.assertNotIn('example-password', result)
        self.assertNotIn('example-value', result)
        self.assertIn('github.com/owner/repo', result)

    def test_auth_network_and_missing_remote_errors_preserve_capability(self):
        for failure in ('Authentication failed', 'Could not resolve host', 'No such remote origin'):
            workflow = self.diagnostic_workflow()
            workflow.destination.side_effect = ValueError(failure)
            result = workflow.connectivity()
            self.assertTrue(result['git_tool_enabled'])
            self.assertEqual(result['failure'], failure)
            self.assertFalse(result['remote_read_access_verified'])
            workflow.remote_head.assert_not_called()

    def test_remote_probe_failure_is_specific(self):
        workflow = self.diagnostic_workflow()
        workflow.remote_head.side_effect = ValueError('Remote access failed: authentication rejected')
        result = workflow.connectivity()
        self.assertIn('authentication rejected', result['failure'])
        self.assertEqual(result['remote_url'], 'https://github.com/SabariVJ/Jarvis-')

    def test_missing_cli_keeps_specific_failure_in_summary(self):
        with patch.object(github_git, 'GitWorkflow', side_effect=FileNotFoundError('Git CLI executable not found')):
            result = github_git.run({'action': 'connectivity'}, session_memory=self.runtime.context())
        self.assertIn('status=failed', result)
        self.assertIn('Git CLI executable not found', diagnostic_summary(result))
        self.assertIn('could not be verified', diagnostic_summary(result))

    def test_missing_repository_does_not_mean_missing_tool(self):
        with patch.object(github_git, 'GitWorkflow', side_effect=ValueError('This folder is not a Git repository.')):
            result = github_git.run({'action': 'connectivity', 'project': str(self.root)},
                                    session_memory=self.runtime.context())
        data = json.loads(result.split('\n', 1)[1])
        self.assertTrue(data['git_tool_enabled'])
        self.assertFalse(data['local_repository'])
        self.assertIn('CLI integration is enabled', diagnostic_summary(result))

    def test_cancelled_diagnostic_does_not_execute(self):
        context = self.runtime.context()
        self.runtime.finish(context)
        with patch.object(GitWorkflow, 'git') as git:
            result = github_git.run({'action': 'connectivity', 'project': str(self.root)}, session_memory=context)
        git.assert_not_called()
        self.assertIn('status=failed', result)

    def test_misleading_model_cannot_replace_executed_diagnostic(self):
        async def exercise():
            llm = Mock(caps={'tools': True, 'tool_format': 'openai'})
            llm.chat.side_effect = AssertionError('Model would falsely say GitHub is not integrated')
            engine = PipelineEngine(llm, config=SimpleNamespace(system_instruction='Old claim: no integration',
                tools=[{'function_declarations': [github_git.PLUGIN]}]))
            payload = 'status=done\n' + json.dumps(self.diagnostic_workflow().connectivity())
            engine._history = [{'role': 'tool', 'name': 'github_git', 'content': payload}]
            await engine._generate()
            llm.chat.assert_not_called()
            self.assertIn('CLI integration is enabled', engine._history[-1]['content'])
            await engine.close()
        asyncio.run(exercise())

    def test_capability_question_bypasses_misleading_model_even_without_fast_apps(self):
        async def exercise():
            llm = Mock(caps={'tools': True, 'tool_format': 'openai'})
            llm.chat.side_effect = AssertionError('Must route without model guessing')
            engine = PipelineEngine(llm, config=SimpleNamespace(system_instruction='',
                tools=[{'function_declarations': [github_git.PLUGIN]}]), fast_commands=False)
            engine._history = [{'role': 'user', 'content': 'Jarvis, are you connected to GitHub?'}]
            await engine._generate()
            self.assertEqual(list(engine._awaiting.values()), ['github_git'])
            llm.chat.assert_not_called()
            await engine.close()
        asyncio.run(exercise())

    def test_non_diagnostic_and_malformed_output_not_treated_as_connectivity(self):
        self.assertIsNone(diagnostic_summary('status=failed reason=offline'))
        self.assertIsNone(diagnostic_summary('status=done\n{"branch":"main"}'))
