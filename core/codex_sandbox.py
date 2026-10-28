"""Official CLI, source-only workspaces, restricted execution and JSONL progress.

No credentials are read/copied. ChatGPT login is reused by the official CLI.
No API authentication, shell shim, legacy unsafe sandbox or retry fallback.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

from core import git_security
from core.process_runner import run_process
from core import codex_diagnostics as diagnostics


def fingerprint(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def clean_environment(temp):
    # Intentionally do not inherit OPENAI_API_KEY, CODEX_API_KEY, access tokens,
    # provider URLs, proxies, cloud credentials or JARVIS telephony credentials.
    names = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'USERPROFILE',
             'LOCALAPPDATA', 'APPDATA', 'HOMEDRIVE', 'HOMEPATH', 'PROGRAMFILES',
             'PROGRAMFILES(X86)', 'PROGRAMDATA', 'CODEX_HOME'}
    env = {key: value for key, value in os.environ.items() if key.upper() in names}
    if not any(key.upper() == 'SYSTEMDRIVE' for key in env):
        env['SystemDrive'] = Path(os.environ.get('SystemRoot', r'C:\Windows')).drive
    env.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp), PYTHONDONTWRITEBYTECODE='1',
               PYTHONIOENCODING='utf-8', GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    return env


def verified_cli(context):
    import shutil
    executable = shutil.which('codex')
    if not executable or Path(executable).suffix.casefold() != '.exe':
        raise ValueError('A native official codex.exe on PATH is required; shell shims are not executed')
    executable = str(Path(executable).resolve(strict=True))
    # Trust Windows chain validation AND exact publisher, not filename alone.
    script = ("$s=Get-AuthenticodeSignature -LiteralPath $env:JARVIS_CODEX_EXE; "
              "$n=if($s.SignerCertificate){$s.SignerCertificate.GetNameInfo('SimpleName',$false)}else{''}; "
              "if($s.Status -eq 'Valid' -and $n -eq 'OpenAI OpCo, LLC'){Write-Output 'OPENAI_SIGNATURE_VALID';exit 0};exit 1")
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env = clean_environment(tempfile.gettempdir())
    env['JARVIS_CODEX_EXE'] = executable
    result = run_process([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                         context=context, timeout=15, env=env)
    if result.returncode or 'OPENAI_SIGNATURE_VALID' not in result.stdout:
        raise ValueError('Codex publisher signature could not be verified; execution refused')
    return {'path': executable, 'sha256': fingerprint(executable)}


def source_bytes(root, name):
    return git_security.file_bytes(root, name)


def snapshot(root, destination, files):
    if not isinstance(files, list) or not 1 <= len(files) <= 200 or len(set(files)) != len(files):
        raise ValueError('Approve between 1 and 200 unique source paths')
    manifest, total = {}, 0
    for name in files:
        if not isinstance(name, str) or '\\' in name:
            raise ValueError('Use unambiguous project-relative forward-slash source paths')
        data = source_bytes(root, name)
        total += len(data or b'')
        if total > 5 * 1024 * 1024:
            raise ValueError('Source scope exceeds 5 MiB; select fewer files to conserve usage')
        manifest[name] = hashlib.sha256(data).hexdigest() if data is not None else None
        if data is not None:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return manifest


def cli_command(executable, workspace, temp, prompt, read_only=False):
    # An OS-enforced restricted-read profile is mandatory; unsupported native
    # Windows builds fail closed rather than falling back to full read access.
    denied_auth = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
    settings = {
        'forced_login_method': 'chatgpt', 'model_provider': 'openai',
        'approval_policy': 'never', 'default_permissions': 'jarvis',
        'permissions.jarvis.filesystem': {':root': 'deny', ':minimal': 'read',
                                         str(workspace): 'read' if read_only else 'write', str(temp): 'write', denied_auth: 'deny'},
        'permissions.jarvis.network.enabled': False,
        'windows.sandbox': 'elevated',
        'features.apps': False, 'features.plugins': False, 'features.multi_agent': False,
        'features.image_generation': False, 'features.hooks': False,
        'features.skill_mcp_dependency_install': False, 'features.skill_search': False,
        'features.skip_host_skill_discovery': True, 'features.tool_suggest': False,
        'features.remote_plugin': False, 'features.code_mode': False, 'features.code_mode_host': False,
        'features.artifact': False, 'features.memories': False,
        'features.in_app_local_automation': False, 'features.in_app_browser': False,
        'features.realtime_conversation': False, 'features.daemon_auto_start': False,
        'features.unbounded_connection_retries': False,
        'features.browser_use': False, 'features.browser_use_external': False,
        'features.computer_use': False, 'features.shell_snapshot': False,
        'mcp_servers': {}, 'web_search': 'disabled',
        'shell_environment_policy.inherit': 'none',
        'shell_environment_policy.set': {'TEMP': str(temp), 'TMP': str(temp),
                                         'GIT_CEILING_DIRECTORIES': str(workspace.parent),
                                         'PYTHONDONTWRITEBYTECODE': '1'},
        'history.persistence': 'none',
    }
    args = [executable, 'exec', '--strict-config', '--ignore-user-config', '--ignore-rules', '--ephemeral',
            '--skip-git-repo-check', '--json', '--color', 'never', '--cd', str(workspace)]
    def toml(value):
        if isinstance(value, dict):
            return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
        return json.dumps(value)
    for key, value in settings.items():
        args.extend(['-c', key + '=' + toml(value)])
    # Prompt through stdin, not visible as a process command-line argument.
    args.append('-')
    return args


def check_config_ancestors(workspace):
    # --ignore-user-config does not grant permission to inherit project MCP,
    # hooks or plugin settings from a surrounding repository.
    for ancestor in (workspace, *workspace.parents):
        if (ancestor / '.codex').exists():
            raise ValueError('Inherited project .codex configuration detected; execution refused rather than loading external hooks/tools')


def terminate_owned(process):
    if process.poll() is not None:
        return
    import psutil
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.Error:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


class OwnedWindowsJob:
    """Kill only this CLI's job tree if JARVIS exits or its worker finishes."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            raise ValueError('This supervised runner requires native Windows job isolation')
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_longlong), ('JobTime', ctypes.c_longlong),
                        ('Flags', wintypes.DWORD), ('MinWorking', ctypes.c_size_t),
                        ('MaxWorking', ctypes.c_size_t), ('ActiveLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('Priority', wintypes.DWORD), ('Scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
                        ('JobMemory', ctypes.c_size_t), ('PeakProcessMemory', ctypes.c_size_t), ('PeakJobMemory', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError('Cannot create isolated Windows process job')
        self.handle = handle
        limits = Extended()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
            self.close()
            raise OSError('Windows process job isolation unavailable; execution refused')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def stream(args, prompt, workspace, temp, context, rt, event, timeout=300):
    """Separate bounded stderr/JSONL evidence, including exceptional exits."""
    env = clean_environment(temp)
    env['GIT_CEILING_DIRECTORIES'] = str(workspace.parent)
    started = time.monotonic()
    diag = {'phase': 'process_launch', 'launched': False, 'returncode': None,
            'command': diagnostics.safe_command(args, workspace, temp),
            'event_types': {}, 'last_valid_event': None, 'malformed_lines': 0,
            'completion_verified': False, 'turn_completed': False, 'final_message_seen': False,
            'terminal_failure': False, 'stderr': '', 'stdout_diagnostic': ''}
    process, owned_job = None, None
    with tempfile.TemporaryDirectory(dir=temp) as spool, (Path(spool) / 'stream').open('w+b') as output, (Path(spool) / 'stream').open('rb') as reader, (Path(spool) / 'stderr').open('w+b') as errors, tempfile.TemporaryFile(dir=temp) as source_input:
        position, pending = 0, b''
        def consume(line):
            if not line.strip():
                return
            if len(line) > 256 * 1024:
                raise ValueError('Codex event exceeded safe size')
            try:
                data = json.loads(line.decode('utf-8'))
                if not isinstance(data, dict) or not isinstance(data.get('type'), str):
                    raise ValueError('Not a typed JSONL event')
            except (ValueError, UnicodeError):
                diag['malformed_lines'] += 1
                diag['stdout_diagnostic'] = f'Malformed JSONL line ({len(line)} bytes); body omitted to protect private session content'
                event({'type': 'diagnostic', 'message': diag['stdout_diagnostic']})
                return
            metadata = diagnostics.event_metadata(data)
            kind = metadata['type']
            diag['event_types'][kind] = diag['event_types'].get(kind, 0) + 1
            diag['last_valid_event'] = metadata
            if kind == 'turn.completed':
                diag['turn_completed'] = True
            if kind in {'error', 'turn.failed', 'turn.interrupted', 'turn.cancelled', 'turn.canceled'}:
                diag['terminal_failure'] = True
                diag['failure_detail'] = metadata.get('message') or kind
                diag['failure_category'] = 'interrupted' if kind in {'turn.interrupted', 'turn.cancelled', 'turn.canceled'} else diagnostics.category(diag['failure_detail'])
            item = data.get('item') if isinstance(data.get('item'), dict) else {}
            if kind == 'item.completed' and item.get('type') == 'agent_message' and str(item.get('text', '')).strip():
                diag['final_message_seen'] = True
            event(data)
        try:
            # Source-fed analysis can exceed a pipe buffer. A file-backed stdin
            # keeps timeout/cancellation responsive even if CLI never reads it.
            encoded_prompt = prompt.encode('utf-8')
            if len(encoded_prompt) > 1024 * 1024:
                raise ValueError('Codex input exceeds the 1 MiB approved limit')
            source_input.write(encoded_prompt)
            source_input.flush()
            source_input.seek(0)
            with rt.side_effect('codex_process_start', context):
                process = subprocess.Popen(args, cwd=workspace, env=env, stdin=source_input,
                                           stdout=output, stderr=errors,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            diag.update(launched=True, phase='process_isolation')
            owned_job = OwnedWindowsJob(process)
            diag['windows_job_verified'] = True
            diag['stdin_transport'] = 'bounded_temporary_file'
            diag['phase'] = 'jsonl_runtime'
            while True:
                rt.check_cancelled(context)
                if time.monotonic() - started > timeout:
                    raise TimeoutError('Codex task reached the approved timeout; no automatic retry')
                size = os.fstat(output.fileno()).st_size
                if size + os.fstat(errors.fileno()).st_size > 8 * 1024 * 1024:
                    raise ValueError('Codex output exceeded 8 MiB; stopped safely')
                reader.seek(position)
                chunk = reader.read(min(65536, max(0, size - position)))
                position += len(chunk)
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    consume(line)
                if len(pending) > 256 * 1024:
                    raise ValueError('Unterminated Codex event exceeded safe size')
                if process.poll() is not None and position == size:
                    if pending.strip():
                        before = diag['malformed_lines']
                        consume(pending)
                        if diag['malformed_lines'] != before:
                            raise ValueError('Incomplete Codex event stream; result not verified')
                    diag['returncode'] = process.returncode
                    diag['completion_verified'] = (process.returncode == 0 and diag['turn_completed'] and
                        diag['final_message_seen'] and not diag['terminal_failure'] and not diag['malformed_lines'])
                    if not diag['completion_verified'] and not diag.get('failure_category'):
                        diag['failure_category'] = ('malformed_jsonl' if diag['malformed_lines'] else
                            'missing_completion' if process.returncode == 0 else 'runtime_failure')
                    return {'returncode': process.returncode, 'turn_completed': diag['turn_completed'],
                            'completion_verified': diag['completion_verified'], 'diagnostics': diag,
                            'seconds': round(time.monotonic() - started, 6)}
                try:
                    process.wait(timeout=.05)
                except subprocess.TimeoutExpired:
                    pass
        except BaseException as exc:
            if process is not None:
                terminate_owned(process)
            diag['returncode'] = process.returncode if process is not None else None
            diag['failure_category'] = ('cancelled' if isinstance(exc, InterruptedError) else
                'timeout' if isinstance(exc, TimeoutError) else 'process_launch' if not diag['launched'] else
                'process_isolation' if diag['phase'] == 'process_isolation' else
                'malformed_jsonl' if diag['malformed_lines'] else diagnostics.category(str(exc)))
            diag['failure_detail'] = diagnostics.sanitize(str(exc) or type(exc).__name__)
            exc.diagnostics = diag
            raise
        finally:
            diag['seconds'] = round(time.monotonic() - started, 6)
            errors.seek(0, 2)
            length = errors.tell()
            errors.seek(0)
            raw = errors.read(min(length, 16384))
            if length > 16384:
                errors.seek(length - 8192)
                raw = raw[:8192] + b'\n[bounded stderr truncated]\n' + errors.read(8192)
            diag['stderr'] = diagnostics.sanitize(raw.decode('utf-8', 'replace'))
            diag['stderr_truncated'] = length > 16384
            if diag['stderr'] and diag.get('failure_category') in {'runtime_failure', 'missing_completion'}:
                found = diagnostics.category(diag['stderr'])
                if found != 'runtime_failure':
                    diag['failure_category'] = found
            if owned_job:
                owned_job.close()
            if process is not None and process.stdin and not process.stdin.closed:
                process.stdin.close()


def classify_error(value):
    text = str(value).casefold()
    if any(word in text for word in ('configuration', 'unknown field', 'strict-config', 'unsupported feature')):
        return 'Codex policy/configuration is unsupported; execution refused, with no unsafe or paid fallback.'
    if any(word in text for word in ('usage_limit', 'rate_limit', 'quota', 'limit reached', 'usage limit')):
        return 'Codex account usage/rate limit reached; wait for reset. No paid API fallback or retry.'
    if any(word in text for word in ('unauthorized', 'not logged', 'authentication', '401', 'login')):
        return 'Codex ChatGPT authentication unavailable; run official codex login manually.'
    if any(word in text for word in ('sandbox', 'permission', 'mxc', 'setup refresh')):
        return 'Codex restricted Windows sandbox unavailable or permission denied; no unsafe fallback.'
    if any(word in text for word in ('network', 'connection', 'timeout', 'timed out')):
        return 'Codex network/timeout failure; result uncertain and not automatically retried.'
    return 'Codex reported a diagnostic; inspect the local task status. No paid fallback.'


def test_evidence(item):
    """Conservative CLI-observed test evidence, never a model's success prose."""
    if item.get('type') != 'command_execution' or item.get('exit_code') != 0:
        return None
    try:
        tokens = shlex.split(str(item.get('command', '')), posix=False)
    except ValueError:
        return None
    if tokens and tokens[0] == '&':
        tokens = tokens[1:]
    if len(tokens) < 3 or tokens[1] != '-m' or tokens[2] not in {'unittest', 'pytest'}:
        return None
    executable = Path(tokens[0].strip('"')).name.casefold()
    if not re.fullmatch(r'python(?:3(?:\.[0-9]+)?)?(?:\.exe)?', executable):
        return None
    if any(not re.fullmatch(r'[A-Za-z0-9_./\\:=+-]+', token) for token in tokens[3:]):
        return None
    output = str(item.get('aggregated_output', ''))
    count = re.search(r'Ran ([1-9][0-9]*) tests?\b', output)
    pytest = re.search(r'\b([1-9][0-9]*) passed\b', output)
    if not count and not pytest:
        return None
    return {'exit_code': 0, 'count': int((count or pytest)[1]), 'source': 'observed_cli_command_event'}
