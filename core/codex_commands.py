"""Narrow voice/text grammar shared by routing and side-effect authorization."""
import re


def command(text):
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', str(text).strip(), flags=re.I)
    match = re.fullmatch(r'(?:ask codex to|prepare codex task:)\s*(.{1,2000})', text, re.I)
    if match:
        return {'action': 'prepare', 'goal': match[1].strip()}
    match = re.fullmatch(r'(run|cancel|review|show) codex task ([a-f0-9]{12})[.!]?', text, re.I)
    if match:
        return {'action': {'run': 'run', 'cancel': 'cancel', 'review': 'review', 'show': 'task_status'}[match[1].lower()], 'id': match[2].lower()}
    match = re.fullmatch(r'check codex sandbox ([a-f0-9]{12})[.!]?', text, re.I)
    if match:
        return {'action': 'preflight', 'id': match[1].lower()}
    match = re.fullmatch(r'apply codex task ([a-f0-9]{12}) review ([a-f0-9]{64})[.!]?', text, re.I)
    if match:
        return {'action': 'apply', 'id': match[1].lower(), 'review': match[2].lower()}
    return None
