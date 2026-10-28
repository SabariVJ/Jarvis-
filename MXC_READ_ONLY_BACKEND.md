# Phase 5.2 — restricted MXC read-only source analysis

## Outcome and scope

Fresh non-inference readiness checks passed on the pinned signed Codex CLI **0.163.0**, Windows build **26200.9711**, with root denied, approved source read-only, private paths denied, task-temp writes only, shell network disabled and no approval escalation.

The supported mode is **source-fed read-only analysis with command execution tools disabled**. Approved copied source is supplied through stdin, so analysis does not require PowerShell or shell-based file reads. This is not a general coding, test-running, refactoring or shell backend. No real model turn was submitted during this upgrade; successful inference remains for the owner's separately approved fresh test.

## PowerShell diagnosis — no permissions broadened

Under the unchanged restricted MXC profile, Windows PowerShell fails with `8009001d` for help, plain commands and encoded commands. It fails before user script execution. The Windows `certutil -error` mapping is **NTE_PROVIDER_DLL_FAIL: Provider DLL failed to initialize correctly**.

Native SHA-1 and SHA-256 hashing of a synthetic approved file succeeded under the same policy. Therefore this is not an encoded-command problem or blanket unavailability of native hashing. Available evidence does not identify the exact provider DLL or missing resource; no specific denied file/registry key is asserted as the cause. We did not disable crypto/strong-name checks, modify registry/security settings, add filesystem permissions, or install another PowerShell runtime.

PowerShell itself is **not repaired**. Its dependency is removed from the supported read-only analysis path. The CLI can construct its analysis input context under MXC without PowerShell when command tools are disabled; that non-inference construction was verified.

## Important feature-contract finding

Codex 0.163.0 reports `shell_tool=false` but retains `unified_exec=true`, even after `--disable unified_exec` or a false config override. Initially this was conservatively treated as an unverified tool surface.

