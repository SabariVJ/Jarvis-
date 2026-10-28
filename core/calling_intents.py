"""Enabled-channel evidence and short-lived, session-local calling clarification.

Never dials. A follow-up grant authorizes one exact current-turn WhatsApp action;
the existing plugin still verifies its recipient and reserves the persistent guard.
"""
import json
import re
import time
import unicodedata
import uuid

TTL = 60


def clean(text):
    text = unicodedata.normalize('NFKC', str(text)).strip()
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', text, flags=re.I)
    return ' '.join(text.split()).rstrip('.!?')


def recipient(text):
    if '\n' in str(text) or '\r' in str(text):
        return None
    text = clean(text)
    if not re.fullmatch(r"[\w .’'+()-]{1,80}", text) or len(text.split()) > 5:
        return None
    if re.search(r"\b(and|or|then|after|before|someone|anyone|somebody|anybody|who|not|no|yes|okay|ok|dont|don't|isnt|isn't|weather|time|thanks|hello|stop|cancel|never|open|close|search|tell|what|why|please)\b", text, re.I):
        return None
    return text


def classify(text):
    text = clean(text)
    lower = text.casefold()
    queries = {'can you call someone', 'can you call somebody', 'can you call', 'can you make calls',
               'can you make a call', 'do you support calling', 'what calling channels are available',
               'what calls can you make', 'what calling tools do you have', 'can you make voice calls',
               'can you make video calls', 'can you video call', 'can you make whatsapp calls'}
    if lower in queries:
        return {'kind': 'calling_capability_query', 'mode': 'video_call' if 'video' in lower else 'voice_call'}
    if re.fullmatch(r'can you (?:voice call|video call|call) .{1,100}', lower):
        return {'kind': 'calling_capability_query', 'mode': 'video_call' if 'video call' in lower else 'voice_call'}
    if lower in {'incoming call status', 'show incoming calls', 'can you answer calls', 'can you receive calls'}:
        return {'kind': 'incoming_call_status'}
    if re.fullmatch(r'(?:end|hang up|stop) (?:the )?(?:whatsapp )?call', lower):
        return {'kind': 'end_call'}
    if lower in {'call status', 'show call status', 'whatsapp call status', 'is the whatsapp call connected',
                 'is the call connected', 'is the call ringing', 'did the call end'}:
        return {'kind': 'call_status'}
    explicit = re.fullmatch(r'(video call|voice call|audio call|call) (.+?) (?:on|using) whatsapp', text, re.I)
    if not explicit:
        explicit = re.fullmatch(r'whatsapp (video call|voice call|audio call|call) (.+)', text, re.I)
    if explicit:
        contact = recipient(explicit[2])
        return {'kind': 'whatsapp_video_call' if explicit[1].casefold() == 'video call' else 'whatsapp_voice_call',
                'recipient': contact, 'invalid_recipient': not bool(contact)}
    if (re.match(r'^(?:call|dial|phone|ring)\b', lower) and
            (re.search(r'\b(phone|telephone|normally|mobile number|regular|landline)\b', lower) or re.search(r'\+[0-9]{7,}', lower))):
        return {'kind': 'phone_call'}
    bare = re.fullmatch(r'(?:call|voice call|audio call|video call) (.+)', text, re.I)
    if bare:
        contact = recipient(bare[1])
        return {'kind': 'calling_channel_clarification', 'recipient': contact,
                'mode': 'video_call' if lower.startswith('video call ') else None,
                'invalid_recipient': not bool(contact)}
    return None


def inventory(declarations):
    tools = {d.get('name'): d for d in declarations or [] if isinstance(d, dict)}
    def supports(name, action):
        fields = tools.get(name, {}).get('parameters', {}).get('properties', {})
        return action in fields.get('action', {}).get('enum', [])
    return {'whatsapp_voice': supports('whatsapp_control', 'voice_call'),
            'whatsapp_video': supports('whatsapp_control', 'video_call'),
            'whatsapp_status': supports('whatsapp_control', 'status'),
            'whatsapp_end': supports('whatsapp_control', 'end_call'),
            'telephone_tool_enabled': 'calling_manager' in tools,
            'telephone_dialing_permitted': False, 'incoming_answering_available': False,
            'video_live_reliability': 'not_verified', 'desktop_microphone': 'separate; not tested by this diagnostic',
            'telephony_audio': 'not integrated; does not disable the desktop microphone'}


def catalog(rt):
    provider = getattr(rt, 'catalog_provider', None)
    return provider() if callable(provider) else getattr(rt, 'catalog', [])


