# Phase 6A — read-only GitHub Guardian increment

This is the first Phase 6 increment, **not completion of Phases 6B–6D**. The implementation is installed in the existing project. Live GitHub API access has NOT been verified: Git is available but `gh` is not installed/on PATH. No software was installed, account changed, external destination connected, repository selected, or real API request submitted by the implementation tests.

## Implemented

- Extend existing `github_git`, not a competing publishing tool. Existing Git/GCM operations and approval gates remain intact.
- Native official-interface `gh` discovery, Windows publisher-signature check and GET-only authenticated GitHub API requests. The CLI manages its credential store. No `auth token`, credential-helper extraction, browser cookie access or token logging. Environment token/debug overrides are not inherited.
- Separate Git/GCM capability from verified API account identity. Missing CLI, failed login, missing endpoint permissions and rate limits have specific failures. API health/status views explicitly identify cached state.
- Bounded owner repository discovery, following vetted github.com API next-page links. Discovery does not create a monitoring selection. An empty allowlist performs no polling.
- Fresh `watch github owner/repository` authorization, account/repository-ID binding, max ten selected repositories, explicit unwatch/pause/resume. Renames/account switches require selection again; no silent expansion.
- Per-repository conditional GETs and persistent reduced checkpoints. Branch/commit SHAs, open issues/bug labels/assignments/review requests, workflow outcomes, releases, recent deployments/latest status for at most three, Dependabot/code-scanning alerts when read permission exists. Never persist issue bodies, source code or workflow logs. Access failures and paginated/truncated sections are labelled, not reported as healthy.
- Health facts and heuristic next-task suggestions are separate. No claim of running tests, reviewing source, verifying write permission or deploying software.
- Existing task-manager recipes, triggers, journal and two-worker pool run 15-minute repository checks. No second scheduler. Missed intervals do not burst after restart. In-flight reads recheck selection/cancellation before accepting a checkpoint. Completed foreground commands cannot replay into a selection or publication.
- Existing local notification system receives generic, deduplicated aggregate alerts. No private repository names, issue titles or source in previews. Silent first healthy baseline; initial observed failures can alert. Subsequent changes are limited to one notice per repository per 15 minutes. No external sends, uncertain retries or paid transports.
- MARK-style GITHUB COMMAND CENTER in Controls: local snapshots, health/failure timestamps, discovered-but-not-selected repos, exact selection, pause/resume and ordinary command submission. Opening/refreshing the panel never makes a network request. Untrusted repository output is wrapped using the existing untrusted-data boundary and displayed as plain text.

## Changed source

- NEW `core/github_guardian.py`
- NEW `core/guardian_panel.py`
- `plugins/github_git.py`
- `plugins/task_manager.py`
- `core/command_router.py`
- `core/prompt.txt`
- `ui.py`
- NEW `tests/test_github_guardian.py`

Backups of changed existing files: `upgrade-backups/phase6-20261023/` preserving the preexisting dirty work. `core/local_notifications.py` was also backed up but not modified. No Git staging, real project commit, push, merge, release or deployment occurred. WhatsApp/Chrome/wake/Codex security implementations and owner configuration/memory were not edited. Tests used isolated journals and temporary repositories; existing Git regression fixtures make their own local test commits, never real project publication.

## Validation and measurements

Baseline full suites: 348 automation tests passed in 133.890 seconds; 99 provider checks passed.

New focused Guardian suite: 38 passed. Coverage includes account mismatch, missing/unsigned CLI, authentication failure, rate limits, malformed JSON, explicit selection, malicious issue text, optional endpoint failure, conditional 304, pagination labels, no first-sync flood, generic preview, reconnect/deduplication, completed-action replay, stale worker rejection (including remove/reselect revision binding before API identity access), cancellation, pause, unchanged unrelated jobs, publication exclusion, notification failure and local-only UI.

Initial full post-change run passed 383 automation tests and 99 provider checks. Final post-hardening results are in `downloads/phase6-work/final-automation.log`, `final-providers.log`, `final-tests.json`. See the final result below; do not interpret subprocess output from mocked services as a real GitHub connection.

