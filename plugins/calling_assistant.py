"""Read-only channel awareness and clarification, not another call executor."""
from core import calling_intents, outcome
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report

PLUGIN = declaration('calling_assistant',
    'Actual enabled calling channels and safe clarification. Use for can you call someone/video call, '
    'call NAME without a channel, regular telephone availability, incoming/status/end questions. '
    'This tool NEVER dials. WhatsApp voice/video use whatsapp_control. Paid phone calling stays disabled. '
    'Recipient/channel follow-ups require current short-lived runtime authorization; never infer old call permission.',
    {'action': string(choices=['capabilities', 'clarify', 'phone', 'incoming', 'status', 'end'])})
PLUGIN['tier'] = 'core'


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    try:
        return report('calling_assistant', calling_intents.respond(rt, session_memory or rt.context(), parameters.get('action')))
    except (ValueError, InterruptedError) as exc:
        return outcome.failed('calling_assistant', str(exc))
