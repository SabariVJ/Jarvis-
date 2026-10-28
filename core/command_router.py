"""Conservative local routes for explicit navigation to known applications."""
import re
import unicodedata
import time

APPS = {"chrome": "Google Chrome", "google chrome": "Google Chrome", "discord": "Discord",
        "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "visual studio code": "Visual Studio Code",
        "whatsapp": "WhatsApp", "notepad": "Notepad", "edge": "Microsoft Edge", "calculator": "Calculator"}


def route(text):
    normalized = " ".join(str(text).casefold().split()).rstrip(".!?")
    match = re.fullmatch(r"(?:hey jarvis[, ]+|jarvis[, ]+)?(?:please )?(?:open|launch|start|hop on|switch to) (.+?)(?: please)?", normalized)
    if match and match[1] in APPS:
        return "open_app", {"app_name": APPS[match[1]]}
    return None


def git_route(text):
    """Read-only shortcuts; staging/committing/pushing keep their review gates."""
    from pathlib import Path
    text = ' '.join(str(text).casefold().split()).rstrip('.!?')
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', text)
    text = re.sub(r' please$', '', text)
    connectivity = {
        'are you connected to github', 'are we connected to github', 'is github connected',
        'is github integrated', 'do you have github integration', 'can you use github',
        'can you access github', 'check github connection', 'check github connectivity',
        'github connection', 'github connectivity', 'what github tools do you have',
        'what can you do with github', 'are you integrated with github',
    }
    if text in connectivity:
        return 'github_git', {'action': 'connectivity', 'project': str(Path(__file__).resolve().parents[1])}
    readonly = {
        'show git branches': 'branches', 'git branches': 'branches', 'git branch': 'branches',
        'what branch are we on': 'branches', 'show current branch': 'branches',
        'show git remotes': 'remotes', 'git remotes': 'remotes', 'git remote': 'remotes',
        'show github remote': 'remotes', 'show recent commits': 'log', 'git log': 'log',
        'show git commits': 'log', 'check github authentication': 'connectivity',
    }
    if text in readonly:
        return 'github_git', {'action': readonly[text], 'project': str(Path(__file__).resolve().parents[1])}
    publication = {'git commit': 'commit', 'commit changes': 'commit', 'commit my changes': 'commit', 'commit project changes': 'commit',
                   'git push': 'push', 'push changes': 'push', 'push my changes': 'push', 'push to github': 'push', 'publish changes to github': 'push'}
    if text in publication:
        return 'github_git', {'action': 'publication_plan', 'operation': publication[text],
                              'project': str(Path(__file__).resolve().parents[1])}
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
    if name == 'wake_settings' and 'phrase' in args:
        from core.wake_phrases import normalize
        args['phrase'] = normalize(args['phrase'])
    return args


def plan(text):
    """Only fully parsed app commands; never infer steps from a substring."""
    text = ' '.join(str(text).casefold().split()).rstrip('.!?')
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', text)
    text = re.sub(r' please$', '', text)
    clauses = re.split(r'\s+(?:and then|and|then)\s+|\s*;\s*', text)
    steps = []
    for clause in clauses:
        match = re.fullmatch(r'(open|launch|start|hop on|switch to|close|quit|exit) (.+)', clause)
        if not match or match[2] not in APPS:
            return None
        app = APPS[match[2]]
        steps.append(('computer_control', {'action': 'close_app', 'app_name': app})
                     if match[1] in {'close', 'quit', 'exit'} else ('open_app', {'app_name': app}))
    return steps or None


