# Previous-task replay fix

The replay defects were reproduced against the pre-fix source with mocked application launches. The corresponding regression passes against the corrected source. No real WhatsApp calls were placed.

## Confirmed causes in the implementation

1. `new_turn()` changed the shared turn ID without invalidating earlier work. Cancellation checked only a global generation counter. A delayed worker holding an old turn context could therefore launch WhatsApp after a newer command arrived.
2. `main.py` assigned the current shared context to incoming tool calls. The typed shortcut captured that context only when its asynchronous callback eventually ran. Old actions could acquire the identity of a later command.
3. Execution receipts included provider call IDs, so changing an ID bypassed duplicate protection. The local pipeline's `send_tool_response()` also accepted responses that were no longer outstanding; when its awaiting set was empty, another identical response started another model generation.
4. The close handler checked the originally selected processes but did not re-scan for remaining or recreated application processes. WhatsApp can also remain running after a window-close request; the close result must reflect that failure rather than assume it exited.

The baseline reproduction printed `True` for both an old worker executing after a new turn and an old action rebound to the current turn. This establishes the software defects, not the cause of a specific historical GUI incident. A read-only check found no `memory/automation.db` at the specified live path during this investigation, so no historical execution trace was available to classify that incident.

## Corrected behavior

Each user command gets a task, turn and execution identity. New commands supersede prior foreground commands. Completion seals the command; already admitted tool batches can drain, and later batches are refused. Reconnects interrupt foreground authorization. Explicit workflow jobs have separate lifecycles and remain authorized across unrelated commands and reconnects. A cancelled, completed or obsolete worker cannot update a newer job execution.

Receipts use execution identity, explicit step identity and canonical arguments. They are read as results, never as queued work. App aliases share identities. A recipe's repeated steps remain distinct. The dispatcher and short Windows side-effect boundaries recheck authorization. Typed callbacks retain the context captured when the user submitted the command. Local provider messages carry their original context; observed native-provider call IDs are also bound to commands. Unknown or duplicate tool responses cannot restart generation.

Known English app commands such as `Close WhatsApp and open Google Chrome` are parsed into ordered close/verify/open steps. Failure to verify closing WhatsApp does not prevent opening Chrome, and both actual results are retained. Desktop actions across model batches execute in order. Weak window-title matches are excluded from closing as well as opening.

WhatsApp retains exact contact and header verification, the persistent 30-second cooldown, same-turn duplicate-call protection, and no automatic call retries. A fresh explicit request naming the contact is required before a call. A restored conversation or uncertain outcome provides no new call authorization.

## Files changed

| File | Purpose |
|---|---|
| `core/automation_runtime.py` | Lifecycle records, canonical receipts, call provenance, execution events and side-effect guards. |
| `core/command_router.py` | Canonical app arguments and ordered app-command parsing. |
| `core/process_runner.py` | Check lifecycle immediately before spawning a task-owned process. |
| `plugins/task_manager.py` | Independent job contexts, step identities and stale-worker ownership checks. |
| `plugins/whatsapp_control.py` | Check authorization at launch, search, selection and call boundaries. |
| `actions/open_app.py` | Guard launches, URI handlers, desktop search and focusing. |
| `actions/computer_control.py` | Exclude weak close targets and verify remaining processes. |
| `core/appidentity.py` | Guard actual Windows close requests and trace process identity. |
| `providers/base.py` | Local event provenance field. |
| `providers/engine_pipeline.py` | Preserve command provenance; ignore late/duplicate responses; ordered local routes. |
| `main.py` | Freeze callbacks, serialize batches, reject stale sessions and close command lifecycles. |
| `tests/test_automation.py`, `tests/test_integration.py` | Adapt fixtures to explicit authorization and include the actual typed command method in dispatch tests. |
| `tests/test_replay.py` | 25 additional replay/lifecycle regressions using mocked disruptive actions. |
| `tools/automation_trace.py` | Read-only event and process viewer. |
| `REPLAY_FIX.md` | This report and smoke-test instructions. |

