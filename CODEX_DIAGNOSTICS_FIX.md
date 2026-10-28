# Codex execution diagnostics and completion verification

## Finding: original execution cause remains undetermined

Task `4445d76a0bcd` remains `needs_attention`, with `start_consumed=true`. It was not launched again. Its original `events.json` has exactly two `diagnostic` markers and no retained message. `cli_result` is absent. No original stderr, subprocess return code, exact argv/environment receipt or valid terminal Codex event was preserved.

Confirmed defects in the previous runner:

1. stderr was merged into JSONL stdout, so normal error output became unparseable JSON diagnostics.
2. Those diagnostics were classified generically; their messages were then omitted by the worker's event recorder. Empty `item_type` on a diagnostic is not proof of a Codex item/parser fault: a diagnostic has no item.
3. The worker saved `cli_result` only after passing completion verification, discarding the failed execution's return code.

These establish why the failure was not diagnosable, not why Codex itself failed. There is no retained evidence proving authentication, quota, path, network, timeout, configuration or sandbox initialization as the underlying cause.

The original approved binary still exists, reports `codex-cli 0.163.0`, and its SHA-256 matches the saved approval (`2692c8c7bb2cf8b0aefe638741fc90236f4b824c59578ce84994f9de58200993`). PATH currently selects another installed Codex location; the failed receipt was not rebound to that executable. Only version/help commands were run, not inference or login changes.

New `downloads/codex-tasks/4445d76a0bcd/investigation.json` records the missing evidence, original event/receipt hashes and a **reconstruction**, not a recovered historical argv. Its reconstruction uses the saved executable/workspace and the existing command builder. Historical CODEX_HOME/environment was not recorded. No prompt, account files or tokens were accessed or recorded. Original events and job receipt are preserved.

## Changes

- New `core/codex_diagnostics.py`: bounded sanitization, error categories, allowlisted event metadata, safe command-policy receipts and diagnostic summaries. It omits raw event bodies, reasoning, account/session IDs, environment and stdin. Credential headers, token/password assignments, recognizable token formats, URLs and private Codex paths are redacted.
- `core/codex_sandbox.py`: separate stderr and JSONL files; bounded head/tail stderr retention; actual return codes on normal and exceptional exits; launch/isolation/stdin/runtime phases; event-type counts and last valid metadata; exact CLI rejection messages when available; timeout/cancellation evidence. Temporary raw process spools are removed at stream exit; permanent evidence is sanitized. The 8-MiB combined transport limit and 256-KiB event limit remain.
- `core/codex_tasks.py`: persist CLI evidence before verification, including failed/interrupted executions; sanitized `diagnostics.json` and detailed `events.json`; specific task/activity/notification errors; legacy status explains missing evidence without mutating or replaying the job.
- `plugins/codex_assistant.py`: `show Codex task ID` exposes the supported diagnosis and evidence path; deterministic narration retains the actual error.
- New `tests/test_codex_diagnostics.py`: local Python-process and mocked-worker regressions. No test invokes a Codex model.

Inspected but unchanged: `core/codex_bridge.py`, `core/codex_commands.py`, `core/process_runner.py` and the existing task manager. No competing worker, scheduler or receipt system was introduced.

### Completion rules

Success requires zero subprocess exit status, an actual `turn.completed`, a nonempty completed agent message, no terminal failure/interruption, and no malformed JSONL. A success marker followed by an error/nonzero exit fails. Diagnostic-only, partial and empty-result streams never count as completed. A complete final JSON object without a trailing newline is accepted; truncated JSON is not. CLI-observed completion and proposal checks do not establish software correctness or a passing test run.

