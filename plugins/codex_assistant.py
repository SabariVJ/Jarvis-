"""Truthful official Codex CLI account awareness; no account mutation."""
from pathlib import Path
from core import codex_bridge, outcome
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report

PLUGIN = declaration('codex_assistant',
    'Official Codex ChatGPT-only supervised coding. Explicit read-only analysis can use MXC only after fresh signed-binary, scope, network, broker and cancellation checks. Command execution is disabled in that mode; normal coding remains blocked. '
    'Check Codex sandbox ID is a read-only preflight, not inference. Ask Codex to GOAL naming source paths to prepare; '
    'run Codex task ID explicitly approves execution in a source-only copy. Review proposals before separate apply approval. '
    'Progress/status/cancel use existing jobs. Never replay, use paid API fallback, commit or push. '
    'A stored login does not prove available quota. Sensitive changes require manual supervised application.',
    {'action': string(choices=['status', 'plan', 'prepare', 'run', 'task_status', 'preflight', 'review', 'cancel', 'apply']),
     'project': string('Installed JARVIS project folder'), 'goal': string('Explicit coding goal with project-relative source paths'),
     'id': string('Local Codex task ID'), 'review': string('Exact reviewed proposal SHA-256')})
PLUGIN['tier'] = 'core'


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    context = session_memory or rt.context()
    try:
        rt.check_cancelled(context)
        if parameters.get('action') == 'status':
            return report('codex_assistant', codex_bridge.diagnostic(context))
        if parameters.get('action') == 'plan':
            return report('codex_assistant', codex_bridge.review_plan(parameters.get('project') or str(codex_bridge.PROJECT), parameters.get('goal')))
        from core import codex_tasks
        action = parameters.get('action')
        if action == 'prepare':
            return report('codex_assistant', codex_tasks.prepare(parameters, context, rt))
        if action == 'run':
            return report('codex_assistant', codex_tasks.run(parameters, context, rt))
        if action == 'apply':
            return report('codex_assistant', codex_tasks.apply(parameters, context, rt))
        job = codex_tasks.task(parameters.get('id'), rt)
        if action == 'preflight':
            from core.codex_preflight import inspect, blocked_view
            codex_tasks.fresh(parameters, context, rt)
            check = inspect(job, context, rt)
            if check.get('ready'):
                return report('codex_assistant', {**job, 'preflight': check, 'message': check['safe_next_action']})
            return report('codex_assistant', blocked_view(job, check))
        if action == 'task_status':
            return report('codex_assistant', codex_tasks.task_status(job['id'], rt))
        if action == 'review':
            if job.get('state') not in {'review_required', 'completed'}:
                raise ValueError('Wait for verified Codex completion before reviewing changes')
            proposal = codex_tasks.inspect_proposal(job)
            return report('codex_assistant', {**proposal, 'id': job['id'], 'state': job['state'],
                          'result_path': job.get('result_path'), 'message': 'Review proposal.patch and exact changes before applying. Sensitive changes need manual application.'})
        if action == 'cancel':
            codex_tasks.fresh(parameters, context, rt)
            if job['state'] in {'prepared', 'preparing'}:
                return report('codex_assistant', rt.update('job', job['id'], {'state': 'cancelled', 'cancel_requested': True}))
            return report('codex_assistant', rt.update('job', job['id'], {'cancel_requested': True}))
        raise ValueError('Unknown Codex action')
    except (ValueError, InterruptedError) as exc:
        return outcome.failed('codex_assistant', str(exc))


def summarize(payload):
    import json
    if str(payload).startswith('status=failed '):
        return str(payload)
    try:
        data = json.loads(str(payload).split('\n', 1)[1])
        if data.get('kind') == 'codex' or 'review' in data:
            if data.get('kind') == 'codex' and data.get('state') in {'queued', 'running'}:
                from core import codex_tasks
                from core.codex_preflight import terminal_view
                projected = terminal_view(data)
                if projected.get('state') not in {'queued', 'running'}:
                    data = projected
                else:
                    try:
                        data = codex_tasks.task_status(data['id'], get_runtime())
                    except (ValueError, KeyError):
                        data = projected
            return ('Codex task ' + str(data.get('id', '')) + ': ' + str(data.get('state', 'review')) + '. ' +
                    str(data.get('error') or data.get('message') or 'Inspect task status for verified progress.') +
                    (' Safe next action: ' + data['preflight']['safe_next_action'] if data.get('preflight') else '') +
                    (' Diagnostics: ' + data['diagnostic_summary'] if data.get('diagnostic_summary') else '') +
                    (' Evidence: ' + data['diagnostic_path'] if data.get('diagnostic_path') else '') +
                    (' Results: ' + data['result_path'] if data.get('result_path') else ''))
        if 'executed' in data:
            return data['message']
        if 'installed' not in data:
            return None
        return data['message'] + ' ' + data.get('billing', '')
    except (ValueError, KeyError, IndexError):
        return None