## Verification

- 57 automation/integration tests passed, 0 failed: 32 existing tests plus 25 new regressions.
- Existing provider suite: 99 checks reported passed, 0 failed; optional Piper checks remain skipped by that suite.
- Coverage includes open/finish/close/open; no WhatsApp relaunch while opening Chrome; close failure and recreated processes; changed provider IDs; canonical app aliases; delayed workers; stale typed callbacks and sessions; repeated responses; ordered multi-step commands; explicitly repeated open/close/open; independent jobs; cancellation and recovery; non-destructive legacy database migration; and call cooldown/replay protection.
- Automated tests use temporary databases and mock desktop changes and calls. The integration suite converts synthetic audio with FFmpeg and uses an in-process MCP mock. Provider settings checks run only against a disposable staging configuration.

## Windows smoke test

Restart JARVIS normally to load the installed files:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
$py = 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe'
& $py .\main.py
```

First type, then repeat by voice:

1. `Open WhatsApp` — wait for its reported result.
2. `Close WhatsApp and open Google Chrome` — expect a separate close result followed by the Chrome result. WhatsApp must not be launched by the completed first command. If WhatsApp remains running, the close result must say it failed or is unverified.
3. `Open Notepad` — the previous WhatsApp action must not run again.
4. To exercise a delayed callback, submit `Open WhatsApp` and immediately submit `Close WhatsApp and open Chrome`. The superseded action must be rejected if it has not reached its launch boundary.
5. If testing reconnection through the app's normal reconnect control, do so after the first command finishes. Restoring the conversation must not authorize another launch or call. Do not issue any call command unless you intend to contact that person.

In a second PowerShell terminal, inspect the actual processes and recent events:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
$py = 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe'
& $py .\tools\automation_trace.py --processes --limit 100
& $py .\tools\automation_trace.py --summary
```

If `JARVIS_AUTOMATION_DB` points to a different database, supply its path with `--db` to the viewer.

Events include task ID, turn ID, execution ID, request ID, source, state, tool and canonical-argument fingerprint. `app_close_verified` records requested and remaining PIDs. An `app_process_launch`, `app_uri_launch`, `app_launch_enter` or `whatsapp_app_launch` after that identifies an assistant launch, with its owning execution/source. `receipt_reused` and `action_rejected` indicate suppression, not a launch. A remaining process without a later launch event indicates the close was not verified; a new process without any assistant launch event may have been started externally. Workflow events distinguish intentionally scheduled work from foreground commands.

## Limits and recovery

Live desktop and actual provider reconnect behavior still require the smoke test above; automated results do not establish that WhatsApp will honor every close request. Native Live events do not contain the local generation identity, so stored call IDs, command lifecycle checks and current app/call intent constrain restoration; a previously unseen call ID cannot independently reveal its original generation. Deterministic routing recognizes known English app names; app opening and call authorization require an explicit current request. Ambiguous/unsupported wording may require rephrasing. Calling by a previously mentioned contact or a bare “yes” is intentionally insufficient.

An explicitly scheduled recipe containing `open_app` can still open that application under its own job lifecycle. It is not cancelled merely because the foreground user closes the app. Inspect the source/task ID and cancel that job or disable its trigger if desired. Cancellation does not undo an OS action that already completed.

The existing database is migrated additively when JARVIS next starts; receipts, call guards, records, credentials and user settings are not reset. The new local event table can include tool-result excerpts and should be treated as private.

Original files were backed up before source editing under `upgrade-backups/replay-fix-20261022-101330`. The installation manifest records an additional deployment backup, exact changed paths and hashes. To restore, close JARVIS, restore only the backed-up source files, and remove only newly added files listed in that manifest after checking for subsequent edits. Do not remove or replace configuration or memory folders.