This follows the CLI event model in [official non-interactive documentation](https://developers.openai.com/codex/noninteractive/). App-server slash-separated notifications are a different protocol and are not substituted for CLI JSONL events.

### Security and sandbox status

Signed native executable preparation, pinned executable hash, source-only copies, fresh explicit run approval, process-job isolation, cancellation, consumed starts, no automatic replay, no paid API fallback and separate publication/application approval remain. No credentials, configuration, memory, history or WhatsApp protections were modified.

`windows.sandbox=elevated`, denied private reads and disabled sandbox network remain. A verified Windows Job Object is labeled as process-tree isolation, **not** proof of Codex sandbox initialization. The new diagnostics capture actual sandbox failures if encountered, but no live Codex sandbox command or setup was run here. The old evidence cannot establish whether initialization was attempted or succeeded.

The [official Windows documentation](https://developers.openai.com/codex/windows/) describes administrator-approved setup for legacy elevated mode. The installed 0.163.0 `sandbox --help` did not advertise the current documentation's `sandbox setup` subcommand. Do not copy a newer-version setup command blindly. If a fresh approved run reports a setup/managed-policy error, use the supported setup flow for the installed signed CLI with owner/admin approval; retain elevated restrictions and denied-read/network boundaries. Do not switch to unelevated/full-access or disable protections to get a success result.

Explicit goals beginning `read-only ` now persist `read_only=true`; their copied source workspace is granted read access rather than write access. Only the separate task temporary directory remains writable. Other tasks retain their existing scoped workspace-write profile. This is a stricter mode for the requested smoke test, not a security fallback.

## Backups and tests

Backup before editing: `D:\Mark-LVI-main\Mark-LVI-main\upgrade-backups\codex-diagnostics-20261022-235331` (118 original source files).

Baseline: **277 automation tests passed in 114.101 seconds; 99 provider checks passed**.

Final: **304 automation tests passed in 115.774 seconds; 99 provider checks passed; zero final failures.** This adds 27 diagnostic/read-only regressions. The final 27 focused regressions also passed after the synthetic secret-scan fixture formatting adjustment. Existing provider checks emitted the upstream pynvml deprecation warning.

Final results: `downloads/codex-diagnostics-work/verified-automation.log`, `verified-providers.log`, `verified-results.json`. Installation is gated on both complete suites passing. Suites cover automation, replay, Chrome ownership, Git, routing, calling, wake, workflows, Codex and provider integrations. Syntax/secret checks and original source fingerprints are validated before installation.

New regressions cover nonzero exits, zero exit without completion, no final message, malformed/truncated/final-no-newline JSONL, argument/config rejection, sandbox failure, authentication/quota/network failure, interruption/error followed by completion, process launch and process-job failure, timeout/cancellation return codes, diagnostic-only streams, bounded stderr, redaction/session metadata, persisted worker evidence, legacy status, truthful narration and read-only scope. Existing Codex regressions retain duplicate/stale execution and no-replay coverage.

Intermediate issues resolved: a direct focused test invocation used the default C: temp location, whose ancestor contains `.codex`, correctly triggering the inherited-configuration refusal. Re-running with the existing isolated D: test-temp environment passed. A fixture initially retained its Python test code in the command receipt; non-Codex fixture arguments are now omitted. Process stdin is explicitly closed when isolation fails. The installation secret scan also correctly flagged synthetic test values assigned to a variable named `secret`; the fixture variable was renamed, without weakening the scanner or removing its redaction assertions. No production security check was bypassed to make tests pass.

No real Codex inference, real calls, credential changes, project commits or pushes were performed. Some existing Git tests create commits only in isolated temporary repositories. Test-suite wall time is not an inference-speed benchmark.

## One fresh, explicitly approved read-only smoke test

Not performed by this upgrade. This will use the existing ChatGPT account allowance, not paid API fallback; login alone does not prove quota.

1. Restart JARVIS so it imports the modified modules. First say `show Codex task 4445d76a0bcd`. Expect the legacy evidence limitation, not a retry or an invented cause.
2. Say: `ask Codex to read-only explain core/command_router.py without editing files or running shell commands`.
3. Inspect the prepared scope and **new** 12-character task ID. It must name only `core/command_router.py`, be `prepared`, and have `read_only=true`. Preparation does not start inference.
4. Only when you explicitly approve that one test, say `run Codex task NEW_ID`, replacing NEW_ID with the newly generated ID. Do not use the old failed ID or a literal placeholder. Do not apply, commit or push anything.
5. Use `show Codex task NEW_ID` for progress and the final diagnostic. Cancel if needed using `cancel Codex task NEW_ID`. A timeout or uncertain outcome must not automatically retry.
6. Inspect `downloads/codex-tasks/NEW_ID/diagnostics.json`. For verified completion require exit 0, `completion_verified=true`, `turn.completed`, a nonempty final agent result, no failure/malformed events and no proposed changes. `result.txt` must contain the actual read-only explanation. If any check fails, preserve the evidence and stop; do not weaken sandbox restrictions or repeat the task automatically.

The live Windows sandbox, model transport, available account quota and successful real task execution remain unverified until this separately approved test actually succeeds.

## Rollback

Stop/restart JARVIS only under owner control. Restore backed-up `core/codex_sandbox.py`, `core/codex_tasks.py` and `plugins/codex_assistant.py` if needed, checking for subsequent owner edits before replacement. The new diagnostics module/test/report can remain unused. Preserve existing task evidence, databases, credentials and settings. Rollback returns the old diagnostic limitations; it must not reset `start_consumed` or replay any task.