The [official configuration reference](https://developers.openai.com/codex/config-reference/) distinguishes the controls: `shell_tool` enables command execution tools, while `unified_exec` selects resumable execution and ignores user opt-outs. The readiness check now verifies the actual command-execution gate, **shell_tool=false**. It does not claim the unified engine flag becomes false, and does not use an ignored opt-out as a security control.

The candidate's command tools are disabled, and root-deny/network/write restrictions independently remain enforced. No shell fallback is enabled. The [official Windows guide](https://developers.openai.com/codex/windows/) describes explicit MXC selection; this integration selects `windows.sandbox=mxc` per verified task instead of enabling a preference that could silently choose a legacy backend.

## What was verified without inference

- Exact native CLI publisher/signature, pinned executable hash, version and supported interfaces.
- Read access to approved copied source and denial of opening it for writes. Probe writes use NUL/zero-byte opens, not source modifications.
- Read and write denial for generated private canaries outside approved roots, including a second canary in the host's normal temporary area. No real SSH keys, credentials, private memory or databases are opened as test canaries.
- Writes succeed in the owned task temporary directory; root and source permissions are not expanded.
- Both an owned loopback HTTP listener and an owned non-loopback host-interface listener are reachable by host-side positive controls but denied to sandboxed curl. Curl startup/version must succeed and each connection must return its specific connection-failure code 7: a missing/broken probe executable is not accepted as network denial. These are representative endpoint tests of the disabled-network policy, not an exhaustive test of every possible destination. No public target is contacted by the shell-network probes.
- Native MXC cancellation: an owned cmd loop updates a unique heartbeat in the task-temp directory; stopping the CLI/owned Windows job stops the loop/heartbeat. The check uses fresh names, not a previous marker. This is a bounded process-cleanup test, not a formal audit of every possible detached process topology.
- Native CLI read-only context construction succeeds with no model turn. Feature metadata confirms the command-execution gate is disabled.
- The official CLI reports an existing ChatGPT login. Its redacted `doctor --json` checks report provider HTTP reachability and a successful Responses WebSocket handshake. This verifies the broker transport, **not quota, model output, unlimited access or software correctness**. Shell network remains disabled; the trusted CLI's account/provider transport is distinct.

Credential contents were not read, copied or logged by the integration. The official CLI alone handles its existing authorized account; no login/logout, credential editing, browser cookies or paid API fallback was used. Health metadata is filtered to required status fields, not dumped as private account/session data.

Evidence: `downloads/mxc-runtime-work/runtime-probes.json`, `context-probe.json`, `doctor-checks.json`, `readiness-probe.json`. The readiness probe uses a fresh isolated fixture database and source copy, not a saved/live task. Its fixture ID is not an instruction to run a real task.

## Implementation

- New `core/mxc_backend.py`: fresh native scope/network/cancellation/context/feature/broker checks; exact binding; restricted candidate command; approved source input; process ownership/output bounds; dispatch validation.
- `core/codex_preflight.py`: exposes partial checks accurately, selects MXC only after every mandatory check passes, and permits testing only a fresh prepared read-only task or its already-approved active worker. Failed/consumed/completed tasks are not reactivated. The base signature/scope/interface checks still precede native verification.
- `core/codex_tasks.py`: uses the verified MXC command in the worker; constructs an unambiguous read-only prompt and supplies only approved copied source; rejects reparse points at the task-temp path as well as existing protected roots. Existing start-consumption, scope approval, receipts and no-replay rules remain authoritative.
- `core/codex_sandbox.py`: bounded temporary-file stdin replaces a potentially blocking pipe, preserving timeout/cancellation for larger source payloads. Input is capped at 1 MiB; source-fed analysis additionally caps approved bytes at 200,000. Temporary input/spools are cleaned at process exit. No credentials enter stdin.
- `plugins/codex_assistant.py`: reports successful preflight as readiness for explicit owner approval, not execution/completion; keeps blocked/failed states and actual evidence reporting.
- `core/codex_bridge.py`: local login is still not global execution readiness. Only fresh per-task checks can enable the restricted mode.
- New `tests/test_mxc_backend.py`; existing policy/execution fixtures updated explicitly for mocked attestations. Test-only mocked success is not installed as a production bypass.

### Binding and authorization

Readiness binds the approved binary hash, complete candidate command/profile, OS build, cleaned process environment, source manifest, goal/scope/read-only mode, execution ID and backend implementation hash. Source/binary/environment changes invalidate it. The worker repeats checks, and dispatch rejects incomplete, mismatched, future-dated or over-60-second-old proof.

No saved JSON file is accepted as a green-light token. `check Codex sandbox ID` is read-only, does not queue inference or consume a start, and does not change a prepared receipt. `run Codex task ID` still needs a fresh exact owner request; it repeats preflight before queueing. Its approved worker verifies again and validates proof immediately before dispatch. Old failed task `4a7bea7bb3ab` and all original evidence/approvals remain unchanged; no old task is rerun.

### Permissions

| Control | Restricted MXC mode |
| --- | --- |
| Backend | Explicit MXC after fresh checks, no fallback |
| Root | deny |
| Minimal runtime | read, unchanged |
| Approved source copy | read |
| Owned task-temp | write |
| Private Codex directory | deny |
| Shell network | disabled |
| Escalation | never |
| Command execution tools | disabled by shell_tool=false |
| Apps/plugins/MCP/browser/code-mode and other existing disabled capabilities | remain disabled |

Normal write-capable coding tasks remain blocked. No root-read workaround or path-deny-on-root-read equivalent is introduced.

## Tests and backup

Backup before edits: `D:\Mark-LVI-main\Mark-LVI-main\upgrade-backups\mxc-runtime-20261023-103250` (122 original source files).

Baseline: **321 automation tests passed in 164.970 seconds; 99 provider checks passed**.

Final: **346 automation tests passed in 123.397 seconds; 99 provider checks passed; zero failures.** This adds 25 regressions. Existing provider checks emitted the upstream pynvml deprecation warning.

Final complete results: `downloads/mxc-runtime-work/golden-automation.log`, `golden-providers.log`, `golden-results.json`. Installation requires both complete suites, original source fingerprints, unchanged original failed-task evidence, syntax and existing secret scans to pass. Tests cover automation, replay, Chrome ownership, WhatsApp/calling, Git security, wake, Codex, workflows and providers. New regressions cover all-check gating, binary/environment/policy/source/execution binding, proof freshness, cancellation, disabled command tools, grouped/contradictory doctor metadata, full source input and nonblocking timeouts, and mocked one-shot read-only worker completion/no replay.

Intermediate issues resolved: stderr was initially merged into non-inference JSON metadata capture and obscured context parsing; streams are now separated. CLI doctor groups checks by category, so the parser now handles that shape while rejecting contradictory health rows. The ignored unified-exec opt-out was corrected using the official feature contract. An existing mocked source-prompt fixture initially hid the function under test; the focused regression now exercises the real function. No production protection was bypassed to obtain passing tests.

No real inference, paid API activation, calls, project commits/pushes, administrator changes or environment installation were performed. Existing Git tests may commit only in temporary repositories.

## One fresh owner-approved read-only live test

1. Restart JARVIS normally to load the new modules.
2. Say: `ask Codex to read-only explain core/command_router.py without editing files or running commands`.
3. Inspect the **new** 12-character ID, exact one-file scope and `read_only=true`. Do not use an old failed ID, a fixture ID or a literal placeholder.
4. Say `check Codex sandbox NEW_ID`, substituting the actual new ID. Expect `ready_for_explicit_read_only_approval` in preflight, backend `mxc`, all mandatory checks true, root deny and command tools disabled. Any failed/unknown check must keep it blocked; do not loosen permissions or retry inference.
5. Only when you approve that one model task, say `run Codex task NEW_ID`. This is the first inference step and uses the existing ChatGPT allowance, not paid API fallback. Readiness does not guarantee quota.
6. Use `show Codex task NEW_ID` to inspect the result, or `cancel Codex task NEW_ID` to cancel. Require exit 0, actual `turn.completed`, a nonempty final explanation, `completion_verified=true`, unchanged source and no proposed changes. Inspect `result.txt`, `diagnostics.json` and events in that new task directory. No tests should be reported as run inside the CLI because command tools are disabled.
7. On any failure/uncertain outcome, preserve evidence and stop. Never replay an old call/task or reset `start_consumed`; never apply, commit or push this read-only test.

This upgrade establishes non-inference readiness only. A successful real read-only Codex response is not claimed until that separately approved test succeeds.

## Rollback

With JARVIS stopped under owner control, restore this patch's modified existing files from the backup after checking for newer owner edits. The new backend/test/report may remain unused. Preserve all task databases, approval flags, credentials, memory and original failed evidence. Rollback returns the prior permanently blocked backend, not a working elevated/root-read workaround.
