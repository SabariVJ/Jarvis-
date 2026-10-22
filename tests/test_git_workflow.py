"""Real temporary Git worktrees and commits; ALL remote operations are mocked."""
import json
import os
from pathlib import Path
import subprocess
import shutil
from unittest.mock import patch

from core import outcome
from core.git_security import inspect_bytes, allowed_path
from core.git_workflow import GitWorkflow, github_url
from plugins import github_git
from test_automation import Isolated


class GitWorkflowTests(Isolated):
    def setUp(self):
        super().setUp()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        empty = self.root / 'global.gitconfig'
        empty.write_text('', encoding='utf-8')
        self.env_patch = patch.dict(os.environ, {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': str(empty),
                            'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never', 'PYTHONIOENCODING': 'utf-8'})
        self.env_patch.start()
        self.raw('-c', 'init.templateDir=', 'init', '-b', 'main')
        self.raw('config', 'user.name', 'Offline JARVIS Test')
        self.raw('config', 'user.email', 'offline@example.invalid')
        self.raw('config', 'commit.gpgsign', 'false')
        self.raw('remote', 'add', 'origin', 'https://github.com/example/jarvis-test.git')
        (self.repo / 'app.py').write_text('VALUE = 7\n', encoding='utf-8')
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests/test_app.py').write_text('import unittest\nfrom app import VALUE\n'
            'class TestApp(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(VALUE, 7)\n', encoding='utf-8')

    def tearDown(self):
        self.env_patch.stop()
        super().tearDown()

    def raw(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def workflow(self, text):
        context = self.runtime.new_turn(text)
        return GitWorkflow(str(self.repo), context)

    def staged(self):
        result = self.workflow('Review app.py and its tests').review(['app.py', 'tests/test_app.py'])
        self.workflow(result['confirmation_required']).stage(result['review_id'])
        return result['review_id']

    def committed(self):
        token = self.staged()
        return self.workflow('Commit the reviewed changes').commit(token, 'Test reviewed application')

    def approval(self):
        commit = self.committed()
        with patch.object(GitWorkflow, 'remote_head', return_value=None):
            approval = self.workflow('Prepare a GitHub push review').prepare_push()
        return commit, approval

    def test_real_local_commit_runs_snapshot_tests_and_returns_hash(self):
        result = self.committed()
        self.assertEqual(result['commit'], self.raw('rev-parse', 'HEAD'))
        self.assertEqual(result['branch'], 'main')
        self.assertEqual(result['push_outcome'], 'not_requested')
        self.assertEqual(self.raw('show', 'HEAD:app.py'), 'VALUE = 7')

    def test_mocked_successful_push_returns_verified_remote_commit(self):
        commit, approval = self.approval()
        workflow = self.workflow(approval['confirmation_required'])
        original = workflow.git
        pushes = []
        def git(*args, **kwargs):
            if 'push' in args:
                pushes.append(args)
                return subprocess.CompletedProcess(args, 0, stdout='ok', stderr='')
            return original(*args, **kwargs)
        with patch.object(workflow, 'git', side_effect=git), patch.object(workflow, 'remote_head', side_effect=[None, commit['commit']]):
            result = workflow.push(approval['approval_id'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['remote_commit'], commit['commit'])
        self.assertEqual(len(pushes), 1)
        self.assertNotIn('--force', pushes[0])
        self.assertNotIn('--mirror', pushes[0])
        self.assertEqual(pushes[0][-1], commit['commit'] + ':refs/heads/main')

    def test_authentication_failure_reports_gcm_setup(self):
        self.committed()
        workflow = self.workflow('Prepare push')
        original = workflow.git
        def git(*args, **kwargs):
            if args[0] == 'ls-remote':
                return subprocess.CompletedProcess(args, 128, stdout='Authentication failed', stderr='')
            return original(*args, **kwargs)
        with patch.object(workflow, 'git', side_effect=git):
            with self.assertRaisesRegex(ValueError, 'git credential-manager github login'):
                workflow.prepare_push()
        self.assertEqual(self.runtime.records('git_push'), {})

    def test_rejected_push_has_no_retry_and_consumes_approval(self):
        _, approval = self.approval()
        workflow = self.workflow(approval['confirmation_required'])
        original, pushes = workflow.git, []
        def git(*args, **kwargs):
            if 'push' in args:
                pushes.append(args)
                return subprocess.CompletedProcess(args, 1, stdout='remote rejected: protected branch', stderr='')
            return original(*args, **kwargs)
        with patch.object(workflow, 'git', side_effect=git), patch.object(workflow, 'remote_head', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Push rejected'):
                workflow.push(approval['approval_id'])
            with self.assertRaisesRegex(ValueError, 'consumed'):
                workflow.push(approval['approval_id'])
        self.assertEqual(len(pushes), 1)

    def test_forged_tool_confirmation_cannot_stage(self):
        review = self.workflow('Review files').review(['app.py'])
        result = github_git.run({'action': 'stage', 'project': str(self.repo), 'review_id': review['review_id'],
                                 'confirmed': True}, session_memory=self.runtime.context())
        self.assertIn('Fresh user confirmation', result)
        self.assertEqual(self.raw('diff', '--cached', '--name-only'), '')

    def test_secret_paths_and_values_are_blocked_without_echo(self):
        for name in ['.env', 'config/api_keys.json', 'memory/automation.db', 'memory/private.json',
                     'upgrade-backups/main.py', '.ssh/id_rsa']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                allowed_path(name)
        value = b'gh' + b'p_' + b'A' * 36
        with self.assertRaises(ValueError) as found:
            inspect_bytes('app.py', b'TOKEN = ' + repr(value).encode())
        self.assertNotIn(value.decode(), str(found.exception))

    def test_changed_after_review_is_not_staged(self):
        review = self.workflow('Review files').review(['app.py'])
        (self.repo / 'app.py').write_text('VALUE = 9\n')
        with self.assertRaisesRegex(ValueError, 'changed after review'):
            self.workflow(review['confirmation_required']).stage(review['review_id'])
        self.assertEqual(self.raw('diff', '--cached', '--name-only'), '')

    def test_existing_staged_content_is_not_overwritten(self):
        self.raw('add', 'app.py')
        with self.assertRaisesRegex(ValueError, 'already contains staged'):
            self.workflow('Review tests').review(['tests/test_app.py'])
        self.assertEqual(self.raw('diff', '--cached', '--name-only'), 'app.py')

    def test_extra_staged_private_data_blocks_commit(self):
        token = self.staged()
        (self.repo / '.env').write_text('PRIVATE=some-data\n')
        self.raw('add', '.env')
        with self.assertRaisesRegex(ValueError, 'Staged paths'):
            self.workflow('Commit reviewed changes').commit(token, 'Must not commit')
        self.assertNotEqual(self.raw('status', '--porcelain'), '')

    def test_failing_tests_block_commit(self):
        (self.repo / 'tests/test_app.py').write_text('import unittest\nclass TestApp(unittest.TestCase):\n'
            '    def test_failure(self):\n        self.fail("intentional failure")\n')
        token = self.staged()
        with self.assertRaisesRegex(ValueError, 'tests failed'):
            self.workflow('Commit changes').commit(token, 'Must not commit')
        self.assertNotEqual(self.raw('status', '--porcelain'), '')

    def test_cancelled_staging_cannot_change_index(self):
        review = self.workflow('Review files').review(['app.py'])
        workflow = self.workflow(review['confirmation_required'])
        self.runtime.cancel()
        with self.assertRaises(InterruptedError):
            workflow.stage(review['review_id'])
        self.assertEqual(self.raw('diff', '--cached', '--name-only'), '')

    def test_cancelled_push_attempt_is_not_automatically_replayed(self):
        _, approval = self.approval()
        workflow = self.workflow(approval['confirmation_required'])
        original, pushes = workflow.git, []
        def git(*args, **kwargs):
            if 'push' in args:
                pushes.append(args)
                self.runtime.cancel()
                raise InterruptedError('Cancelled after dispatch; remote outcome uncertain')
            return original(*args, **kwargs)
        with patch.object(workflow, 'git', side_effect=git), patch.object(workflow, 'remote_head', return_value=None):
            with self.assertRaises(InterruptedError):
                workflow.push(approval['approval_id'])
        new = self.workflow(approval['confirmation_required'])
        with self.assertRaisesRegex(ValueError, 'consumed'):
            new.push(approval['approval_id'])
        self.assertEqual(len(pushes), 1)

    def test_changed_remote_invalidates_confirmation(self):
        _, approval = self.approval()
        self.raw('remote', 'set-url', 'origin', 'https://github.com/example/different.git')
        workflow = self.workflow(approval['confirmation_required'])
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            workflow.push(approval['approval_id'])

    def test_changed_branch_or_commit_invalidates_confirmation(self):
        _, approval = self.approval()
        self.raw('checkout', '-b', 'another')
        workflow = self.workflow(approval['confirmation_required'])
        with self.assertRaisesRegex(ValueError, 'branch or staged changes changed'):
            workflow.push(approval['approval_id'])

    def test_confirmation_names_repository_branch_and_changes(self):
        _, approval = self.approval()
        self.assertIn('example/jarvis-test', approval['confirmation_required'])
        self.assertIn('branch main', approval['confirmation_required'])
        self.assertTrue(approval['changes_digest'])
        with self.assertRaisesRegex(ValueError, 'Fresh user confirmation'):
            self.workflow('Yes push it').push(approval['approval_id'])

    def test_replay_receipt_prevents_second_push_dispatch(self):
        _, approval = self.approval()
        workflow = self.workflow(approval['confirmation_required'])
        args = {'action': 'push', 'approval_id': approval['approval_id']}
        calls = []
        def invoke():
            calls.append(1)
            return outcome.done('github_git', commit='mocked', verified=True)
        context = self.runtime.context('first')
        first = self.runtime.execute('github_git', args, invoke, context)
        second = self.runtime.execute('github_git', args, invoke, {**context, 'request_id': 'reconnect-id'})
        self.assertEqual(first, second)
        self.assertEqual(calls, [1])
        self.runtime.finish(context)
        with self.assertRaises(InterruptedError):
            self.runtime.execute('github_git', args, invoke, context)

    def test_unreviewed_extra_history_cannot_be_pushed(self):
        self.committed()
        with patch.object(GitWorkflow, 'remote_head', return_value='a' * 40):
            with self.assertRaisesRegex(ValueError, 'additional history'):
                self.workflow('Prepare push').prepare_push()

    def test_url_credentials_non_github_hosts_and_options_are_rejected(self):
        credential_url = 'https://' + 'name:password' + '@github.com/a/b.git'
        for url in [credential_url, 'https://example.com/a/b', 'file:///tmp/repo',
                    'https://github.com/a/b?token=value', 'ssh://other@github.com/a/b']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                github_url(url)

    def test_url_rewriting_and_mirror_configuration_are_rejected(self):
        self.raw('config', 'url.https://example.invalid/.insteadOf', 'https://github.com/')
        with self.assertRaisesRegex(ValueError, 'URL rewriting'):
            self.workflow('Prepare push').destination('origin')

    def test_secret_is_found_in_staged_blob_even_if_worktree_was_cleaned(self):
        value = 'sk-' + 'x' * 30
        (self.repo / 'app.py').write_text(('TO' + 'KEN') + ' = ' + repr(value) + '\n')
        self.raw('add', 'app.py')
        (self.repo / 'app.py').write_text('VALUE = 7\n')
        with self.assertRaisesRegex(ValueError, 'secret detected'):
            self.workflow('Check staged files').scan_index()

    def test_missing_repository_reports_setup_without_creating_one(self):
        empty = self.root / 'not-a-repo'
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, 'git init -b'):
            GitWorkflow(str(empty), self.runtime.context())
        self.assertFalse((empty / '.git').exists())

    def test_ignore_rules_protect_keys_database_and_backups(self):
        shutil.copy2(Path(__file__).resolve().parents[1] / '.gitignore', self.repo / '.gitignore')
        for name in ('config/api_keys.json', '.env', 'memory/automation.db', 'upgrade-backups/main.py',
                     'memory/git-reviews/one.diff', 'token_google.json', 'client_secret_google.json'):
            self.assertEqual(self.raw('check-ignore', '--no-index', name), name)

    def test_file_race_cannot_stage_unreviewed_blob(self):
        review = self.workflow('Review app.py').review(['app.py'])
        workflow = self.workflow(review['confirmation_required'])
        original = workflow.git
        def git(*args, **kwargs):
            if args[0] == 'hash-object' and '-w' in args:
                (self.repo / 'app.py').write_text('VALUE = 99\n')
            return original(*args, **kwargs)
        with patch.object(workflow, 'git', side_effect=git):
            with self.assertRaisesRegex(ValueError, 'changed while staging'):
                workflow.stage(review['review_id'])
        self.assertEqual(self.raw('diff', '--cached', '--name-only'), '')

    def test_execute_mode_change_invalidates_review(self):
        token = self.staged()
        self.raw('update-index', '--chmod=+x', 'app.py')
        with self.assertRaisesRegex(ValueError, 'Staged content changed'):
            self.workflow('Commit reviewed changes').commit(token, 'Must not commit')

    def test_snapshot_tests_do_not_receive_api_environment_credentials(self):
        source = self.repo / 'tests/test_app.py'
        source.write_text('import os, unittest\nclass TestApp(unittest.TestCase):\n'
                          '    def test_no_credentials(self):\n        self.assertNotIn("EXAMPLE_API_KEY", os.environ)\n')
        test_key = 'EXAMPLE_' + 'API_KEY'
        with patch.dict(os.environ, {test_key: 'sensitive-test-value'}):
            self.assertEqual(self.committed()['push_outcome'], 'not_requested')

    def test_remote_mirror_config_is_rejected(self):
        self.raw('config', 'remote.origin.mirror', 'true')
        with self.assertRaisesRegex(ValueError, 'Mirror'):
            self.workflow('Prepare push').destination('origin')

    def test_missing_tests_blocks_commit(self):
        review = self.workflow('Review app.py only').review(['app.py'])
        self.workflow(review['confirmation_required']).stage(review['review_id'])
        with self.assertRaisesRegex(ValueError, 'No tests available'):
            self.workflow('Commit reviewed code').commit(review['review_id'], 'Must not commit')

    def test_installed_but_unused_lfs_filter_does_not_block_source_review(self):
        self.raw('config', 'filter.lfs.clean', 'THIS-COMMAND-MUST-NOT-RUN')
        result = self.workflow('Review app.py').review(['app.py'])
        self.assertEqual(result['changes'][0]['file'], 'app.py')

    def test_active_filter_is_refused_before_it_can_execute(self):
        self.raw('config', 'filter.lfs.clean', 'THIS-COMMAND-MUST-NOT-RUN')
        (self.repo / '.gitattributes').write_text('*.py filter=lfs\n')
        with self.assertRaisesRegex(ValueError, 'Active Git content filters'):
            self.workflow('Review app.py').review(['app.py'])

    def test_unquoted_yaml_credentials_are_not_displayed_or_staged(self):
        (self.repo / 'settings.yaml').write_text('api_key: ' + 'private-' + 'fixture-value\n')
        with self.assertRaisesRegex(ValueError, 'YAML credential'):
            self.workflow('Review settings').review(['settings.yaml'])
        self.assertEqual(self.raw('diff', '--cached', '--name-only'), '')

    def test_changed_review_artifact_invalidates_staging_confirmation(self):
        review = self.workflow('Review app.py').review(['app.py'])
        Path(review['review_file']).write_text('Changed review text\n')
        with self.assertRaisesRegex(ValueError, 'review artifact changed'):
            self.workflow(review['confirmation_required']).stage(review['review_id'])
