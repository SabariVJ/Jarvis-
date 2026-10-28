"""Read-only, fail-closed compatibility inspection. Never selects a fallback.

Candidate/version/OS support is NOT an isolation attestation. No model request,
credential file access, administrator setup or task receipt mutation occurs.
"""
import json
import hashlib
import os
from pathlib import Path
import re
import tomllib

from core import codex_sandbox as sandbox
from core.codex_diagnostics import sanitize
from core.process_runner import run_process


def host_build():
    if os.name != 'nt':
        return None
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion') as key:
        return (int(winreg.QueryValueEx(key, 'CurrentBuildNumber')[0]), int(winreg.QueryValueEx(key, 'UBR')[0]))


def assess(version, build, help_text):
    match = re.search(r'codex-cli (\d+)\.(\d+)\.(\d+)', version)
    cli_supported = bool(match and tuple(map(int, match.groups())) >= (0, 162, 0)
                         and '--permission-profile' in help_text and '--include-managed-config' in help_text)
    windows_supported = bool(build and ((build[0] == 26100 and build[1] >= 9278) or
        (build[0] == 26200 and build[1] >= 9278) or build[0] > 26200))
    return {'mxc_candidate': cli_supported and windows_supported,
            'cli_sandbox_interface_supported': cli_supported, 'windows_build_candidate': windows_supported,
            'backend_enabled': False, 'isolation_verified': False}


def profile(args):
    result = {}
    for index, value in enumerate(args[:-1]):
        if value == '-c':
            key, raw = args[index + 1].split('=', 1)
            result[key] = tomllib.loads('value=' + raw)['value']
    return result


