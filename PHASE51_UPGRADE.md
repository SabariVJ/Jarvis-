# Phase 5.1 — supervised Codex execution and free local notifications

## Delivered and verified scope

Implemented on the existing JARVIS installation, not rebuilt. The official local
Codex CLI's Windows Authenticode signature is valid for OpenAI OpCo, LLC; existing
ChatGPT login was confirmed by official read-only CLI commands. No token file,
browser cookie, password or account setting was extracted/changed.

Supervised execution is implemented, rather than another planning stub. Real
Codex inference has NOT been submitted during testing, so account allowance,
provider response and the full native Codex sandbox path remain live-test items.
No claim of unlimited/free inference or a verified desktop coding run is made.

### Safe usage after a normal JARVIS restart

1. Say/type: `ask Codex to explain core/command_router.py`.
2. Inspect the returned prepared task ID and source scope. Only explicitly named
   project-relative source paths are copied; no whole-project/default upload.
3. Approve execution: `run Codex task <12-character ID>`.
4. Monitor: `show Codex task <ID>` or Settings -> TASKS & ALERTS… -> refresh.
5. Cancel: `cancel Codex task <ID>`. Cancellation polls about every 50 ms while
   running; source verification/finalization and OS teardown add overhead.
6. Review: `review Codex task <ID>`. Open its `proposal.patch` and `result.txt`.
7. For eligible non-sensitive files only, after reviewing the exact proposal:
   `apply Codex task <ID> review <64-character SHA-256>`.

IDs/digests are best copied into the typed command input. Ambiguous voice
transcriptions are refused, not guessed. Example implementation goal:
`ask Codex to refactor plugins/example.py and update tests/test_example.py`.
All files needed for imports/tests must be named in the goal. New source paths
can be explicitly included. Keep scope small to conserve existing account usage.

## Execution and publication boundaries

- Only this installed project directory is approved. Source-only copies live in
  `downloads/codex-tasks/<ID>/workspace`; originals and results remain alongside.
- Existing dirty/uncommitted bytes are snapshotted, not reset, stashed or edited
  by the coding process. Secrets/private paths, databases, backups, symlinks and
  reparse points are excluded with the existing Git security policy. Limits:
  200 explicit paths, 5 MiB source scope, 8 MiB CLI output, bounded JSON events.
- Native signed executable is fingerprinted at preparation and rechecked before
  use. The official CLI reuses its own authorized login; JARVIS does not copy it.
- CLI overrides force ChatGPT authentication, remove API keys/provider URLs/cloud
  credentials from the child environment, and never buy credits or switch to a
  paid API. Actual available account limits remain a server-side constraint.
- Each run uses ignored user config, strict configuration, no inherited project
  `.codex` configuration, disabled hooks/apps/plugins/image generation/browser/
  computer-use/automatic MCP installation/host skills, and no model-selected
  permission escalation. Shell networking is disabled.
- A restricted filesystem profile denies general reads, grants platform runtime
  reads and source-copy/temporary writes, and explicitly denies the auth folder.
  Native elevated Windows sandbox support is required. If unsupported or not set
  up, execution fails closed; no full-access/legacy fallback is attempted.
- CLI processes belong to an owned Windows Job Object with kill-on-close. The
  300-second timeout/cancellation terminates only that process tree. This job
  handle also closes if JARVIS exits. Existing unrelated apps are not targeted.
- `approval_policy=never` means out-of-scope operations are DENIED, not silently
  approved. Sensitive/destructive requirements must be handled manually with the
  owner. No automatic commit, push, Git history rewrite or publication exists.
- Runs share the existing task manager's persistent jobs, cancellation flags and
  two-worker limit. There is no competing scheduler. Fresh approved background
  jobs survive unrelated foreground commands; explicit cancellation still works.
- Start authorization is consumed before launch. Duplicate responses return the
  existing receipt. Interrupted/uncertain/limited jobs are never automatically
  resumed or retried. The CLI may perform its own bounded transport retries;
  JARVIS does not relaunch the coding task. A local CLI version rejected attempts
  to override built-in provider retry settings; those overrides were removed.
