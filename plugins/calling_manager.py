"""Telephone provider foundation; distinct from working WhatsApp automation."""
from core import outcome
from core import calling_manager as manager
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report

PLUGIN = declaration('calling_manager',
    'Inspect actual telephone configuration/history or check provider caller ID. Opt-in phone TTS notifications only: '
    'call NAME on phone and say "MESSAGE". No two-way audio, incoming answering or schedules yet. '
    'Telephone-only diagnostics, NOT general calling capabilities. WhatsApp uses whatsapp_control and remains separate. '
    'Use calling_assistant for can you call someone/video and channel clarification. Desktop microphone status is separate. '
    'Never infer call permission or claim connected without evidence.',
    {'action': string(choices=['status', 'contacts', 'history', 'connectivity', 'notify', 'refresh']),
     'recipient': string('Exact authorized name, my phone, or allowlisted E.164 number'),
     'message': string('Exact message explicitly supplied in current command'), 'id': string('Local acknowledged call ID')})
PLUGIN['tier'] = 'core'
PLUGIN_SETTINGS = {'namespace': 'calling_manager', 'title': 'Calling (disabled until approved)', 'fields': [
    {'key': 'provider', 'label': 'Provider (twilio; evaluate local regulations first)', 'type': 'text'},
    {'key': 'caller_id', 'label': 'Provider-owned/verified E.164 caller ID', 'type': 'text'},
    {'key': 'contacts_json', 'label': 'Authorized contacts JSON (name, number, authorized, owner)', 'type': 'text'}]}


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    context = session_memory or rt.context()
    try:
        rt.check_cancelled(context)
        action = parameters.get('action')
        if action == 'notify':
            data = manager.notify(parameters, context, rt)
            if not data.get('verified'):
                return outcome.failed('calling_manager', data.get('failure', 'Call outcome uncertain; do not retry'))
            return report('calling_manager', data)
        if action == 'refresh':
            data = manager.refresh(parameters.get('id'), rt)
            return report('calling_manager', data)
        data = manager.snapshot(rt)
        if action == 'connectivity':
            config = manager.configuration()
            backend = manager.provider(config)
            backend.verify_caller(config.get('caller_id'))
            data.update(remote_access='verified_read_only', caller_id_verification='verified_at_provider')
        elif action not in {'status', 'contacts', 'history'}:
            raise ValueError('Unknown calling action')
        return report('calling_manager', data)
    except (ValueError, InterruptedError) as exc:
        return outcome.failed('calling_manager', str(exc))
    except Exception as exc:
        return outcome.failed('calling_manager', manager.safe_failure(exc))


def summarize(payload):
    import json
    if str(payload).startswith('status=failed '):
        return str(payload)
    try:
        data = json.loads(str(payload).split('\n', 1)[1])
        if 'provider' not in data:
            return None
        if 'state' in data:
            return 'Telephone notification state: ' + data['state'] + '. This is not a two-way conversation.'
        return ('Telephone provider: ' + str(data['provider']) + '. Remote access: ' + data['remote_access'] +
                '. Outgoing notifications are ' + ('enabled' if data['outgoing_enabled'] else 'disabled') +
                '. This describes telephone-provider calls only, not WhatsApp availability. '
                'Use calling_assistant to inspect enabled WhatsApp voice/video controls. '
                'Telephone audio is not the desktop microphone; no microphone failure was established here.')
    except (ValueError, KeyError, IndexError):
        return None
