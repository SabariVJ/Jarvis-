# Phase 5.2B — managed-runtime gate

Production execution is BLOCKED. This supersedes the earlier source-fed readiness conclusion in MXC_READ_ONLY_BACKEND.md. No live-test execution procedure is authorized while managed_powershell is false.

Fresh installed-code probes on Windows 26200.9711 / signed CLI 0.163.0 reproduced managed PowerShell failure before command execution: plain, encoded and help invocation all return 4294901760 with 8009001d. The error maps to NTE_PROVIDER_DLL_FAIL (provider DLL initialization failure). Native certutil SHA1/SHA256 succeed in the same root-deny profile. Thus this is not merely an encoded-command or execution-policy error, nor a blanket inability to run native cryptography. The exact provider DLL, attempted resource and whether the ultimate defect is MXC implementation or a denied initialization dependency remain unproven. No verified secure PowerShell remediation was found. No registry, execution-policy, credential or filesystem expansion was attempted.

The native shell probes are not evidence that Codex's managed shell works. Source-fed prompt construction also does not prove managed-shell readiness or completed inference. Disabling shell tools is retained as defense in depth, not accepted as a substitute for the mandatory runtime check.

Changes:
- core/mxc_backend.py: fresh managed PowerShell probe under explicit MXC, required in CHECKS and dispatch proof validation.
- core/codex_preflight.py: expose managed runtime failure, require every mandatory proof and base check, report selected probe policy accurately.
- tests/test_mxc_backend.py: startup failure/nonzero-with-marker regressions; simulated successful runtime remains explicitly mocked.
- downloads/mxc-runtime-work/phase52b_*.py: isolated non-inference diagnostics and full-suite runner.

Backup: upgrade-backups/phase52b-20261023 (three changed existing Python sources). Existing dirty user changes retained. Earlier failed task never executed or modified.

Fresh evidence under downloads/mxc-runtime-work:
- phase52b-runtime.json: full bounded synthetic runtime results, binary fingerprint, no inference.
- phase52b-readiness.json: installed preflight ready=false, managed_powershell=false. Approved source reads, private read/write denial, source write denial, task-temp write, positive-controlled loopback and non-loopback host-interface network rejection, native cancellation/heartbeat stop all passed limited probes.
- provider HTTP and WebSocket health probes passed through official CLI; this is more than local login, but not a completed model response or proof of quota. No authentication material was copied/read by these scripts.
- phase52b-automation.log / phase52b-providers.log / phase52b-tests.json: actual complete suite output and counts.

Initial run: 348 automation tests, eight failures caused by the runner's non-UTF-8 stdout encoding when existing action logging printed a Unicode triangle; zero errors. All 99 provider checks passed. The initial automation log is preserved as phase52b-initial-automation.log. The runner was corrected to UTF-8 without modifying desktop action implementations, and the complete suite rerun.

Second run: 347/348 automation passed, all 99 provider checks passed. The one failure exposed a reporting compatibility mismatch: requested_policy.backend was overwritten with the attempted backend. Corrected by preserving requested_policy and adding attempted_backend=mxc; no readiness relaxation. Second log preserved as phase52b-reporting-automation.log. A standalone policy run without the suite's isolated TEMP also exposed inherited .codex configuration in host TEMP; that was correctly refused. Final full run uses isolated TEMP and UTF-8.

Final full run: **348 automation tests passed in 177.785 seconds; zero failures/errors. All 99 provider checks passed.** Final installed preflight rerun remains ready=false, attempted_backend=mxc, managed_powershell=false. Existing failed-task evidence fingerprints were rechecked: zero mismatches. No real inference, project commit/push, calls, credential changes or permission broadening occurred.

Network checks are representative host destinations, not an exhaustive external-destination audit. Mandatory runtime remains failed; neither these probes nor an old report can authorize execution. Preflight/worker repeat current checks with short-lived binary/source/environment/policy bindings. Calls, messages, Git publication and failed tasks are not replayed.

Next safe action: obtain narrowly scoped provider-loader/sandbox failure tracing using synthetic source, excluding credentials and private session content, to identify the failing DLL/resource. An upstream compatible runtime fix must pass the same root-deny tests before any fresh owner-approved inference. No broad-read workaround or fallback is enabled.

Official runtime reference: https://learn.chatgpt.com/docs/windows/windows-sandbox — explicit MXC failure is not permission to fall back or broaden access. The OpenAI Docs review informed retention of explicit selection and separate runtime verification.
