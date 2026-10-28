"""Explicit wake settings in the existing store; no background configuration."""
import json
from core import outcome
from core.automation_runtime import get_runtime
from core.wake_phrases import normalize, settings_command
from memory.config_manager import get_wake_config, get_wake_word_enabled, save_wake_config
from plugins._automation_common import declaration, report, string

PLUGIN = declaration('wake_settings',
    'Get the actual local wake phrase/fallback or change it on a fresh explicit request. Custom wake phrases ARE supported. '
    'Never search the web for wake configuration. Changes persist in existing settings; Hey Jarvis is an optional fallback. '
    'Sleeping audio stays local. Never invent a requested phrase or alter other settings.',
    {'action': string(choices=['get', 'set']), 'phrase': string('Exact phrase explicitly requested'),
     'fallback_enabled': {'type': 'BOOLEAN'}})
PLUGIN['tier'] = 'core'


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    context = session_memory or rt.context()
    try:
        rt.check_cancelled(context)
        action = parameters.get('action')
        if action == 'set':
            requested = settings_command(context.get('user_text', ''))
            if context.get('lifecycle') == 'job' or not requested or requested.get('action') != 'set':
                raise ValueError('A fresh explicit wake-settings command is required')
            if ('phrase' in parameters and ('phrase' not in requested or
                    normalize(parameters['phrase']) != normalize(requested['phrase']))):
                raise ValueError('The proposed phrase differs from the actual user request')
            if ('fallback_enabled' in parameters and ('fallback_enabled' not in requested or
                    type(parameters['fallback_enabled']) is not bool or parameters['fallback_enabled'] != requested['fallback_enabled'])):
                raise ValueError('Fallback was not explicitly requested')
            current = get_wake_config()
            phrase = requested.get('phrase', current['phrase'])
            fallback = requested.get('fallback_enabled', current['fallback_enabled'])
            with rt.side_effect('wake_settings_update', context):
                callback = getattr(rt, 'configure_wake', None)
                if callable(callback):
                    callback(phrase, fallback)
                else:
                    save_wake_config(phrase, fallback)
        elif action != 'get':
            raise ValueError('Choose get or set')
        data = get_wake_config()
        data.update(enabled=get_wake_word_enabled(), updated=action == 'set',
                    note='Custom phrases are supported. Enable WAKE WORD to use local wake detection.')
        if callable(getattr(rt, 'wake_state', None)):
            data.update(rt.wake_state())
        return report('wake_settings', data)
    except Exception as exc:
        return outcome.failed('wake_settings', str(exc))


def summarize(payload):
    try:
        data = json.loads(str(payload).split('\n', 1)[1])
        if 'phrase' not in data:
            return None
        return ('Wake phrase saved: ' if data.get('updated') else 'My wake phrase is: ') + data['phrase'] + '. ' + (
            'Hey Jarvis fallback is enabled. ' if data.get('fallback_enabled') else 'Hey Jarvis fallback is disabled. ') + (
            'Wake detection is enabled.' if data.get('enabled') else 'Wake detection is currently off; enable WAKE WORD in settings.')
    except (ValueError, IndexError, TypeError):
        return None