- Proposed changes undergo scope, secret and syntax checks. A verified completed
  CLI turn is not proof the requested software change is correct. Passing test
  evidence is reported only for observed CLI test commands with positive counts
  and successful exit, not model prose, zero tests or echo statements.
- Results explicitly distinguish proposed changes, applied changes, observed
  tests and unverified correctness. Sensitive paths (core, providers, config,
  memory, actions, main/UI, requirements, WhatsApp safety) require manual
  supervised application; the apply tool refuses them.
- Eligible apply requires a fresh exact review digest, unchanged proposal and
  unchanged live source hashes. It backs up existing files before per-file atomic
  replacement. Concurrent edits are refused. Multi-file apply is NOT globally
  atomic; partial writes are recorded and never automatically repeated.

Official documentation used for authentication, JSONL execution and restrictive
permissions: [authentication](https://learn.chatgpt.com/docs/auth),
[non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[permissions](https://learn.chatgpt.com/docs/permissions),
[Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox).

## Free notifications

Default routing is dashboard only. Configure existing Settings -> Plugin Settings
-> Free task notifications -> Event routes JSON, for example:

```json
{"started":["dashboard"],"progress":["dashboard"],"completed":["dashboard","desktop"],"failed":["dashboard","desktop"],"needs_attention":["dashboard","desktop"],"cancelled":["dashboard"]}
```

Add `speech` to selected events if desired. Speech uses existing offline Windows
SAPI through the system's speaker/voice, not Gemini or another inference service.
It is bounded, interruptible, and skipped/deferred when sleeping, muted or busy;
deferred speech is not repeatedly replayed. Delivery receipts record that state.
No physical speech playback was tested. The Windows tray uses installed Qt;
there is no new notification package, subscription or cloud transport.

Supported events: started, progress, completed, failed, cancelled, needs_attention.
Dashboard and history include verified execution outcomes and actual result paths.
Desktop receipt means queued/submitted, not proof the owner saw it; Windows Focus
Assist/settings can suppress visibility. Notifications are reserved persistently
before delivery, so reconnect does not announce them twice. An unverified
completion is labeled needs_attention. Invalid settings fall back to dashboard;
per-channel failures are reported rather than falsely claiming delivery.

Existing recipe completion notices also use these local routes when hosted by
JARVIS. No messaging channel is configured/assumed, and none was added: there are
no automatic WhatsApp messages, external notifications or task-completion calls.
`show notification settings` and `show notification history` are read-only tools.

## WhatsApp and paid calling

The existing exact contact/header checks, voice/video controls, 30-second
persistent cooldown, same-turn guard and uncertain-outcome no-retry behavior are
preserved. Added an early self-call guard for me/myself/my phone, `(You)` labels
and any explicitly configured self_contact/self_number. It does not guess a
third party's identity or infer calling permission from a coding task.
Unknown/localized own-account identities are not independently discovered; never
use an owner/self chat as a test recipient. No new background calling-rule engine
was enabled: fresh explicit call requests remain necessary.

Twilio/Exotel and paid phone transports remain unconfigured/disabled. No phone
number, subscription, API credits or other paid service was purchased/activated.
New notification paths cannot select a phone/messaging transport. Existing
credentials and voice-provider settings were not modified. The pre-existing
Gemini integration's account billing was not changed; native Gemini Live audio
selection remains model-mediated. Codex still uses existing account allowance,
not offline inference or an unlimited allocation.

## Exact source changes

New: core/codex_commands.py, core/codex_sandbox.py, core/codex_tasks.py,
core/local_notifications.py, plugins/local_notifications.py,
tests/test_phase51_execution.py, this report.

Modified: core/plugin_loader.py, core/codex_bridge.py,
plugins/codex_assistant.py, plugins/task_manager.py, core/command_router.py,
core/prompt.txt, main.py, ui.py, tests/test_automation.py,
plugins/whatsapp_control.py.

Discovery now reuses modules already imported from the same source path. The
full-suite investigation found that replacing task_manager in sys.modules could
duplicate worker pools and invalidate existing references. A regression verifies
that repeated discovery preserves the single shared pool. Plugin enablement is
still read dynamically; source-file updates require the normal restart.

Runtime, process_runner, Chrome ownership/app identity, Git publication/security,
wake engine/phrase and memory/config manager sources were not modified.
No user credentials/settings or automation history were removed or reset.

## Actual test results and performance

- Baseline: 204 automation tests OK; 99 provider checks OK.
- Final complete release: 246 automation tests OK (135.204 seconds); 99 provider
  checks OK. Optional Piper checks retain their existing dependency skips.
- The 42 new checks cover execution/copy/apply, login failure, quota failure,
  cancellation, timeout, actual local subprocess streaming/owned job isolation,
  duplicate/reconnect/recovery, dirty and concurrent edits, partial apply,
  secrets/scope/CLI policies, notifications/UI/offline speech mocks, routing,
  discovery worker-pool identity and WhatsApp self-call prevention.
- Earlier runs exposed the discovery replacement issue (fixed), a COM mock
  import-order error (test corrected), and allocation failures in the combined
  AST-heavy harness. The final full suite collects cyclic test fixtures between
  cases; no tests were omitted or product guards disabled. Final failures: zero.
- All model inference and communications were mocked. Local Python subprocesses
  exercised streaming/timeout/cancellation. Actual signed CLI/authentication and
  source preparation were checked without submitting a coding/inference request.
- No real call, audible notification, Windows toast visibility or live Codex
  coding task was tested. Existing Git tests create commits only in temporary
  repositories and mock remote operations; no project commit or push occurred.

Warm router benchmark (2,000 samples per command/version):

| Command | Before median / p95 | After median / p95 | Routing change |
| --- | --- | --- | --- |
| Ask Codex to explain a named file | 19 / 37 microseconds | 16 / 34 microseconds | fallback -> deterministic |
| Run Codex task ID | 18 / 37 microseconds | 17 / 39 microseconds | fallback -> deterministic |
| Show notification settings | 18 / 37 microseconds | 21 / 49 microseconds | fallback -> deterministic |
| Open Chrome | 9 / 18 microseconds | 9 / 18 microseconds | deterministic preserved |

This is not an end-to-end voice speedup claim. The useful verified improvement
is removing model planning for these exact typed/local-STT requests. Native audio
remains model-mediated. One real local preparation (source snapshot, signature,
CLI login checks only) took 2.899767 seconds; n=1, no median/p95 claim.
Historical dispatch median 14.418 ms/p95 16.996 ms (n=41); WhatsApp execution
median 29.143709 seconds (n=3, no p95). There are insufficient controlled UIA,
microphone/planning/reporting samples to claim a WhatsApp bottleneck fix.
Accessibility scans were not changed on speculation. Long historical reporting
samples are uncontrolled delays, not reliable spoken-response latency.

Evidence: downloads/phase51-work/performance.json, cli-preflight.json,
baseline-automation.log, baseline-providers.log, release-automation.log,
release-providers.log, installation.json and installed-smoke.json.

## Windows verification and rollback

C: remains critically low (roughly 16–19 MB during this work). Free space safely
before live use; no user files/caches were deleted and no paging/security settings
were changed. D: held backups/test files. Do not bypass a sandbox failure. If the
official CLI needs sandbox setup, review its interactive permissions/setup flow
with the owner after addressing disk space; this upgrade did not provision it.

Restart normally, then use the safe read-only analysis example above:

```powershell
Set-Location 'D:\Mark-LVI-main\Mark-LVI-main'
& 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe' .\main.py
```

Check CLI status, prepare a one-file explanation, inspect scope, explicitly run
the returned task ID, monitor result, and verify live source unchanged. If quota
is unavailable, stop/wait; do not buy credits or add API keys. Test cancellation
on a separately explicit task. Review desktop/speech opt-in independently; no
automated real call is part of this smoke procedure.

Backup: `D:\Mark-LVI-main\Mark-LVI-main\upgrade-backups\phase51-20261022-215418`.
Stop JARVIS before rollback. Preserve subsequent user edits, restore only the ten
modified source files from this backup, and move the seven newly added files
listed in installation.json to a recovery folder. Do not whole-tree reset,
restore credentials, delete memory or remove automation.db. Artifacts/history
are retained. Run the baseline suites and restart. Installation records every
deployed file/hash; no restart or Git publication was performed automatically.
