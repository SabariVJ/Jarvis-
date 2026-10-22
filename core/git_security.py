"""Conservative file/content policy for reviewed Git snapshots. Never logs secrets."""
import os
from pathlib import Path
import re
import stat

FORBIDDEN_DIRS = {'.git', '.ssh', '.aws', '.azure', '.codex', '__pycache__', '.venv', 'venv', 'node_modules',
                  'upgrade-backups', 'backups', 'backup', 'logs', 'screenshots', 'downloads'}
TOKENS = [re.compile(rb'gh[pousr]_[A-Za-z0-9]{20,}'), re.compile(rb'github_pat_[A-Za-z0-9_]{20,}'),
          re.compile(rb'sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}'), re.compile(rb'AIza[A-Za-z0-9_-]{30,}'),
          re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}'), re.compile(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
          re.compile(rb'(?i)authorization[\s"\x27:]+bearer\s+[A-Za-z0-9_.-]{16,}')]
TOKENS.append(re.compile(rb'(?i)https?://[^/\s:@]+:[^/\s@]+@'))
ASSIGNMENT = re.compile(rb'''(?im)["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|secret[_-]?key|token|secret)["']?\s*[:=]\s*["']([^"'\r\n]{8,})["']''')
YAML_VALUE = re.compile(rb'(?im)^\s*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|secret[_-]?key|token|secret)\s*:\s*([^\s#\r\n]{8,})')
PLACEHOLDERS = {b'placeholder', b'your_api_key', b'your-api-key', b'your_token', b'example', b'not-a-real-key'}


def allowed_path(name):
    p = Path(name)
    parts = [s.casefold() for s in p.parts]
    base = p.name.casefold()
    if p.is_absolute() or p.drive or '..' in p.parts or not parts:
        raise ValueError('Choose explicit project-relative files')
    if any(ch in str(name) for ch in ('\0', '\r', '\n')):
        raise ValueError('Control characters are not supported in reviewed filenames')
    if any(':' in part or part != part.rstrip(' .') or part.split('.')[0] in {
            'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
           for part in parts):
        raise ValueError('Ambiguous Windows/NTFS paths cannot be reviewed')
    if any(part in FORBIDDEN_DIRS or part.endswith(('.bak', '.backup')) for part in parts):
        raise ValueError(f'Private/generated directory excluded: {name}')
    if (base.startswith(('.env', 'private')) or any(word in base for word in ('api_key', 'apikey', 'credential', 'secret'))
            or base in {'.gitconfig', '.netrc', '.npmrc', '.pypirc'}
            or base.startswith(('token', 'id_rsa', 'id_ed25519'))
            or p.suffix.casefold() in {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.pfx', '.p12', '.log'}
            or '.db-' in base or base.endswith(('~', '.bak'))):
        raise ValueError(f'Credential/private/runtime file excluded: {name}')
    if parts[0] in {'memory', 'config'} and p.suffix.casefold() != '.py':
        raise ValueError(f'Private configuration/memory data excluded: {name}')
    return p


def inspect_bytes(name, content):
    allowed_path(name)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError(f'File exceeds the 2 MiB review limit: {name}')
    if b'\0' in content:
        raise ValueError(f'Binary files require a separate manual Git review: {name}')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError(f'Only UTF-8 text is supported by automated review: {name}')
    if any(pattern.search(content) for pattern in TOKENS):
        raise ValueError(f'Possible secret detected in {name}; value is withheld')
    for match in ASSIGNMENT.finditer(content):
        value = match[1].strip().lower()
        if value not in PLACEHOLDERS and not value.startswith((b'your_', b'example_', b'<', b'${')):
            raise ValueError(f'Possible embedded credential detected in {name}; value is withheld')
    if Path(name).suffix.casefold() in {'.yaml', '.yml'}:
        for match in YAML_VALUE.finditer(content):
            value = match[1].strip(b'\"\x27').lower()
            if value not in PLACEHOLDERS and not value.startswith((b'your_', b'example_', b'<', b'$')):
                raise ValueError(f'Possible YAML credential detected in {name}; value is withheld')
    if name.endswith('.py'):
        compile(text, name, 'exec')


def file_bytes(root, name):
    relative = allowed_path(name)
    current = root
    for component in relative.parts:
        current = current / component
        if current.exists() or current.is_symlink():
            st = current.lstat()
            if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
                raise ValueError(f'Symlinks/junctions are excluded: {name}')
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('File escapes the project')
    if not resolved.exists():
        return None
    if not resolved.is_file() or resolved.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(f'Only bounded regular files can be reviewed: {name}')
    with resolved.open('rb') as handle:
        data = handle.read(2 * 1024 * 1024 + 1)
    inspect_bytes(name, data)
    return data


def redact(text):
    text = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', str(text))
    for pattern in TOKENS:
        text = pattern.sub(b'[REDACTED]', text.encode('utf-8', errors='replace')).decode('utf-8')
    return text[-1400:]