def inspect(job, context, rt):
    """Inspect actual executable and intended overrides; unknown checks block."""
    rt.check_cancelled(context)
    checks = {'signed_executable': False, 'pinned_executable': False,
        'supported_cli_interface': False, 'filesystem_overrides': False, 'source_copy': False,
        'effective_os_filesystem_isolation': False, 'private_path_denial': False,
        'source_write_restrictions': False, 'network_isolation': False,
        'authentication_connectivity': False, 'native_cancellation': False, 'managed_powershell': False}
    result = {'ready': False, 'backend': None, 'proposed_backend': None, 'checks': checks,
        'state': 'blocked', 'inference_submitted': False, 'receipt_modified': False}
    try:
        approved = sandbox.verified_cli(context)
        checks['signed_executable'] = True
        checks['pinned_executable'] = approved == job.get('cli')
        if not checks['pinned_executable']:
            raise ValueError('The signed CLI on PATH differs from this prepared task; prepare a new task, never rebind an old approval')
        executable = approved['path']
        from core.codex_tasks import workspace_for
        directory = workspace_for(job['id'])
        env = sandbox.clean_environment(directory)
        version = run_process([executable, '--version'], context=context, timeout=10, env=env)
        help_result = run_process([executable, 'sandbox', '--help'], context=context, timeout=10, env=env)
        exec_help = run_process([executable, 'exec', '--help'], context=context, timeout=10, env=env)
        detected = assess(str(version.stdout), host_build(), str(help_result.stdout))
        result.update(candidate=detected, cli_version=sanitize(version.stdout, 100), host_build=host_build())
        checks['supported_cli_interface'] = (not version.returncode and not help_result.returncode and not exec_help.returncode
            and detected['cli_sandbox_interface_supported'] and all(flag in str(exec_help.stdout) for flag in
                ('--strict-config', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--json')))
        workspace, temp = directory / 'workspace', directory / 'temp'
        args = sandbox.cli_command(executable, workspace, temp, '', read_only=job.get('read_only', False))
        settings = profile(args)
        fs = settings['permissions.jarvis.filesystem']
        denied_auth = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
        expected_fs = {':root': 'deny', ':minimal': 'read', str(workspace): 'read' if job.get('read_only') else 'write',
                       str(temp): 'write', denied_auth: 'deny'}
        checks['filesystem_overrides'] = (fs == expected_fs and len(fs) == 5
            and settings.get('permissions.jarvis.network.enabled') is False
            and settings.get('approval_policy') == 'never')
        source_matches = []
        for name, digest in job.get('manifest', {}).items():
            data = sandbox.source_bytes(workspace, name)
            source_matches.append((hashlib.sha256(data).hexdigest() if data is not None else None) == digest)
        checks['source_copy'] = bool(source_matches) and all(source_matches)
        result['requested_policy'] = {'backend': settings['windows.sandbox'], 'root': fs.get(':root'),
            'minimal': fs.get(':minimal'), 'source': fs.get(str(workspace)), 'temp': fs.get(str(temp)),
            'network_enabled': settings['permissions.jarvis.network.enabled'], 'approval_policy': settings['approval_policy'],
            'note': 'CLI overrides inspected, NOT a claim that the backend enforced them'}
        if settings['windows.sandbox'] == 'elevated' and fs.get(':root') == 'deny':
            result['failure_category'] = 'sandbox_policy_conflict'
            result['error'] = 'elevated Windows sandbox requires effective :root read access; the approved root-deny policy is incompatible. Root access was NOT broadened.'
        else:
            result['failure_category'] = 'sandbox_isolation_unverified'
            result['error'] = 'No independently verified backend satisfies every required isolation and connectivity check.'
        result['proposed_backend'] = 'mxc' if detected['mxc_candidate'] else 'independently_isolated_environment'
        permitted_probe = (not job.get('start_consumed') and job.get('state') == 'prepared') or (
            context.get('lifecycle') == 'job' and job.get('state') in {'queued', 'running'} and
            job.get('start_consumed') and job.get('scope_sha256') and job.get('approved_turn') and job.get('approved_scope') == job.get('scope_sha256'))
        if all(checks[k] for k in ('signed_executable', 'pinned_executable', 'supported_cli_interface', 'filesystem_overrides', 'source_copy')) and detected['mxc_candidate'] and job.get('read_only') and permitted_probe:
            from core import mxc_backend
            proof = mxc_backend.verify(job, context, rt)
            result['mxc'] = proof
            result['attempted_backend'] = 'mxc'
            found = proof.get('checks', {})
            checks.update(effective_os_filesystem_isolation=all(found.get(k) for k in ('native_source_read', 'source_write_denied', 'private_read_denied', 'private_write_denied', 'temp_write_only')),
                private_path_denial=found.get('private_read_denied', False),
                source_write_restrictions=found.get('source_write_denied', False),
                network_isolation=found.get('network_probe_runtime', False) and found.get('loopback_denied', False) and found.get('lan_denied', False),
                authentication_connectivity=all(found.get(k) for k in ('chatgpt_login', 'provider_http', 'provider_websocket')),
                native_cancellation=found.get('native_cancellation', False),
                managed_powershell=found.get('managed_powershell', False))
            if proof.get('ready') is True and all(found.get(k) is True for k in mxc_backend.CHECKS) and all(checks.values()):
                checks.update(effective_os_filesystem_isolation=True, private_path_denial=True,
                    source_write_restrictions=True, network_isolation=True, authentication_connectivity=True, native_cancellation=True)
                result.update(ready=True, backend='mxc', state='ready_for_explicit_read_only_approval',
                    failure_category=None, error='', safe_next_action='Only a fresh explicit run of this prepared read-only task may start inference. Checks repeat in its worker; no prior task is replayed.')
                result['requested_policy']['backend'] = 'mxc'
                result['requested_policy']['command_execution_tools'] = 'disabled by shell_tool=false'
                rt.trace('codex_preflight_ready', context, backend='mxc', binding=proof['binding'])
                return result
            result['failure_category'] = 'mxc_runtime_unverified'
            result['error'] = 'MXC read-only verification blocked: ' + proof.get('error', 'incomplete mandatory checks')
        result['safe_next_action'] = ('Do not run or retry inference. MXC is only a candidate: verify filesystem/private canaries, source writes, complete network policy, PowerShell runtime, authenticated broker connectivity and native cancellation before a separately reviewed backend change. '
            'Do not use ordinary WSL with Windows mounts/interop or grant root-read as an equivalent substitute.')
        if result.get('mxc'):
            result['safe_next_action'] = ('Do not start inference. Resolve the failed checks shown in mxc.checks with the same signed binary and unchanged deny policy. '
                'Command execution must be disabled by shell_tool=false; unified_exec alone is not an authorization gate. No root-read, paid API or automatic fallback.')
    except InterruptedError:
        raise
    except Exception as exc:
        result.update(failure_category='preflight_failed', error=sanitize(str(exc)))
        result['safe_next_action'] = 'Resolve the specific preflight failure; no inference, escalation or automatic fallback.'
    rt.trace('codex_preflight_blocked', context, failure_category=result['failure_category'], checks=checks)
    return result


def blocked_view(job, check):
    return {**job, 'stored_state': job['state'], 'state': 'blocked', 'preflight': check,
            'error': check['error'], 'message': 'Execution NOT started. ' + check['safe_next_action']}


def terminal_view(job):
    """Read-only projection: terminal process evidence overrides stale narration."""
    result = dict(job)
    diagnostic = job.get('execution_diagnostics') or (job.get('cli_result') or {}).get('diagnostics') or {}
    code = diagnostic.get('returncode')
    failure = (code is not None and code != 0) or diagnostic.get('terminal_failure')
    if failure and job.get('state') in {'queued', 'running'}:
        from core.codex_diagnostics import summary
        result.update(stored_state=job['state'], state='needs_attention', error=summary(diagnostic),
            message='Terminal execution failure; not queued or running. No automatic replay.')
    return result
