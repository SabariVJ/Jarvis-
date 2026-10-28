# Chrome closing: Windows ownership fix

Implemented and verified with mocked Windows processes and windows. A live Chrome close on your desktop has not been verified.

## Root cause

`_chain_pids()` intentionally includes JARVIS, all descendants and all ancestors. Chrome launched by `open_app` through `Popen` is consequently in that tree. `own_exes()` can cache Chrome's executable from that tree, and `own_pids()` extends protection to other processes using that executable. `running()` excludes protected matches by default. Finally, `request_close()` checks ancestry and executable protection again. A user-facing Chrome window therefore gets refused as assistant infrastructure.

Those safeguards protect JARVIS, its interpreter, model processes and internal browsers, so removing Chrome from the protected process tree or broadly exempting `chrome.exe` would be incorrect. The old generic close handler also groups processes by executable name; applying that to Chrome would risk closing unrelated windows or touching renderer/helper processes.

The executable resolver and task-owned process runner do not cause the classification error. They were inspected and left unchanged. The automation runtime's lifecycle and replay checks were also retained unchanged.

## Corrected behavior

- `open_app` records the verified actual process PID, creation time, executable fingerprint, command-line fingerprint, visible window handles, window class/thread identity and selected user window. This works when Chrome reuses an existing browser process as well as when a new process starts. The recorded command line is a hash, not the arguments themselves.
- A small per-window Windows property distinguishes the registered window from a later window that reuses its HWND. Registration never grants process-termination permission.
- Browser helper flags, automation/debugging/headless flags, known private JARVIS/Playwright profiles, JARVIS itself, its interpreter and ancestor executables remain protected. Owned modal/pop-up windows are not registered as ordinary Chrome browser windows.
- A verified user-facing Chrome process can appear as a user desktop match even while conservative process-kill protection still includes its PID/executable. Other internal Chrome processes retain their protected classification.
- Closing Chrome selects exactly one verified window: the intended foreground window, the uniquely remembered selected window, or an explicit `window_handle`. Ambiguity produces a question instead of closing all windows.
- `request_close()` revalidates the process, the exact window and the captured command lifecycle immediately before posting one `WM_CLOSE`. Chrome has no fallback to `terminate()`, `kill()`, process-tree termination, or closing every window for a PID.
- Success means the intended window was observed to disappear. Chrome's browser process, renderers or background services may remain running. A refused request, remaining window, changed identity, inaccessible window or cancellation produces a failed/unverified outcome rather than success.
- Ownership records contain identity evidence only. They do not queue work or restore execution authorization. Existing execution IDs, receipts, cancellation and call replay protections continue to govern dispatch. Explicit window handles now participate in canonical duplicate-action identity.

An ordinary external Chrome window can be adopted by an explicit close request if it is outside the JARVIS tree and passes verification. An unregistered Chrome child of JARVIS remains protected until a fresh `Open Chrome` request verifies and registers its user-facing window.

## Exact files changed

| Path | Change |
|---|---|
| `core/appidentity.py` | Separate verified user-window classification from broad process protection; route Chrome closing through the one-window verifier; exclude internal Chrome from launch verification. |
| `actions/open_app.py` | Register actual app/window identities after successful launch or focus, and skip internal browser candidates. |
| `actions/computer_control.py` | Use the Chrome window close path; expose an optional exact window handle for ambiguity. |
| `core/command_router.py` | Include an explicit close window handle in canonical arguments/idempotency. |
| `core/desktop_ownership.py` (new) | Process/window identity evidence, registration, protected browser roles and guarded single-window `WM_CLOSE`. |
| `tests/test_chrome_ownership.py` (new) | 22 mocked Chrome ownership and close regressions. |
| `tools/chrome_ownership_check.py` (new) | Read-only ancestry/window diagnostic; does not register, focus or close windows. |
| `CHROME_CLOSE_FIX.md` (new) | This report. |

`plugins/whatsapp_control.py`, `core/automation_runtime.py`, `core/process_runner.py`, and `core/windows_apps.py` are unchanged. No credentials, personal settings or existing memory/database were copied, deleted or reset.

## Verification

79 automation/replay/integration tests passed, 0 failed: the complete existing 57-test suite plus 22 new Chrome tests. The existing provider self-test reports 99 passed, 0 failed; its optional Piper checks remain skipped.

The mocked regressions cover:

- Open Chrome, then close its window while browser/background/helper processes remain alive.
- Open Chrome, close Chrome, then open WhatsApp.
- Open WhatsApp, close WhatsApp, open Chrome, then close Chrome.
- Preserving JARVIS, Python/model infrastructure, internal browser windows and unrelated Chrome helpers.
- Closing only one of multiple Chrome windows and asking when the target is ambiguous.
- Failed `WM_CLOSE`, a window remaining open, inspection failures before/after posting, owned popups, PID/HWND reuse, changed browser roles and refused registration.
- Stale/cancelled command authorization and completed receipt replay prevention.
- Preserving distinct explicit window targets in canonical duplicate checks.

All potentially disruptive desktop actions and calls in the tests are mocked. A read-only process/window snapshot found a running JARVIS process but no Chrome processes at the time of inspection. No live Chrome window was opened or closed by this investigation. No real WhatsApp calls were placed.

## Live Windows smoke test

Close JARVIS normally and restart it to load the changed modules:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
$py = 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe'
& $py .\main.py
```

Use a normal Chrome window with no pending work. Say `Open Chrome`, wait for the result, then say `Close Chrome`. JARVIS should close the verified window and report its result. Remaining Chrome background processes alone are not a close failure. Do not expect unrelated Chrome windows or internal automation sessions to close.

Then test the sequences, waiting for each result:

1. `Open Chrome` → `Close Chrome` → `Open WhatsApp`.
2. `Open WhatsApp` → `Close WhatsApp` → `Open Chrome` → `Close Chrome`.

In a second PowerShell terminal, capture read-only evidence before and after:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
$py = 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe'
& $py .\tools\chrome_ownership_check.py
& $py .\tools\automation_trace.py --limit 100
```

`desktop_window_registered` identifies the selected app process/window. `app_window_close_requested` records the exact HWND targeted. `app_window_close_verified` means that window disappeared; it does not claim that every Chrome process exited. The task/turn/execution/source fields associate these events with the current command.

## Remaining limits and recovery

Live desktop verification is still pending. Elevated windows, inaccessible process details, unsupported Chrome window classes, invalidated window properties or pending browser dialogs can prevent verification. Remote-debugging/automation Chrome instances intentionally stay protected. With several windows and no unambiguous target, focus the intended Chrome window and repeat the command, or use the returned exact handle. A Chrome window already opened by the old JARVIS code may need a fresh `Open Chrome` command after restart to establish its window record.

Original files were backed up before editing under `upgrade-backups/chrome-ownership-20261022-135754`. The installation manifest records a second deployment backup and exact hashes. To roll back, stop JARVIS, restore only those original source files and remove only the newly added files listed in the manifest after checking for subsequent edits. Keep all configuration and memory/database files. Additional identity records are harmless to older source versions; they never schedule actions. Windows automatically removes the per-window property when that window is destroyed.
