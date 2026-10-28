"""Bounded evidence, never raw session events, account files or environment."""
import re


def sanitize(value, limit=4096):
    text = str(value)[:65536]
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'(?im)^.*\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=].*$', '[REDACTED authentication header]', text)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[^\s,;]+', '[REDACTED]', text)
    text = re.sub(r'''(?ix)(["']?(?:access_token|refresh_token|id_token|api_key|password|passwd|secret|token|thread_id|session_id)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b', '[REDACTED]', text)
    text = re.sub(r'https?://[^\s<>"\']+', '[URL REDACTED]', text)
    text = re.sub(r'(?i)[^\s"\']*[/\\]\.codex[/\\][^\s"\']*', '[CODEX PRIVATE PATH]', text)
    text = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32).replace('\x7f', '')
    if len(text) > limit:
        text = text[:limit // 2] + '\n[bounded output truncated]\n' + text[-limit // 2:]
    return text


def category(value):
    text = str(value).casefold()
    if any(x in text for x in ('unexpected argument', 'unrecognized argument', 'unknown option', 'invalid value', 'unknown field', 'unknown variant', 'strict-config', 'unsupported feature', 'configuration', 'error loading config')):
        return 'cli_configuration'
    if any(x in text for x in ('usage_limit', 'rate_limit', 'quota', 'limit reached', 'usage limit', '429')):
        return 'usage_limit'
    if any(x in text for x in ('unauthorized', 'not logged', 'authentication', '401', 'login required')):
        return 'authentication'
    if any(x in text for x in ('sandbox', 'mxc', 'setup refresh', 'restricted token')):
        return 'windows_sandbox'
    if any(x in text for x in ('network', 'connection', 'dns', 'timed out')):
        return 'network'
    return 'runtime_failure'


def event_metadata(data):
    item = data.get('item') if isinstance(data.get('item'), dict) else {}
    types = {'thread.started', 'turn.started', 'turn.completed', 'turn.failed', 'turn.interrupted',
             'turn.cancelled', 'turn.canceled', 'item.started', 'item.updated', 'item.completed', 'error', 'diagnostic'}
    items = {'agent_message', 'reasoning', 'command_execution', 'file_change', 'mcp_tool_call',
             'web_search', 'todo_list', 'plan', 'error'}
    statuses = {'in_progress', 'completed', 'failed', 'interrupted', 'cancelled', 'canceled', 'incomplete'}
    result = {'type': data.get('type') if data.get('type') in types else 'unknown'}
    if item.get('type'):
        result['item_type'] = item['type'] if item['type'] in items else 'unknown'
    if item.get('status'):
        result['item_status'] = item['status'] if item['status'] in statuses else 'unknown'
    if data.get('type') in {'error', 'turn.failed', 'turn.interrupted', 'turn.cancelled', 'diagnostic'}:
        detail = data.get('error') or data.get('message') or ''
        if isinstance(detail, dict):
            detail = str(detail.get('code', '')) + ': ' + str(detail.get('message', ''))
        result['message'] = sanitize(detail, 1200)
    return result


def safe_command(args, workspace, temp):
    if len(args) < 2 or args[1] != 'exec':
        return [sanitize(args[0]), '<non-Codex fixture arguments omitted>'] if args else []
    result = []
    for arg in args:
        text = str(arg).replace(str(workspace), '<WORKSPACE>').replace(str(temp), '<TEMP>')
        # The command contains policy settings only. Never record stdin/goal.
        result.append(sanitize(text, 1000))
    return result


def summary(diag):
    code = diag.get('returncode')
    message = f"Codex {diag.get('failure_category') or 'completed'}; phase={diag.get('phase', 'unknown')}; exit={code if code is not None else 'not available'}."
    detail = diag.get('failure_detail') or diag.get('stderr') or diag.get('stdout_diagnostic')
    if detail:
        message += ' ' + sanitize(detail, 1400)
    if diag.get('failure_category') == 'usage_limit':
        message += ' No paid API fallback or retry.'
    if not diag.get('completion_verified'):
        message += ' Completion not verified; no automatic replay.'
    return sanitize(message, 2200)
