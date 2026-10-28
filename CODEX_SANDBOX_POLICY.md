# Codex Windows sandbox policy conflict: verified findings and fail-closed guard

## Confirmed cause

Failed task `4a7bea7bb3ab` launched its pinned signed native CLI, then exited **1** during session initialization. Saved stderr states: `elevated Windows sandbox requires effective :root read access (code -32603)`. No JSONL completion was emitted. Its requested profile explicitly denied root, permitted minimal runtime reads, granted copied source read access and task-temp writes, denied the private Codex directory, disabled sandbox network and disallowed approval escalation.

This is a confirmed **backend/policy incompatibility**, not evidence of an authentication, quota or project-path problem. Root access was not broadened. The original task record, approval flags, events and diagnostics are unchanged.

## Native compatibility findings (no inference)

- Pinned CLI: official `codex-cli 0.163.0`, valid Windows signature, publisher `OpenAI OpCo, LLC`. Its `sandbox --help` supports explicit permission profiles and managed requirements.
- Windows registry: display version 25H2, build **26200.9711**. The registry ProductName still says Windows 10 Pro; the build is used for compatibility, not that stale label.
- The [official Windows sandbox documentation](https://developers.openai.com/codex/windows/) identifies MXC support beginning with CLI 0.162 and appropriate Windows 11 builds. Version/build eligibility alone does not prove isolation.
- An explicitly selected MXC echo probe initially failed: `MXC requires nonempty Unicode host SystemDrive`. The existing environment allowlist omitted this nonsecret OS variable. Supplying it made the echo probe succeed, exit 0, without changing saved settings. The production environment allowlist now retains/derives SystemDrive; it still drops API keys, tokens and provider URLs.
- A synthetic native cmd/curl probe used **root-deny, source-read, temp-write, network-off**, an empty per-probe CODEX_HOME and no copied credentials. Results: approved source read succeeded; opening source for writing was denied; reading an unapproved private canary was denied; owned temp writes succeeded; an owned loopback HTTP listener was unreachable from the sandbox, while its host-side positive control succeeded. Source content stayed unchanged. No owner credentials, memory, databases, SSH keys or other repository files were opened by these probes.
- A separate PowerShell probe failed under that same restricted MXC policy: `Loading managed Windows PowerShell failed with error 8009001d`. The policy was not loosened to make it run. The preceding inline-cmd quoting experiment also failed and is retained as an inconclusive probe, not an isolation success.
- These are bounded canary results, **not** proof of full PowerShell compatibility, all network destinations, authenticated broker connectivity or native cancellation/descendant cleanup. MXC remains a **candidate only**, not an enabled execution backend.

Probe evidence is under `downloads/codex-policy-work/`: `compatibility.json` (initial missing-SystemDrive failure), `compatibility-systemdrive.json` (MXC echo), `isolation.json` (PowerShell failure), `native-isolation.json` (inconclusive quoting experiment), and `native-isolation-cmd.json` (valid native canary test).

The echo and native probe used a new empty temporary Codex home. They never loaded/copied account authentication files or submitted a model request. Temporary canaries and probe configuration were cleaned up only inside their generated directories. No administrator setup, firewall changes, subscriptions or inference were performed.

## WSL2 evaluation

An existing **Ubuntu WSL2** distribution is registered. A read-only mount/interop metadata check found three Windows filesystem mounts and Windows interop enabled. It was started only for that metadata check, not configured as a JARVIS backend. The ordinary distribution is therefore **not an independently restricted replacement**: WSL2 by itself does not remove access to the owner's Windows files or interop tools.

No distro was installed, automount/interop setting changed, account credentials copied, or new environment enabled. A future isolated environment would need a reviewed mount namespace/host-path boundary, blocked interop, restricted egress, a trusted authentication broker and explicit approved source-copy mounts. That is a separate owner-approved deployment, not a silent fallback.

## Implemented security/reporting changes

- New `core/codex_preflight.py`: read-only signature/pinned-binary checks, exact CLI help/interface checks, Windows build eligibility, inspection of the actual generated profile overrides, approved source-copy hash checks, explicit per-requirement verification status and a specific blocking diagnosis. It distinguishes requested overrides from OS-enforced isolation. Unknown enforcement, authentication connectivity and cancellation checks remain false, not optimistically passed.
- **No backend is enabled by this patch.** Production preflight deliberately returns `ready=false` until a separately verified backend implementation satisfies every requirement. It does not trust a saved probe JSON, model assertion, build number, successful echo or local login as authorization/readiness.
- `core/codex_tasks.py`: preflight runs before queuing/consuming a fresh start, and defensively at worker entry. A blocked request returns a read-only `blocked` view with the stored state and safe next action; no prepared receipt is changed. Consumed tasks are never relaunched. Existing scoped approval, source hashes, process isolation, cancellation and no-replay behavior remain.
- `core/codex_commands.py` / `plugins/codex_assistant.py`: `check Codex sandbox ID` routes directly to the read-only preflight. Status/narration projects a terminal subprocess failure as `needs_attention`, even if a cached receipt says queued/running. It refreshes current task evidence before narrating stale queued/running data. Saved receipts are not rewritten to correct the display.
- `core/codex_bridge.py`: a stored login is reported independently of execution readiness. Status and preparation text say execution is blocked pending verified isolation rather than implying login means ready.
- `core/codex_sandbox.py`: only the SystemDrive environment compatibility fix. **No sandbox/backend, root, source, private-directory, network or approval permissions were broadened.**
- `tests/test_codex_policy.py`: compatibility, fail-closed isolation, source/privacy, stale narration, authorization and no-replay regressions.
- `tests/test_phase51_execution.py`: its existing mocked execution fixture explicitly mocks a successful test-only preflight so older execution/apply/timeout regressions can still exercise their intended paths. This is not a production bypass or a claim that live isolation passed.

### Exact permission differences

| Setting | Before | Installed patch |
| --- | --- | --- |
| Backend override | elevated | elevated retained, execution blocked before dispatch |
| Root | deny | deny |
| Minimal runtime | read | read |
| Read-only source copy | read | read |
| Normal coding source copy | scoped write | unchanged, also blocked until verified |
| Owned task-temp directory | write | write |
| Private Codex directory | deny | deny |
| Sandbox network | disabled | disabled |
| Approval policy | never | never |
| SystemDrive OS metadata | omitted | retained/derived; no credential data |

The **proposed** future backend difference would be explicit `elevated` to `mxc`, not auto-selection or fallback, with the same root-deny/source/privacy restrictions. It is **not installed or activated** here. Path-specific deny rules on a root-readable profile are not equivalent to deny-by-default privacy. Any root-read workaround would expose previously unapproved Windows files and requires a separate explicit owner decision; this patch neither proposes it as safe nor applies it.

## Tests, backups and preservation

Backup before edits: `D:\Mark-LVI-main\Mark-LVI-main\upgrade-backups\codex-policy-20261023-100413` (120 original source files).

Baseline: **304 automation tests passed in 181.404 seconds; 99 provider checks passed**.

Final: **321 automation tests passed in 163.318 seconds; 99 provider checks passed; zero failures.** This adds 17 policy regressions. Existing provider checks emitted an upstream pynvml deprecation warning. Syntax and existing secret scans passed for all nine installed files.

Final full results: `downloads/codex-policy-work/verified-automation.log`, `verified-providers.log`, `verified-results.json`. Final installation requires both suites to pass, unchanged baseline source fingerprints, original failed-task evidence hashes, syntax checks and existing secret scans. The suites include automation, replay, Chrome ownership, Git, WhatsApp/calling, wake, Codex, workflow and provider regressions. New policy tests mock CLI/host probes and do not infer or place calls.

No real model inference, credential changes, project commits/pushes, phone calls, administrator changes or environment installation. Existing Git tests may commit only inside isolated temporary repositories. No automation database is deleted or reset. Failed task receipts/evidence are checked read-only before and after installation.

## Safe procedure for one fresh read-only Codex test

1. Restart JARVIS normally. Say `show Codex task 4a7bea7bb3ab`. Expect the real terminal sandbox error, `needs_attention`, evidence path and no replay.
2. Say `check Codex sandbox 4a7bea7bb3ab`. This uses signed CLI version/help and policy/source metadata only. Expect `blocked`, not queued/running, with the elevated/root-deny conflict and unverified checks. It does not edit the failed receipt or submit inference.
3. If preparing a new scope, say `ask Codex to read-only explain core/command_router.py without editing files or running shell commands`. Inspect its new ID, exact one-file scope and `read_only=true`. Preparation is not inference.
4. Say `check Codex sandbox NEW_ID`, replacing NEW_ID with that actual ID. **Currently this must remain blocked. Do not approve execution while any required check is false.**
5. A real inference smoke test is only safe after a separately reviewed backend change has resolved the MXC runtime issue and independently verified source/private canaries, full required network restrictions, authenticated broker connectivity and native cancellation/process cleanup. Those checks must be bound to the same signed CLI/profile/source snapshot, not a reused task or saved success marker.
6. Only then, with explicit owner approval of that one fresh read-only task, say `run Codex task NEW_ID`. Verify exit 0, actual completion/final message, unchanged source copy/live files and `diagnostics.json`. Stop on any failure; never broaden permissions or replay the old failed task.

There is deliberately **no immediately runnable, verified inference backend** in this delivery. The confirmed unsafe/incompatible path is blocked rather than disguised as a working solution.

## Rollback

With JARVIS stopped under owner control, restore only this patch's modified files from the backup after checking for newer owner edits. Keep all original failed-task records/evidence, credentials, memory and settings. Leaving the new module/tests/report unused is harmless. Rollback restores the incompatible elevated launch path and is not a security or execution remedy; never reset approval/start flags.