def route(text, declarations):
    parsed = classify(text)
    if not parsed:
        return None
    caps = inventory(declarations)
    kind = parsed['kind']
    if kind.startswith('whatsapp_') and parsed.get('recipient'):
        mode = 'video_call' if kind == 'whatsapp_video_call' else 'voice_call'
        if caps['whatsapp_video' if mode == 'video_call' else 'whatsapp_voice']:
            return [('whatsapp_control', {'action': mode, 'contact': parsed['recipient']})]
    actions = {'calling_capability_query': 'capabilities', 'calling_channel_clarification': 'clarify',
               'whatsapp_voice_call': 'clarify', 'whatsapp_video_call': 'clarify', 'phone_call': 'phone',
               'incoming_call_status': 'incoming', 'call_status': 'status', 'end_call': 'end'}
    return [('calling_assistant', {'action': actions[kind]})]


def authorization(text):
    """Intent constraints, not a claim that any executor is enabled."""
    parsed = classify(text)
    if not parsed:
        return None
    if parsed['kind'].startswith('whatsapp_') and parsed.get('recipient'):
        return [('whatsapp_control', {'action': 'video_call' if parsed['kind'] == 'whatsapp_video_call' else 'voice_call',
                                      'contact': parsed['recipient']})]
    return route(text, [])


def claim_live_call(rt, context, declarations):
    """One deterministic dispatch of an explicit FINAL native transcript.

    This only claims a current execution, not a call attempt. The plugin must
    still verify the exact recipient/header and reserve its persistent cooldown.
    No claim is restored as queued work or replayed after reconnect.
    """
    context = context or {}
    if (context.get('source') != 'speech_live' or not context.get('input_final')
            or context.get('lifecycle') == 'job'):
        return None
    steps = route(context.get('user_text', ''), declarations)
    if not steps or steps[0][0] != 'whatsapp_control' or steps[0][1]['action'] not in {'voice_call', 'video_call'}:
        return None
    with rt._state_lock:
        rt.check_cancelled(context)
        if rt.context().get('execution_id') != context.get('execution_id'):
            return None
        execution = rt.get('execution', context['execution_id'], {})
        if execution.get('live_call_claimed'):
            return None
        rt.authorize(*steps[0], context)
        rt.update('execution', context['execution_id'], {'live_call_claimed': True})
        rt.trace('live_call_route_claimed', context, 'whatsapp_control', final_transcript=True)
        return steps


def clear(rt):
    rt._calling_pending = None
    rt._calling_reply = None
    rt._calling_grant = None


def advance(rt, previous, text, final):
    pending = getattr(rt, '_calling_pending', None)
    clear(rt)
    if (not pending or pending['origin'] != previous.get('execution_id') or
            pending['generation'] != rt._generation or time.monotonic() >= pending['expires']):
        return
    rt._calling_reply = {'execution_id': rt._context['execution_id'], 'pending': pending}
    refresh(rt, text, final)


def refresh(rt, text, final):
    rt._calling_grant = None
    reply = getattr(rt, '_calling_reply', None)
    if not reply or reply['execution_id'] != rt._context.get('execution_id'):
        return
    pending = reply['pending']
    if pending['generation'] != rt._generation or time.monotonic() >= pending['expires']:
        rt._calling_reply = None
        return
    if not final:
        return  # Native partial transcripts can NEVER authorize a follow-up call.
    caps = inventory(catalog(rt))
    mode, contact = pending.get('mode'), pending.get('recipient')
    if contact:
        modes = {'video': 'video_call', 'whatsapp video': 'video_call', 'whatsapp video call': 'video_call',
                 'voice': 'voice_call', 'whatsapp': 'voice_call', 'whatsapp voice': 'voice_call',
                 'whatsapp voice call': 'voice_call', 'audio': 'voice_call'}
        mode = modes.get(clean(text).casefold())
    else:
        contact = recipient(text)
    if not contact or not mode or not caps['whatsapp_video' if mode == 'video_call' else 'whatsapp_voice']:
        rt._calling_reply = None  # Unrelated/ambiguous topic consumes the clarification.
        return
    rt._calling_grant = {'execution_id': rt._context['execution_id'], 'generation': rt._generation,
                         'reply': clean(text).casefold(), 'expires': pending['expires'], 'nonce': pending['nonce'],
                         'arguments': {'action': mode, 'contact': contact}}


def followup(rt, context):
    grant = getattr(rt, '_calling_grant', None)
    context = context or {}
    if (not grant or context.get('lifecycle') == 'job' or grant['execution_id'] != context.get('execution_id') or
            grant['generation'] != rt._generation or time.monotonic() >= grant['expires'] or
            clean(context.get('user_text', '')).casefold() != grant['reply'] or not context.get('input_final', True)):
        return None
    caps = inventory(catalog(rt))
    mode = grant['arguments']['action']
    if not caps['whatsapp_video' if mode == 'video_call' else 'whatsapp_voice']:
        return None
    return [('whatsapp_control', dict(grant['arguments']))]


def permits_followup(rt, args, context):
    steps = followup(rt, context)
    if not steps:
        return False
    from core.command_router import canonical_arguments
    return canonical_arguments('whatsapp_control', args) == canonical_arguments('whatsapp_control', steps[0][1])


