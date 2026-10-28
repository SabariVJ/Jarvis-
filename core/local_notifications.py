"""Opt-in local-only notifications. No calls, messages, model or paid services."""
import json
import hashlib
import re
import time
from core.automation_runtime import get_runtime
from memory.config_manager import get_plugin_config, get_plugin_enabled

EVENTS = {'started', 'progress', 'completed', 'failed', 'cancelled', 'needs_attention'}
CHANNELS = {'dashboard', 'desktop', 'speech'}


def quiet_now(now=None):
    """Local wall-clock quiet hours; an empty setting preserves existing behavior."""
    value = str(get_plugin_config('local_notifications').get('quiet_hours') or '').strip()
    if not value:
        return False
    match = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)', value)
    if not match:
        raise ValueError('Quiet hours must be HH:MM-HH:MM in local time')
    start, end = int(match[1])*60+int(match[2]), int(match[3])*60+int(match[4])
    if start == end:
        raise ValueError('Quiet hours must have different start and end times')
    local = time.localtime(time.time() if now is None else now)
    minute = local.tm_hour*60+local.tm_min
    return start <= minute < end if start < end else minute >= start or minute < end


def preview(event, verified, has_result):
    # Never send caller-provided text, source, paths or exception bodies to previews.
    text = {'started':'Task started.', 'progress':'Task progress updated.',
            'completed':'Task completed with a verified result.', 'failed':'Task failed.',
            'cancelled':'Task cancelled.', 'needs_attention':'Task needs review; completion is not verified.'}[event]
    if has_result:
        text += ' Results: available in the task dashboard.'
    return text


def preferences():
    raw = get_plugin_config('local_notifications').get('routes_json')
    routes = json.loads(raw) if raw else {}
    if not isinstance(routes, dict) or any(key not in EVENTS or not isinstance(value, list)
            or any(not isinstance(channel, str) or channel not in CHANNELS for channel in value)
            or len(value) != len(set(value))
            for key, value in routes.items()):
        raise ValueError('Notification routes must map supported events to dashboard, desktop and/or speech lists')
    return {event: routes.get(event, ['dashboard']) for event in sorted(EVENTS)}


def desktop(title, message):
    # Existing optional dependency; no installation, external transport or
    # broadcast "msg *" fallback. Successful submit is not proof user saw it.
    from plyer import notification
    notification.notify(title=title, message=message, app_name='JARVIS', timeout=10)


def emit(task_id, event, message, *, verified=False, result_path=None, rt=None, sequence=None):
    rt = rt or get_runtime()
    if event not in EVENTS:
        raise ValueError('Unsupported local notification event')
    if event == 'completed' and not verified:
        event = 'needs_attention'
        message = 'Task returned without a verified completion: ' + str(message)
    message = preview(event, verified, bool(result_path))
    try:
        routes = preferences()
        route_error = None
    except ValueError:
        routes = {kind: ['dashboard'] for kind in EVENTS}
        route_error = 'Invalid channel preferences; dashboard fallback only'
    try:
        quiet = quiet_now()
    except ValueError:
        quiet = True
        route_error = 'Invalid quiet hours; intrusive channels suppressed'
    identity = 'v2:' + hashlib.sha256(json.dumps([str(task_id), event, sequence],
        ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    record = {'task_id': task_id, 'event': event, 'message': message, 'verified': bool(verified),
              'result_path': str(result_path) if result_path else None, 'at': time.time(), 'channels': {}}
    record['delivery_state'] = 'reserved'
    record['quiet_hours_active'] = quiet
    if route_error:
        record['route_error'] = route_error
    if not get_plugin_enabled('local_notifications'):
        routes[event] = []
        record['delivery_disabled'] = True
    # Persist before effects. Interrupted delivery is never repeated automatically.
    with rt.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', ('notification', identity)).fetchone()
        if row:
            return json.loads(row[0])
        # Match stored fields as well as the old composite key; task IDs may contain colons.
        legacy = f'{task_id}:{event}:{sequence or "once"}'
        row = db.execute('SELECT data FROM records WHERE kind=? AND id=?', ('notification', legacy)).fetchone()
        if row:
            old = json.loads(row[0])
            if old.get('task_id') == task_id and old.get('event') == event:
                return old
        db.execute('INSERT INTO records VALUES (?,?,?)', ('notification', identity, json.dumps(record)))
    for channel in routes[event]:
        if quiet and channel in {'desktop', 'speech'}:
            record['channels'][channel] = 'suppressed_quiet_hours'
            rt.put('notification', identity, record)
            continue
        record['channels'][channel] = 'dispatching_outcome_unverified'
        rt.put('notification', identity, record)
        try:
            if channel == 'desktop':
                callback = getattr(rt, 'desktop_notify', None)
                if callable(callback):
                    result = callback('JARVIS task ' + event, message)
                    record['channels'][channel] = ('deferred_or_unavailable' if result is False else
                        result if result in ('queued_to_ui; visibility_not_verified', 'submitted_to_windows') else
                        'submitted_to_callback; visibility_not_verified')
                else:
                    desktop('JARVIS task ' + event, message)
                    record['channels'][channel] = 'submitted_to_windows'
            else:
                callback = getattr(rt, 'dashboard_notify' if channel == 'dashboard' else 'local_speech', None)
                if not callable(callback):
                    record['channels'][channel] = 'unavailable'
                else:
                    result = callback(message)
                    record['channels'][channel] = ('deferred_or_unavailable' if result is False else
                                                   'announced_locally' if channel == 'speech' else 'posted_to_dashboard')
        except Exception as exc:
            record['channels'][channel] = 'failed:' + type(exc).__name__
        rt.put('notification', identity, record)
    record['delivery_state'] = 'attempts_recorded'
    rt.put('notification', identity, record)
    return record
