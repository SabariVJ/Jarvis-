"""Verified user desktop windows; never grants permission to kill processes.

Process ancestry is conservative infrastructure protection. open_app can grant
one separately verified browser window a narrow WM_CLOSE capability instead.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time

from core import outcome
from core.automation_runtime import get_runtime

_PROPERTY = 'JarvisUserDesktopWindowV1'


def is_chrome(name):
    from core.appidentity import fold
    return fold(str(name).casefold().removesuffix('.exe')) in {'chrome', 'googlechrome'}


def _api():
    api = ctypes.WinDLL('user32', use_last_error=True)
    signatures = {
        'GetForegroundWindow': ([], wintypes.HWND),
        'IsWindow': ([wintypes.HWND], wintypes.BOOL),
        'IsWindowVisible': ([wintypes.HWND], wintypes.BOOL),
        'GetWindow': ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
        'GetWindowThreadProcessId': ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
        'GetClassNameW': ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
        'SetPropW': ([wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE], wintypes.BOOL),
        'GetPropW': ([wintypes.HWND, wintypes.LPCWSTR], wintypes.HANDLE),
        'PostMessageW': ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(api, name)
        fn.argtypes, fn.restype = args, result
    return api


def foreground():
    return int(_api().GetForegroundWindow() or 0)


def window_info(hwnd):
    """None means a positively absent window; inspection failure raises."""
    api = _api()
    if not api.IsWindow(hwnd):
        return None
    owner = wintypes.DWORD()
    tid = int(api.GetWindowThreadProcessId(hwnd, ctypes.byref(owner)))
    name = ctypes.create_unicode_buffer(256)
    if not tid or not api.GetClassNameW(hwnd, name, len(name)):
        raise OSError('Could not inspect window identity')
    return {'hwnd': int(hwnd), 'pid': int(owner.value), 'thread_id': tid,
            'class': name.value, 'visible': bool(api.IsWindowVisible(hwnd)),
            'owner': int(api.GetWindow(hwnd, 4) or 0),  # GW_OWNER: exclude modal/owned popups.
            'nonce': int(api.GetPropW(hwnd, _PROPERTY) or 0)}


def _mark_window(hwnd, nonce):
    if not _api().SetPropW(hwnd, _PROPERTY, nonce):
        raise OSError('Windows refused to register this desktop window')


def _post_window_close(hwnd):
    return bool(_api().PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE, exactly one HWND


def _hard_protected(pid, exe):
    from core import appidentity as ai
    if pid == os.getpid() or os.path.normcase(exe) == os.path.normcase(sys.executable):
        return True
    # Ancestors and their sibling binaries stay protected even with a grant.
    for parent in ai.psutil.Process(os.getpid()).parents():
        if pid == parent.pid or os.path.normcase(exe) == os.path.normcase(parent.exe()):
            return True
    return False


def process_identity(pid, chrome_only=False):
    from core import appidentity as ai
    proc = ai.psutil.Process(pid)
    exe = proc.exe()
    command = proc.cmdline()
    if not exe or _hard_protected(pid, exe):
        raise ValueError('Assistant, interpreter or ancestor process remains protected')
    chrome = is_chrome(Path(exe).name)
    if chrome_only and not chrome:
        raise ValueError('The target is not a Chrome browser process')
    if chrome:
        flags = [str(arg).casefold() for arg in command[1:]]
        profile_values = [arg.split('=', 1)[1] for arg in flags if arg.startswith('--user-data-dir=')]
        profile_values += [flags[i + 1] for i, arg in enumerate(flags[:-1]) if arg == '--user-data-dir']
        private_profile = any(word in value for value in profile_values for word in ('jarvis', 'playwright', 'chromiumdev'))
        if any(arg == '--type' or arg.startswith(('--type=', '--headless', '--enable-automation', '--remote-debugging', '--test-type'))
               for arg in flags) or private_profile:
            raise ValueError('Internal/automated browser or Chrome helper remains protected')
    return {'pid': int(pid), 'created': float(proc.create_time()), 'exe': os.path.normcase(exe),
            'command_sha256': hashlib.sha256(json.dumps(command).encode()).hexdigest()}


def _same_process(first, second):
    return all(first.get(key) == second.get(key) for key in ('pid', 'created', 'exe', 'command_sha256'))


def _same_window(first, second):
    return bool(second and first.get('nonce') and all(first.get(key) == second.get(key)
                for key in ('hwnd', 'pid', 'thread_id', 'class', 'nonce', 'owner')))


def can_open(match, spoken):
    if not is_chrome(spoken):
        return True
    try:
        process_identity(match.pid, chrome_only=True)
        return True
    except Exception:
        return False


def record_open(match, spoken):
    """Record actual app process + HWND identity after a verified open/focus.

    A per-window OS property prevents a recycled HWND from inheriting a grant.
    No permission to terminate this process or its descendants is recorded.
    """
    from core import appidentity as ai
    if ai._OS != 'Windows':
        return False
    rt = get_runtime()
    try:
        identity = process_identity(match.pid, chrome_only=is_chrome(spoken))
        chrome_process = is_chrome(Path(identity['exe']).name)
        windows = []
        for hwnd in ai._hwnds_for(match.pid):
            info = window_info(hwnd)
            if not info or info['pid'] != match.pid or not info['visible']:
                continue
            if chrome_process and (info['class'] != 'Chrome_WidgetWin_1' or info.get('owner', 0)):
                continue
            nonce = secrets.randbelow(2 ** 30) + 1
            with rt.side_effect('desktop_window_registered', app=spoken, pid=match.pid, hwnd=hwnd):
                _mark_window(hwnd, nonce)
            info = window_info(hwnd)
            if info and info['nonce'] == nonce:
                windows.append(info)
        if not windows:
            return False
        selected = foreground()
        handles = [w['hwnd'] for w in windows]
        if selected not in handles:
            selected = handles[0] if len(handles) == 1 else None
        context = getattr(rt._local, 'context', None) or rt.context()
        rt.put('desktop_app', str(match.pid), {**identity, 'app': spoken, 'windows': windows,
               'selected': selected, 'recorded': time.time(), 'opened_by_execution': context.get('execution_id')})
        return True
    except Exception as exc:
        rt.trace('desktop_registration_unverified', app=spoken, pid=match.pid, reason=str(exc))
        return False


def registered_windows(pid):
    grant = get_runtime().get('desktop_app', str(pid))
    if not grant or not _same_process(grant, process_identity(pid, chrome_only=True)):
        return [], grant
    valid = []
    for window in grant.get('windows', []):
        current = window_info(window['hwnd'])
        if (current and current['visible'] and current['class'] == 'Chrome_WidgetWin_1'
                and not current.get('owner', 0) and _same_window(window, current)):
            valid.append(window)
    return valid, grant


def is_registered_user_process(pid):
    try:
        windows, _ = registered_windows(pid)
        return bool(windows)
    except Exception:
        return False


def request_window_close(match, timeout=6):
    """Revalidate process, window and command at the last side-effect boundary."""
    rt = get_runtime()
    context = getattr(match, 'window_context', None)
    target = getattr(match, 'window_identity', None)
    if not context or not target:
        return False, 'A verified user-facing Chrome window and current close command are required; no process was terminated'
    try:
        rt.authorize('computer_control', {'action': 'close_app', 'app_name': 'Google Chrome'}, context)
        windows, grant = registered_windows(match.pid)
        if not any(_same_window(target, w) for w in windows):
            return False, 'The registered Chrome process/window identity changed; nothing was closed'
        with rt.side_effect('app_window_close_requested', context, pid=match.pid, hwnd=target['hwnd'], app='Google Chrome'):
            # Check again inside the lock, immediately before posting WM_CLOSE.
            if not _same_process(grant, process_identity(match.pid, chrome_only=True)):
                return False, 'Chrome process identity changed before WM_CLOSE'
            current = window_info(target['hwnd'])
            if not current or not current['visible'] or not _same_window(target, current):
                return False, 'Chrome window identity changed before WM_CLOSE'
            if not _post_window_close(target['hwnd']):
                return False, 'Windows refused the Chrome window close request'
        deadline = time.monotonic() + timeout
        while True:
            rt.check_cancelled(context)
            current = window_info(target['hwnd'])
            if current is None:
                rt.trace('app_window_close_verified', context, pid=match.pid, hwnd=target['hwnd'],
                         verified=True, process_exit_required=False)
                return True, ''
            if not _same_window(target, current):
                return False, 'Chrome window identity changed during verification; closing was not confirmed'
            if time.monotonic() >= deadline:
                return False, 'The intended Chrome window is still present; a dialog or pending work may prevent closing it'
            time.sleep(.1)
    except Exception as exc:
        return False, f'Chrome window close was not verified: {exc}'


def close_chrome(spoken, window_handle=None):
    from core import appidentity as ai
    rt = get_runtime()
    context = dict(getattr(rt._local, 'context', None) or rt.context())
    rt.authorize('computer_control', {'action': 'close_app', 'app_name': spoken,
                 **({'window_handle': window_handle} if window_handle is not None else {})}, context)
    targets, selected = [], []
    for match in ai.running(spoken, include_self=True):
        if match.score < 30 or not is_chrome(match.name):
            continue
        try:
            process_identity(match.pid, chrome_only=True)
            windows, grant = registered_windows(match.pid)
            # An existing, ordinary external Chrome can be adopted by this
            # explicit close. An unregistered child remains infrastructure.
            if not grant and match.pid not in ai._chain_pids():
                record_open(match, spoken)
                windows, grant = registered_windows(match.pid)
            for info in windows:
                from dataclasses import replace
                targets.append(replace(match, window_identity=info, window_context=context))
                if grant.get('selected') == info['hwnd']:
                    selected.append(info['hwnd'])
        except Exception as exc:
            rt.trace('desktop_close_candidate_protected', context, pid=match.pid, reason=str(exc))
    if window_handle is not None:
        targets = [m for m in targets if m.window_identity['hwnd'] == int(window_handle)]
    else:
        fore = foreground() if targets else 0
        front = [m for m in targets if m.window_identity['hwnd'] == fore]
        remembered = [m for m in targets if m.window_identity['hwnd'] in selected]
        if front:
            targets = front
        elif len(remembered) == 1:
            targets = remembered
    if not targets:
        return outcome.failed('close_app', 'No verified user-facing Chrome window is available. '
                              'Open Chrome through JARVIS first. Internal browser processes stay protected.', app=spoken)
    if len(targets) > 1:
        return outcome.ask('close_app', 'More than one Chrome window is available. Focus the intended window '
                           'or specify its window_handle.', app=spoken,
                           window_handles=','.join(str(m.window_identity['hwnd']) for m in targets))
    target = targets[0]
    gone, reason = ai.request_close(target)
    if gone:
        return outcome.done('close_app', app=spoken, window_handle=target.window_identity['hwnd'],
                            pid=target.pid, windows_closed=1, verified=True, process_exit_required=False)
    return outcome.unverified('close_app', reason, app=spoken, window_handle=target.window_identity['hwnd'])
