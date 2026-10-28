"""Official CLI authentication diagnostic. No token files or browser access.

Login is deliberately interactive/user-operated. Auth status does not prove
quota, remote inference availability, or permission to edit/publish a project.
"""
from pathlib import Path
import shutil
import subprocess

from core.process_runner import run_process

PROJECT = Path(__file__).resolve().parents[1]


def diagnostic(context=None):
    executable = shutil.which('codex')
    if not executable:
        return {'installed': False, 'authenticated': False, 'remote_inference': 'not_checked',
                'message': 'Codex CLI was not found on PATH. Install the official CLI, then run codex login.',
                'login_command': 'codex login'}
    # Never execute an arbitrary .cmd/.bat shim through a shell. On Windows the
    # supported signed native CLI is codex.exe; a shim needs manual setup.
    if Path(executable).suffix.casefold() in {'.cmd', '.bat', '.ps1'}:
        return {'installed': True, 'authenticated': False, 'remote_inference': 'not_checked',
                'message': 'CLI shell shim detected; configure a native Codex executable on PATH.',
                'login_command': 'codex login'}
    try:
        version = run_process([executable, '--version'], cwd=PROJECT, context=context, timeout=10)
        result = run_process([executable, 'login', 'status'], cwd=PROJECT, context=context, timeout=15)
    except InterruptedError:
        raise
    except (TimeoutError, OSError, subprocess.SubprocessError) as exc:
        return {'installed': True, 'authenticated': False, 'remote_inference': 'not_checked',
                'message': 'Codex CLI diagnostic failed: ' + type(exc).__name__}
    output = str(result.stdout).casefold()
    authenticated = result.returncode == 0 and 'logged in' in output and 'not logged in' not in output
    auth_mode = ('ChatGPT' if 'chatgpt' in output else 'API key' if 'api key' in output else 'unknown') if authenticated else 'none'
    # Whitelist the version line, never return raw auth output/URLs or tokens.
    import re
    match = re.search(r'codex-cli [0-9][a-zA-Z0-9.+-]*', str(version.stdout))
    return {'installed': True, 'version': match[0] if match else 'unknown',
            'authenticated': authenticated, 'authentication_mode': auth_mode,
            'remote_inference': 'not_checked', 'quota': 'unknown',
            'execution_ready': False, 'sandbox_status': 'fresh_per_task_mxc_preflight_required',
            'message': ('Official CLI reports a stored login. Inference and quota have not been tested.' if authenticated
                        else 'CLI does not report a valid login. In a terminal run codex login, then check again.') +
                       ' Execution remains blocked until fresh per-task MXC isolation checks pass for read-only source analysis; a login is not execution readiness.',
            'login_command': 'codex login',
            'billing': 'ChatGPT/Codex plan limits and separately billed API usage are distinct; neither implies unlimited access.'}


def review_plan(project, goal):
    root = Path(project).resolve(strict=True)
    if root != PROJECT or not root.is_dir():
        raise ValueError('Only the installed JARVIS project is approved for this integration')
    if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 2000:
        raise ValueError('Provide a bounded explicit coding goal')
    return {'project': str(root), 'goal': goal.strip(), 'state': 'approval_required',
            'executed': False, 'publishing_allowed': False,
            'message': 'Plan prepared only; no process was started. Prepare a source-only task with explicit paths, then check Codex sandbox ID. Execution is blocked until a separately verified backend meets the privacy requirements.',
            'required_checks': ['Review project access and exclude credentials/private memory',
                                'Use supported read-only or scoped workspace sandbox',
                                'Approve sensitive edits separately; do not auto-commit or push',
                                'Run tests and secret checks on proposed changes']}
