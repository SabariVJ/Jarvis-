"""Owner-configured free task notification routing in existing settings."""
import json
from core import outcome
from core import local_notifications as notifications
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report

PLUGIN = declaration('local_notifications',
    'Read local task notification routes/history. Configure channels through Plugin Settings. '
    'Only dashboard, Windows desktop and local offline speech are supported; not telephone calls or messaging.',
    {'action': string(choices=['status', 'history'])})
PLUGIN_SETTINGS = {'namespace': 'local_notifications', 'title': 'Free task notifications', 'fields': [
    {'key': 'routes_json', 'label': 'Event routes JSON (dashboard, desktop, speech)', 'type': 'text',
     'placeholder': '{"completed":["dashboard","desktop"],"failed":["dashboard"]}'},
    {'key': 'quiet_hours', 'label': 'Quiet hours (local time; blank disables)', 'type': 'text',
     'placeholder': '22:00-07:00'}]}


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    try:
        rt.check_cancelled(session_memory or rt.context())
        action = parameters.get('action')
        if action == 'status':
            return report('local_notifications', {'routes': notifications.preferences(), 'paid_channels': False,
                'quiet_hours_active': notifications.quiet_now(), 'automatic_retries': False,
                'preview_policy': 'event summary only; details remain in task dashboard'})
        if action == 'history':
            rows = sorted(rt.records('notification').values(), key=lambda r: r['at'], reverse=True)[:30]
            return report('local_notifications', {'notifications': rows})
        raise ValueError('Choose status or history')
    except (ValueError, InterruptedError) as exc:
        return outcome.failed('local_notifications', str(exc))