def resolve(text, declarations, runtime=None, context=None):
    """Conservative grammar, validated against the currently enabled schemas.

    No fuzzy contact/app guesses, inferred approvals, or substring execution.
    None means model planning, not that the capability is uninstalled.
    """
    tools = {d['name']: d for d in declarations or () if isinstance(d, dict) and d.get('name')}
    from core.wake_phrases import settings_command
    try:
        wake = settings_command(text)
    except ValueError:
        wake = None
    normalized = ' '.join(unicodedata.normalize('NFKC', str(text)).split()).rstrip('.!?')
    normalized = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', normalized, flags=re.I)
    normalized = re.sub(r' please$', '', normalized, flags=re.I)
    from core import calling_intents
    calling = calling_intents.followup(runtime, context) if runtime else None
    steps = [('wake_settings', wake)] if wake else calling or plan(text)
    if not steps:
        proposed = calling_intents.route(text, declarations)
        if proposed and proposed[0][0] in tools:
            steps = proposed
    if not steps:
        from core.github_guardian import command as guardian_command
        guardian = guardian_command(text)
        if guardian:
            steps = [('github_git', guardian)]
    if not steps:
        git = git_route(text)
        steps = [git] if git else None
    if not steps:
        from core.codex_commands import command as codex_command
        codex = codex_command(text)
        if codex:
            steps = [('codex_assistant', codex)]
    if not steps:
        from core.calling_manager import command as calling_command
        calling = calling_command(text)
        if calling:
            steps = [('calling_manager', calling)]
        elif normalized.casefold() in {'are you connected to codex', 'check codex connection',
                                       'show codex status', 'is codex connected'}:
            steps = [('codex_assistant', {'action': 'status'})]
    if not steps:
        actions = {
            'show notification settings': ('local_notifications', {'action': 'status'}),
            'show notification history': ('local_notifications', {'action': 'history'}),
            'show automation performance': ('automation_status', {'action': 'performance'}),
            'show automation status': ('automation_status', {'action': 'status'}),
            'what tools do you have': ('automation_status', {'action': 'capabilities'}),
            'show enabled plugins': ('automation_status', {'action': 'capabilities'}),
            'list workflows': ('task_manager', {'action': 'list'}),
            'show workflows': ('task_manager', {'action': 'list'}),
        }
        candidate = actions.get(normalized.casefold())
        match = re.fullmatch(r'(?:show )?workflow status ([a-f0-9]{12,32})', normalized, re.I)
        cancel = re.fullmatch(r'cancel workflow ([a-f0-9]{12,32})', normalized, re.I)
        if match or cancel:
            candidate = ('task_manager', {'action': 'status' if match else 'cancel', 'id': (match or cancel)[1]})
        if candidate:
            steps = [candidate]
    if not steps:
        patterns = (
            (r'(?:voice call|call) (.+?) on whatsapp', 'voice_call'),
            (r'video call (.+?) on whatsapp', 'video_call'),
            (r'open (?:the )?chat with (.+?) on whatsapp', 'open_chat'),
            (r'open (?:the )?(.+?) chat on whatsapp', 'open_chat'),
            (r'(?:search for|search|find) (.+?) on whatsapp', 'search'),
        )
        for pattern, action in patterns:
            match = re.fullmatch(pattern, normalized, re.I)
            if match:
                contact = match[1].strip()
                # Compound/injected requests and ambiguous contact instructions
                # belong to planning, not a guessed literal contact name.
                if not 1 <= len(contact) <= 80 or re.search(r'\b(and|then|or|after|before)\b|[;\n\r]', contact, re.I):
                    return None
                steps = [('whatsapp_control', {'action': action, 'contact': contact})]
                break
    if not steps:
        return None
    for name, arguments in steps:
        schema = tools.get(name, {}).get('parameters', {})
        properties = schema.get('properties', {})
        if name not in tools or any(key not in properties for key in arguments):
            return None
        if any(key not in arguments for key in schema.get('required', ())):
            return None
        for key, value in arguments.items():
            field = properties[key]
            if field.get('enum') and value not in field['enum']:
                return None
            if field.get('type', '').casefold() == 'string' and not isinstance(value, str):
                return None
    return steps


def recognize(text, declarations, runtime=None, context=None):
    started = time.perf_counter()
    steps = resolve(text, declarations, runtime, context)
    if runtime:
        runtime.trace('performance_span', context, phase='intent_recognition', parent=None,
                      seconds=round(time.perf_counter() - started, 6))
        runtime.trace('intent_recognized', context, route='deterministic' if steps else 'model_fallback',
                      steps=len(steps or []), enabled_tools=len(declarations or []))
        if steps:
            runtime.set_route(context, steps)
    return steps


def authorization_plan(text):
    """Restrict native/model calls for fully recognized explicit commands."""
    from core.github_guardian import command as guardian_command
    guardian = guardian_command(text)
    if guardian:
        return [('github_git', guardian)]
    from core.wake_phrases import settings_command
    try:
        wake = settings_command(text)
    except ValueError:
        wake = None  # Native input transcripts may still be incomplete.
    if wake:
        return [('wake_settings', wake)]
    from core.calling_manager import command as telephone_command
    requested_notification = telephone_command(text)
    if requested_notification and requested_notification.get('action') == 'notify':
        # Existing separately configured/approved notification API keeps its
        # guard. Unified routing still declines paid calling for this owner.
        return [('calling_manager', requested_notification)]
    from core.calling_intents import authorization
    calling = authorization(text)
    if calling:
        return calling
    from core.codex_commands import command as codex_command
    codex = codex_command(text)
    if codex:
        return [('codex_assistant', codex)]
    from core.calling_manager import command as calling_command
    calling = calling_command(text)
    return [('calling_manager', calling)] if calling else plan(text)
