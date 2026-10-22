"""Exact local routes for simple app launches; compound commands stay with AI."""
import re
import unicodedata

APPS = {"chrome": "Google Chrome", "google chrome": "Google Chrome", "discord": "Discord",
        "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "visual studio code": "Visual Studio Code",
        "whatsapp": "WhatsApp", "notepad": "Notepad", "edge": "Microsoft Edge", "calculator": "Calculator"}


def route(text):
    normalized = " ".join(str(text).casefold().split()).rstrip(".!?")
    match = re.fullmatch(r"(?:hey jarvis[, ]+|jarvis[, ]+)?(?:please )?(?:open|launch|start) (.+?)(?: please)?", normalized)
    if match and match[1] in APPS:
        return "open_app", {"app_name": APPS[match[1]]}
    return None


def git_route(text):
    """Read-only shortcuts; staging/committing/pushing keep their review gates."""
    from pathlib import Path
    text = ' '.join(str(text).casefold().split()).rstrip('.!?')
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', text)
    if text in {'git status', 'show git status', 'show project git status', 'github status'}:
        return 'github_git', {'action': 'status', 'project': str(Path(__file__).resolve().parents[1])}
    if text in {'git diff', 'show git diff', 'show project diff'}:
        return 'github_git', {'action': 'diff', 'project': str(Path(__file__).resolve().parents[1])}
    return None


def canonical_app(value):
    value = ' '.join(unicodedata.normalize('NFKC', str(value)).casefold().split()).removesuffix('.exe')
    return APPS.get(value, value).casefold()


def canonical_arguments(name, args):
    args = dict(args)
    if name == 'open_app':
        return {'app_name': canonical_app(args.get('app_name', ''))}
    if name == 'computer_control' and args.get('action') in {'close_app', 'close_application', 'quit_app'}:
        result = {'action': 'close_app', 'app_name': canonical_app(args.get('app_name', ''))}
        if args.get('window_handle') is not None:
            result['window_handle'] = int(args['window_handle'])
        return result
    if name == 'whatsapp_control':
        args['contact'] = ' '.join(unicodedata.normalize('NFKC', str(args.get('contact', ''))).casefold().split())
    return args


def plan(text):
    """Only fully parsed app commands; never infer steps from a substring."""
    text = ' '.join(str(text).casefold().split()).rstrip('.!?')
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', text)
    text = re.sub(r' please$', '', text)
    clauses = re.split(r'\s+(?:and then|and|then)\s+|\s*;\s*', text)
    steps = []
    for clause in clauses:
        match = re.fullmatch(r'(open|launch|start|close|quit|exit) (.+)', clause)
        if not match or match[2] not in APPS:
            return None
        app = APPS[match[2]]
        steps.append(('computer_control', {'action': 'close_app', 'app_name': app})
                     if match[1] in {'close', 'quit', 'exit'} else ('open_app', {'app_name': app}))
    return steps or None
