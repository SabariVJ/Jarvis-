"""Dedicated reviewed Git/GitHub operations; credentials stay in Git/GCM/SSH."""
from pathlib import Path
from core import outcome
from core.automation_runtime import get_runtime
from core.git_security import redact
from core.git_workflow import GitWorkflow
from plugins._automation_common import declaration, report, string

PLUGIN = declaration('github_git',
    'Git status/diff/review, explicitly approved staging, tested commits, confirmed GitHub push and remote verification. '
    'Never stages all files. Review exact files, show the review, then wait for the user to say Stage review ID. '
    'Commit runs security checks and staged-snapshot tests. prepare_push shows repository/branch/changes and a confirmation phrase; '
    'push only after a fresh user message exactly confirms that phrase. Never invent confirmation, tokens, remotes or branches.',
    {'action': string(choices=['status', 'diff', 'review', 'stage', 'checks', 'commit', 'prepare_push', 'push', 'verify']),
     'project': string('Absolute Git worktree root; defaults to the installed JARVIS project'),
     'files': {'type': 'ARRAY', 'items': string('Exact project-relative file; no glob')},
     'review_id': string(), 'message': string('Commit message'), 'approval_id': string(),
     'remote': string('Existing remote name, default origin'), 'test_kind': string(choices=['unittest', 'pytest'])})


def run(parameters, player=None, session_memory=None):
    context = session_memory or get_runtime().context()
    try:
        workflow = GitWorkflow(parameters.get('project') or str(Path(__file__).resolve().parents[1]), context)
        action = parameters.get('action')
        with workflow.lock:
            if action == 'status':
                data = workflow.status()
            elif action == 'diff':
                data = workflow.diff(parameters.get('files'))
            elif action == 'review':
                data = workflow.review(parameters.get('files'))
            elif action == 'stage':
                data = workflow.stage(parameters.get('review_id', ''))
            elif action == 'checks':
                data = workflow.checks(parameters.get('review_id', ''), parameters.get('test_kind', 'unittest'))
            elif action == 'commit':
                data = workflow.commit(parameters.get('review_id', ''), parameters.get('message', ''), parameters.get('test_kind', 'unittest'))
            elif action == 'prepare_push':
                data = workflow.prepare_push(parameters.get('remote', 'origin'))
            elif action == 'push':
                data = workflow.push(parameters.get('approval_id', ''))
            elif action == 'verify':
                data = workflow.verify(parameters.get('remote', 'origin'))
            else:
                raise ValueError('Unknown Git action')
            if 'diff' in data:
                diff = data.pop('diff')
                return report('github_git', data) + '\n' + outcome.untrusted('reviewed Git diff', diff)
            if data.get('push_outcome') == 'unverified':
                return outcome.unverified('github_git', 'Push returned but remote commit did not match; do not retry automatically') + '\n' + str(data)
            return report('github_git', data)
    except Exception as exc:
        # An interrupted/uncertain push is never retried; approval stays consumed.
        if parameters.get('action') == 'commit':
            review = get_runtime().get('git_review', parameters.get('review_id', ''), {})
            if review.get('state') == 'committing':
                return outcome.unverified('github_git', 'Commit may have completed; inspect Git status before any new action. '
                                          'No push was approved. ' + redact(str(exc)))
        if parameters.get('action') == 'push':
            approval = get_runtime().get('git_push', parameters.get('approval_id', ''), {})
            if approval.get('state') == 'consumed':
                return outcome.unverified('github_git', 'Push attempt ended without remote verification; do not retry automatically. ' +
                                          redact(str(exc)), commit=approval.get('commit'), branch=approval.get('branch'),
                                          remote=approval.get('remote'), push_outcome='unknown')
        return outcome.failed('github_git', redact(str(exc)))