def respond(rt, context, action):
    rt.check_cancelled(context)
    if context.get('lifecycle') == 'job':
        raise ValueError('Calling clarification requires a fresh foreground conversation')
    declarations = catalog(rt)
    selected = route(context.get('user_text', ''), declarations)
    if selected != [('calling_assistant', {'action': action})]:
        raise ValueError('Calling question/action differs from the current user request')
    rt.authorize('calling_assistant', {'action': action}, context)
    parsed, caps = classify(context['user_text']), inventory(declarations)
    data = {'intent': parsed['kind'], 'capabilities': caps, 'state': 'informational', 'call_initiated': False}
    available = caps['whatsapp_voice'] or caps['whatsapp_video']
    mode = parsed.get('mode') or ('voice_call' if caps['whatsapp_voice'] else 'video_call')
    contact = parsed.get('recipient')
    pending = False
    if action == 'phone':
        message = 'Regular telephone calling is not configured or permitted under your no-paid-services policy. '
        message += 'I can help call an exact saved contact through WhatsApp.' if available else 'No enabled WhatsApp calling control is currently available.'
    elif action == 'incoming':
        message = 'Telephone incoming answering is disabled. The WhatsApp plugin does not expose verified incoming-answer controls. Your desktop microphone is separate from telephone audio integration.'
    elif action in {'status', 'end'}:
        message = ('Automatic WhatsApp call ending is not currently exposed by an enabled verified control. Use WhatsApp to hang up; I have not ended a call.' if action == 'end' else
                   'WhatsApp ringing, connected, declined and ended states are unknown: the enabled plugin does not expose verified call-status monitoring. A call-button click is not a connection.')
    elif not available:
        message = 'WhatsApp calling is not offered by the current enabled tools. Regular telephone calling is also disabled. No call was initiated.'
    elif parsed.get('invalid_recipient'):
        message = 'I need an exact saved contact name. I cannot verify an unspecified or approximate recipient, and will not substitute another contact.'
    elif action == 'capabilities':
        if mode == 'voice_call' and not caps['whatsapp_voice']:
            message = 'WhatsApp voice calling is not enabled. WhatsApp video controls are available, with live reliability unverified. Ask explicitly for a WhatsApp video call.'
        elif mode == 'video_call' and not caps['whatsapp_video']:
            message = 'WhatsApp video calling is not enabled. WhatsApp voice controls are available.'
        else:
            pending = True
            message = ('Yes, sir. I have WhatsApp video-calling controls available, but live video reliability is unverified. Who would you like to video call on WhatsApp?' if mode == 'video_call' else
                       'Yes, sir. I can call your contacts through WhatsApp voice calling. Regular telephone calls are disabled. Who would you like me to call on WhatsApp?')
    else:
        pending = True
        if caps['whatsapp_voice'] and caps['whatsapp_video']:
            message = f'Would you like a WhatsApp voice call or video call to {contact}? Video reliability is unverified.'
            mode = None
        else:
            mode = 'voice_call' if caps['whatsapp_voice'] else 'video_call'
            message = f'Would you like a WhatsApp {"voice" if mode == "voice_call" else "video"} call to {contact}? Reply WhatsApp {"voice" if mode == "voice_call" else "video"}.'
    if pending and (context.get('source') != 'speech_live' or context.get('input_final')):
        with rt._state_lock:
            rt.check_cancelled(context)
            existing = getattr(rt, '_calling_pending', None)
            if not existing or existing['origin'] != context['execution_id']:
                rt._calling_pending = {'origin': context['execution_id'], 'generation': rt._generation,
                                       'expires': time.monotonic() + TTL, 'nonce': uuid.uuid4().hex,
                                       'mode': mode, 'recipient': contact}
        data['state'] = 'awaiting_recipient' if not contact else 'awaiting_channel'
    data['message'] = message
    return data


def summarize(payload):
    text = str(payload or '')
    if text.startswith(('status=failed ', 'status=cancelled ', 'status=unverified ')):
        return 'Calling diagnostic reported: ' + text[:1400]
    try:
        data = json.loads(text.split('\n', 1)[1])
        return data.get('message') if data.get('call_initiated') is False and 'capabilities' in data else None
    except (ValueError, TypeError, IndexError):
        return None


def capability_policy(declarations):
    caps = inventory(declarations)
    return ('\n[CURRENT CALLING CHANNELS]\n' + json.dumps(caps) +
            '\nUse calling_assistant for general calling questions/clarifications/status/end support. '
            'Telephone unavailability does not mean WhatsApp is unavailable. Microphone and provider audio are separate. '
            'Explicit WhatsApp calls use whatsapp_control with exact recipient/header checks. '
            'A fresh final explicit call instruction authorizes one attempt; do not invent an extra internal confirmation. '
            'Report external permission only from an observed permission-dialog tool receipt, never from missing authorization or unknown UI. '
            'No channel on a call request requires clarification, never a paid transport guess. '
            'Only current runtime-authorized follow-ups may complete an intent. Never replay calls or infer a connection from clicking.\n')