**Final results: 386 automation tests passed in 141.596 seconds, zero failures/errors; 99 provider checks passed.** All eight changed/added implementation/test source files passed the existing source secret-policy/syntax checks, and rollback sources were verified present (`downloads/phase6-work/source-checks.json`).

Warm local deterministic route-only measurement (1,000 samples): median 0.0171 ms, p95 0.0310 ms. This excludes model planning, dispatch, tool execution and network. No comparable pre-Guardian feature baseline exists, so **no end-to-end speedup is claimed**. Actual repository-read spans now feed existing automation metrics; network durations await an owner-enabled live test.

`downloads/phase6-work/installed-diagnostic.json` records the actual missing-gh diagnostic against an isolated runtime. `guardian-command-center.png` is a visually inspected offscreen render of that real missing-CLI state, not proof the live desktop session was tested. An explicit installed Windows font was loaded for offscreen rendering only; no font was installed or changed.

## Owner verification procedure

1. Install the official GitHub CLI yourself or explicitly authorize its installation separately. No agent installation or account change was performed. Review authentication scopes; do not paste tokens into JARVIS or this chat.
2. In a new terminal verify `gh --version`, then `gh auth status --hostname github.com`. If login is needed, use the CLI's own owner-approved `gh auth login --hostname github.com --web` flow. The plugin never refreshes scopes or changes accounts automatically. Git/GCM success alone is not API authentication.
3. Restart JARVIS to reload declarations. Say `check github guardian connection`; confirm the returned identity is the intended account (SabariVJ). A signature/permission error must be resolved, not bypassed.
4. Say `discover github repositories`. Inspect the returned accessible page, including private/read/write permission distinctions. `show more github repositories` reads the next vetted page. No repository is selected by discovery.
5. If you choose the existing JARVIS repo, explicitly say `watch github SabariVJ/Jarvis-`. Then `check monitored github projects`. No writes, Actions runs or notifications to third parties are authorized by this selection.
6. Open Controls → GITHUB COMMAND CENTER, or say `show github project health` / `show me the broken builds`. Check sync timestamps, partial-section/permission warnings, and observed facts. A metadata endpoint success is not proof every optional endpoint succeeded.
7. `pause github monitoring` stops future scheduled reads and discards stale in-flight checkpoints; `resume github monitoring` enables only the saved selections. `unwatch github SabariVJ/Jarvis-` removes that selection. Unrelated long-running jobs retain their lifecycle.

## Remaining work / boundaries

- **6B not implemented in this increment:** personal vibe-coding profiles, provider-neutral generation, independently verified isolated drafting/test execution, reviewable patch/apply controls. Existing project_assistant and Codex systems are not silently activated as alternatives. A chosen repository/backend and tested isolation controls are prerequisites. Missing provider must remain awaiting-provider, not fake completion.
- **6C not implemented:** external Telegram/Discord/email adapters, destination verification, channel-priority/quiet-hour policy, delivery tests. None is connected; existing local routes remain unchanged. No WhatsApp messaging capability is claimed.
- **6D partially foundational only:** recurring safe read checks use existing scheduler, but daily digests, comprehensive notification policies and supervised multi-repository coding are not implemented. The “today” question currently returns timestamped recent observed changes (latest 30), not exhaustive daily history; this limitation is explicit in output.
- Monitoring runs only while JARVIS and the PC/network are available. No hosted monitor/webhook receiver exists. No webhooks were registered.
- List reads are bounded (10–30 recent entries per endpoint; discovery 50). Optional security endpoints may require account/product permissions; their absence is unknown, not “no security issues.” Local worktree mapping/divergence monitoring is not configured; use existing approved Git diagnostics for local/remote comparison. Removed-repository snapshots remain historical journal data, not active monitoring.
- No actual authenticated API polling, Windows voice command, live dashboard interaction or notification delivery was exercised. These require the owner's explicit setup and repository selection above.
- Codex execution remains under its existing mandatory managed-PowerShell preflight block. Guardian has no path to bypass it.

References used: GitHub CLI API interface, https://cli.github.com/manual/gh_api ; GitHub conditional requests, serial polling and rate-limit guidance, https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api . These inform request handling, not a claim of verified account access.
