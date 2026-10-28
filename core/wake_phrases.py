"""Conservative normalization and explicit wake-settings command parsing."""
import re
import unicodedata
DEFAULT_PHRASE = "Wake up Jarvis, daddy's home"
FALLBACK_PHRASE = 'Hey Jarvis'


def normalize(text):
    text = unicodedata.normalize('NFKC', str(text)).casefold()
    text = re.sub(r"['’‘`ʼ]", '', text)
    text = ' '.join(re.findall(r'[^\W_]+', text, re.UNICODE))
    text = re.sub(r'\bjar vis\b', 'jarvis', text)
    return re.sub(r'\bdaddy (?:s|is)\b|\bdaddies\b', 'daddys', text)


def validate(phrase):
    if not isinstance(phrase, str) or '\n' in phrase or '\r' in phrase or len(phrase) > 120:
        raise ValueError('Use one wake phrase, at most 120 characters')
    phrase = phrase.strip().strip('"“”')
    if not 2 <= len(normalize(phrase).split()) <= 12 or not re.fullmatch(r"[A-Za-z0-9\s'’‘,!.?\-]+", phrase):
        raise ValueError('Use 2–12 English words with ordinary punctuation')
    return phrase


def recognition_phrases(phrase, fallback):
    canonical = normalize(validate(phrase))
    variants = {canonical}
    if 'daddys' in canonical:
        variants = {canonical.replace('daddys', word) for word in ("daddy's", 'daddy is', 'daddies')}
    if fallback:
        variants.add(normalize(FALLBACK_PHRASE))
    return sorted(variants)


def matches(text, phrase, fallback=True):
    candidate = normalize(text)
    return candidate == normalize(phrase) or bool(fallback and candidate == normalize(FALLBACK_PHRASE))


def settings_command(text):
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', str(text).strip(), flags=re.I)
    if text.casefold().rstrip('.!?') in {'what is my wake phrase', 'what is your wake phrase',
            "what's my wake phrase", 'show wake phrase', 'show wake word settings', 'what is your wake word'}:
        return {'action': 'get'}
    match = re.fullmatch(r'(?:set|change|update) (?:my |your |the )?wake (?:phrase|word) to\s+(.+)', text, re.I)
    if match:
        return {'action': 'set', 'phrase': validate(match[1])}
    match = re.fullmatch(r'(enable|disable) (?:the )?hey jarvis fallback[.!?]?', text, re.I)
    if match:
        return {'action': 'set', 'fallback_enabled': match[1].casefold() == 'enable'}
    return None
