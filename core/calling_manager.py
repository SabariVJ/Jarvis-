"""Opt-in telephone notification calls. No startup network, retries or recording.

Consumer WhatsApp remains in its existing desktop plugin. This module does not
provide conversational audio, schedules, provisioning or emergency calling.
"""
import hashlib
import json
import os
import re
import time
import unicodedata
import uuid
from xml.sax.saxutils import escape

from core.automation_runtime import get_runtime
from memory.config_manager import get_plugin_config

TERMINAL = {'completed', 'busy', 'no-answer', 'canceled', 'failed'}
STATES = {'queued', 'initiated', 'ringing', 'in-progress'} | TERMINAL


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).casefold().split())


def e164(value):
    # Do not guess country, remove extensions, or resolve local/emergency codes.
    if not isinstance(value, str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ValueError('Use an exact international +country-code number (E.164)')
    return value


def command(text):
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', str(text).strip(), flags=re.I)
    queries = {'show calling status': 'status', 'show calling history': 'history',
               'check phone connection': 'connectivity', 'show calling contacts': 'contacts'}
    action = queries.get(normalized(text).rstrip('.!?'))
    if action:
        return {'action': action}
    # A notification, not a live two-way conversation. Message is part of the
    # actual command, never supplied only by a model or prior task context.
    match = re.fullmatch(r'call (.{1,80}?) on (?:the )?phone and say "([^"\r\n]{1,500})"[.!]?', text, re.I)
    if match and not re.search(r'\b(and|then|or|after|before)\b|[;\r\n]', match[1], re.I):
        return {'action': 'notify', 'recipient': match[1].strip(), 'message': match[2]}
    return None


def configuration():
    config = dict(get_plugin_config('calling_manager'))
    contacts = json.loads(config.get('contacts_json') or '[]')
    if not isinstance(contacts, list) or len(contacts) > 100:
        raise ValueError('Contacts must be a list of at most 100 explicitly authorized entries')
    config['contacts'] = contacts
    return config


def resolve_recipient(value, config):
    contacts = config.get('contacts', [])
    if normalized(value) == 'my phone':
        candidates = [c for c in contacts if c.get('owner') is True]
    elif value.startswith('+'):
        number = e164(value)
        candidates = [c for c in contacts if c.get('number') == number]
    else:
        candidates = [c for c in contacts if normalized(c.get('name', '')) == normalized(value)]
    if len(candidates) != 1:
        raise ValueError('Recipient is unknown or ambiguous; configure one exact authorized contact')
    contact = candidates[0]
    if contact.get('authorized') is not True:
        raise ValueError('Recipient is not authorized for telephone notifications')
    return e164(contact.get('number'))


class TwilioProvider:
    """Official optional SDK, bounded HTTP, no automatic call creation retry."""
    def __init__(self):
        sid, token = os.environ.get('JARVIS_TWILIO_ACCOUNT_SID'), os.environ.get('JARVIS_TWILIO_AUTH_TOKEN')
        if not sid or not re.fullmatch(r'AC[0-9a-fA-F]{32}', sid) or not token:
            raise ValueError('Set JARVIS_TWILIO_ACCOUNT_SID and JARVIS_TWILIO_AUTH_TOKEN in the authorized process environment; never in source')
        try:
            from twilio.rest import Client
            from twilio.http.http_client import TwilioHttpClient
        except ImportError:
            raise ValueError('Optional official twilio SDK is missing; install requirements-calling.txt into an approved environment') from None
        http = TwilioHttpClient(timeout=10, max_retries=0)
        http.session.trust_env = False
        self.client = Client(sid, token, http_client=http)
        self.account_sid = sid

    def verify_caller(self, caller):
        e164(caller)
        # Account ownership/verified caller ID is checked at provider, not by a
        # browser sign-in or a model-generated "verified" flag.
        owned = self.client.incoming_phone_numbers.list(phone_number=caller, limit=2)
        verified = self.client.outgoing_caller_ids.list(phone_number=caller, limit=2) if not owned else []
        if not any(item.phone_number == caller for item in [*owned, *verified]):
            raise ValueError('Caller ID is not owned or verified in this Twilio account')
        return True

    def create(self, number, caller, message):
        twiml = '<Response><Say>Hello. This is Jarvis, an AI assistant. ' + escape(message) + '</Say><Hangup/></Response>'
        result = self.client.calls.create(to=number, from_=caller, twiml=twiml, record=False, timeout=25)
        return {'sid': result.sid, 'state': result.status}

    def fetch(self, sid):
        call = self.client.calls(sid).fetch()
        return {'sid': call.sid, 'state': call.status, 'duration': call.duration}


def provider(config):
    if config.get('provider') != 'twilio':
        raise ValueError('No supported telephone provider configured; choose a provider before activation')
    return TwilioProvider()


def safe_failure(exc):
    # SDK errors can include phone numbers, credentials, URLs and message text.
    status = getattr(exc, 'status', None)
    code = getattr(exc, 'code', None)
    if status in (401, 403):
        return 'Provider authentication/permission failed (HTTP ' + str(status) + '); check account credentials and voice permissions'
    if status:
        return 'Provider rejected request (HTTP ' + str(status) + ', code ' + str(code) + '); inspect provider console'
    return 'Network/provider outcome uncertain (' + type(exc).__name__ + '); no retry will be attempted'


def snapshot(rt=None, config=None):
    rt = rt or get_runtime()
    config = configuration() if config is None else config
    history = sorted(rt.records('telephone_call').values(), key=lambda c: c['at'], reverse=True)[:30]
    return {'provider': config.get('provider') or 'unconfigured', 'remote_access': 'not_checked',
            'outgoing_enabled': config.get('outgoing_enabled') is True,
            'caller_id': config.get('caller_id') or None,
            'caller_id_verification': 'not_checked',
            'contacts': [{'name': c.get('name'), 'authorized': c.get('authorized') is True,
                          'owner': c.get('owner') is True} for c in config.get('contacts', [])],
            'history': history, 'incoming_enabled': False, 'recording_enabled': False,
            'transcription_enabled': False, 'scheduled_calls': [], 'proactive_calls_enabled': False,
            'audio': 'Provider TTS notification only; desktop microphone/speaker not connected',
            'limitations': 'No two-way AI audio, incoming answering, transfers, scheduled or completion calls yet',
            'cooldown_seconds': 30}


def notify(arguments, context, rt=None, config=None, backend=None):
    rt = rt or get_runtime()
    context = context or rt.context()
    rt.check_cancelled(context)
    if context.get('lifecycle') == 'job' or command(context.get('user_text', '')) != arguments:
        raise ValueError('A fresh exact phone-notification command is required; calls cannot be replayed from jobs')
    rt.authorize('calling_manager', arguments, context)
    config = configuration() if config is None else config
    if config.get('outgoing_enabled') is not True or config.get('costs_and_consent_approved') is not True:
        raise ValueError('Telephone calls are disabled; approve provider costs, recipient consent and jurisdiction before enabling')
    number = resolve_recipient(arguments['recipient'], config)
    caller = e164(config.get('caller_id'))
    backend = backend or provider(config)
    backend.verify_caller(caller)
    if normalized(arguments['recipient']) == 'my phone':
        backend.verify_caller(number)  # Owner number must also be verified at provider.
    rt.check_cancelled(context)
    identity = uuid.uuid4().hex
    now = time.time()
    # Single shared guard table preserves the WhatsApp 30-second global guard.
    # One irreversible attempt per turn, even across changed provider call IDs.
    token = context.get('turn_id')
    if not token:
        raise ValueError('Missing fresh command identity')
    guard = 'telephone-turn:' + hashlib.sha256(token.encode()).hexdigest()
    record = {'id': identity, 'at': now, 'state': 'requested', 'direction': 'outgoing',
              'recipient': arguments['recipient'], 'provider': config.get('provider'),
              'execution_id': context['execution_id'], 'verified': False}
    with rt.side_effect('telephone_attempt_reserved', context):
        rt.authorize('calling_manager', arguments, context)
        with rt.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM guards WHERE id=?', (guard,)).fetchone():
                raise ValueError('This turn already attempted a telephone call; a fresh request is required')
            last = db.execute("SELECT at FROM guards WHERE id='last_call'").fetchone()
            if last and now - last[0] < 30:
                raise ValueError('Call cooldown is active; wait 30 seconds and ask again')
            db.execute('INSERT INTO guards VALUES (?,?)', (guard, now))
            db.execute("INSERT OR REPLACE INTO guards VALUES ('last_call',?)", (now,))
            db.execute('INSERT INTO records VALUES (?,?,?)', ('telephone_call', identity, json.dumps(record)))
    # Record precedes network. Cancellation before transport prevents dispatch;
    # once submitted it cannot be reliably recalled. Never hold runtime lock
    # during a potentially slow provider request, blocking voice interruption.
    try:
        rt.authorize('calling_manager', arguments, context)
    except InterruptedError:
        record.update(state='canceled', verified=True)
        rt.put('telephone_call', identity, record)
        raise
    try:
        result = backend.create(number, caller, arguments['message'])
        if not re.fullmatch(r'CA[0-9a-fA-F]{32}', str(result.get('sid', ''))) or result.get('state') not in STATES:
            raise RuntimeError('Invalid provider acknowledgement')
        record.update(sid=result['sid'], state=result['state'], verified=True)
    except Exception as exc:
        record.update(state='uncertain', failure=safe_failure(exc))
    rt.put('telephone_call', identity, record)
    return record


def refresh(identity, rt=None, config=None, backend=None):
    rt = rt or get_runtime()
    record = rt.get('telephone_call', identity)
    if not record or not record.get('sid'):
        raise ValueError('No acknowledged local call with this ID; uncertain submissions must not be redialed')
    config = configuration() if config is None else config
    backend = backend or provider(config)
    result = backend.fetch(record['sid'])
    if result.get('sid') != record['sid'] or result.get('state') not in STATES:
        raise ValueError('Provider returned an invalid call identity or state')
    record.update(state=result['state'], duration=result.get('duration'), verified=True)
    rt.put('telephone_call', identity, record)
    return record


def validated_webhook(url, form, signature, expected_url, expected_account):
    """Host integration seam, NOT an exposed server or enabled incoming mode.

    Use the official SDK against the exact configured public URL and ALL form
    fields. Only form-encoded events supported. Host must bound request size.
    """
    if not expected_url.startswith('https://') or url != expected_url:
        raise ValueError('Webhook endpoint mismatch')
    from twilio.request_validator import RequestValidator
    token = os.environ.get('JARVIS_TWILIO_AUTH_TOKEN')
    if not token or not RequestValidator(token).validate(url, form, signature):
        raise ValueError('Invalid provider webhook signature')
    if form.get('AccountSid') != expected_account:
        raise ValueError('Webhook account mismatch')
    # Signed caller ID is still not caller authentication. Incoming calls stay
    # rejected without tools, model context, memory, recording or transcription.
    return {'incoming_enabled': False, 'response': '<Response><Reject reason="rejected"/></Response>'}
