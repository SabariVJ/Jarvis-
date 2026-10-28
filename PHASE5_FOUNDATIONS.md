# Phase 5 — first increment: calling and Codex foundations

Implemented against the existing Windows installation on 2026-10-22. This is
NOT a claim that the complete Phase 5 conversational/autonomous system exists.

## What changed

- Added `core/calling_manager.py` and `plugins/calling_manager.py`: local status,
  authorized contacts, persistent call history, explicit notification calls,
  read-only provider connectivity and acknowledged call status refresh.
- Optional official Twilio SDK adapter: account-owned or provider-verified caller
  ID, explicit E.164 recipients, AI disclosure, XML-escaped owner-requested text,
  recording off, bounded HTTP and zero automatic creation retries. SDK is NOT
  installed by this upgrade; dependency range is in `requirements-calling.txt`.
- Persistent attempts use existing automation.db records/guards. No database was
  reset or schema replaced. Telephone calls share the existing global 30-second
  call cooldown. One phone attempt per turn, including uncertain submissions.
- Exact fresh command/message and live execution authorization are checked before
  reservation and immediately before transport. Completed/cancelled/stale jobs
  cannot dial. Provider preflight cancellation is tested. An already-submitting
  network request cannot reliably be recalled; no promise of remote cancellation.
- `core/codex_bridge.py`, `plugins/codex_assistant.py`: official CLI version and
  `codex login status`, with no credential-file/browser-session access. A coding
  delegation PLAN can be prepared, but no coding subprocess is executed yet.
- `core/command_router.py`: enabled-schema-validated Calling/Codex shortcuts and
  narrow phone-notification authorization. Disabled tools are not invented.
- `providers/engine_pipeline.py`: deterministic evidence-based narration for the
  new tools; no extra model call to contradict their status/failure. Native
  Gemini Live audio selection remains model-mediated.
- `ui.py`: Settings -> CALLING… local-only panel, manual local refresh, provider,
  caller ID verification status, permissions, contacts, history/state/duration
  when known, diagnostics and explicit audio/schedule limitations. No network
  polling, dialing button, credentials test or provisioning on panel open.
- `core/prompt.txt`: truthful capability/account/permission distinctions.
- `tests/test_automation.py`: plugin discovery expects 15 bundled plugins.
- `tests/test_phase5_foundations.py`: 28 new offline regressions.

Existing WhatsApp, desktop ownership, task manager, runtime, process runner,
wake engine, main.py and Git publication implementations were not edited.
Existing user settings/credentials were not changed. No calls, model inference,
account activation, provisioning, commits or pushes were performed.

## Architecture and intentionally unavailable features

Foreground command -> actual enabled tool schema -> runtime execution receipt ->
exact call intent -> authorized recipient -> provider caller-ID preflight ->
persistent irreversible-attempt guard -> one provider submission -> actual
acknowledgement/state. Status refresh is read-only. Call history is private in
the already excluded automation database. Messages are not stored in call-history
records, but normal private runtime receipts can contain tool arguments.

Incoming HTTP handling is only a disabled integration seam: official SDK signature
validation over the exact public HTTPS URL and all form fields, account check,
then Reject. No webhook listener, public tunnel or incoming account setting was
created. This is NOT an incoming answering implementation. Signature validation
is delegated to the official SDK; tests mock its contract, not its cryptography.

Not implemented/enabled: two-way telephone AI/media bridge, incoming answering,
caller Q&A, message transcription/recording, summaries/transfers, provisioning,
scheduled/proactive/completion calls, automatic Codex editing/delegation,
new workflow notification rules, WhatsApp end/status UI extensions, or new
voice streaming/interruption/concurrency behavior. These need further increments.
The persistent task manager still rejects calls and Codex plans in replayable
recipes; no competing scheduler was created. Never treat general task delegation
as call permission. Consumer WhatsApp desktop remains a separate channel.

## Provider comparison and cost boundary

Country/jurisdiction and existing provider account remain to be confirmed.
Asia/Calcutta timezone is not proof of operating jurisdiction. The Twilio adapter
is a prototype choice, not an approved provider purchase or India-number promise.

| Provider | Relevant support | Cost/availability boundary |
| --- | --- | --- |
| Twilio | Programmable Voice, provider caller IDs, call state reads and signed webhooks; adapter included | Published India page lists outbound mobile $0.0496/min and local $0.0699/min; international numbers start at $1.15/month. Actual country, number, tax, account and feature charges vary. |
| Exotel | India-oriented voice services and voice streaming; possible candidate if Indian domestic service is required | Obtain account/use-case/number eligibility and a written quote; no Exotel adapter or account activation in this increment. |

Pricing sources checked for this increment:
[Twilio India pricing](https://www.twilio.com/en-us/voice/pricing/in),
[Exotel pricing](https://exotel.com/pricing/),
[Exotel voice-AI API](https://developer.exotel.com/docs/agentstream/connect-voice-ai).
Twilio's [India voice guidelines](https://www.twilio.com/en-us/guidelines/in/voice)
state calls to India originate from international, non-Indian numbers and describe
commercial recipient-consent restrictions. This is not a local telecom/legal
approval. Confirm jurisdiction, recipient consent, allowed destinations, account
geographic permissions and use case with the chosen provider before activation.

## Setup and authentication checklist — do not enable by default

1. Resolve the zero-free-space C: condition without deleting unknown user files.
   Backups and tests for this upgrade live on D:. A full system drive can break
   Python, audio, Codex temporary state and unrelated Windows applications.
2. Choose provider, operating country and caller-number type; approve recurring
   rental, per-minute/media charges, KYC, recipient consent and destination rules.
   This upgrade has not bought or verified any real telephone number.
3. If Twilio is approved, install `requirements-calling.txt` into the approved
   Python environment (consider D: storage). Do not install blindly onto full C:.
4. Make `JARVIS_TWILIO_ACCOUNT_SID` and `JARVIS_TWILIO_AUTH_TOKEN` available to the
   authorized JARVIS process through an owner-approved secret delivery mechanism.
   Do not put tokens in source, prompts, Git, dashboard fields or example files.
   The adapter reads process environment only; it does not implement a new OS
   credential vault or store credentials. Environment inheritance needs local
   access controls and should not be shared with untrusted child programs.
5. Non-secret fields use existing `plugin_config.calling_manager`: `provider`,
   `caller_id`, `contacts_json` (JSON list of name/number/authorized/optional owner).
   `outgoing_enabled` and `costs_and_consent_approved` must both be actual JSON
   booleans true before telephone transport can run. They are intentionally not
   auto-enabled by the UI. Do not edit the full settings file with a command
   that could overwrite existing secrets or other settings. Owner-number calls
   also require provider verification of that number.
6. Status is local by default. `check phone connection` performs a read-only
   provider lookup and can fail specifically for missing SDK/credentials/caller
   ID, HTTP authentication/permissions, or network access. It does not dial.
7. Notification grammar, ONLY after intentional setup:
   `call Appa on phone and say "Your requested notification text"`.
   Recipient must already be authorized; explicit numbers must also be allowlisted.
   Bare `call Appa` is intentionally ambiguous, not silently turned into a paid
   phone call. This mode reads the AI disclosure and requested text, then hangs up;
   it is not a live conversational call. Do not use it for emergency calling.

Codex CLI detected: `codex-cli 0.163.0-alpha.11`; its official status reported
ChatGPT login. No remote inference/remaining quota was verified. Existing login
was not changed. If absent/expired, the owner should run `codex login` manually,
then `codex login status`. No cookie/password/token extraction is supported.
ChatGPT/Codex plan access and separately billed API usage are distinct.
[Official CLI reference](https://learn.chatgpt.com/docs/cli/reference) and
[non-interactive sandbox guidance](https://learn.chatgpt.com/docs/non-interactive-mode).
The current integration prepares plans only. Supervised source-only sandboxes,
coding progress/checkpoint integration and exact sensitive-operation approvals
are still required before enabling automatic coding execution. Existing Git
publication approval/secret scanning is not bypassed.

## Verification and performance

Baseline isolated source suites: 176 automation tests OK; 99 provider checks OK.
Final isolated source suites: 204 automation tests OK; 99 provider checks OK.
Optional Piper voice checks retain their existing dependency skip behavior.
One initial new test expected ValueError when a forged job context is rejected;
the runtime correctly rejected it earlier with InterruptedError. The test now
accepts both protective rejections; no guard was weakened. No final failures.

New coverage: saved/explicit/owner/ambiguous/unauthorized recipients, provider
caller-ID gate, opt-in cost/consent gate, auth redaction, uncertain transport,
shared cooldown, concurrent duplicates, persistent receipts, cancelled/stale/
completed/reconnected work, workflow replay exclusion, malformed acknowledgement,
provider state refresh, disabled incoming signature contract, SDK no-retry/no-
recording request contract, CLI unavailable/logged-out/logged-in distinctions,
project scope, zero model calls for status routing, truthful failures, offscreen
local-only Calling panel. Existing WhatsApp voice/video and Chrome/Git/wake/
provider tests also ran. All phone transports and Codex inference are mocked.
Real provider SDK compatibility/account access and microphone use remain untested.

Warm routing benchmark: 2,000 iterations per command and version. New Calling
status/history and Codex connectivity queries previously had no deterministic
route; now they do. Current medians 15–16 microseconds, p95 27–32 microseconds.
`open Chrome` median remains 9 microseconds (p95 before 19, after 21 microseconds).
This does not show a CPU speedup; the verified gain is avoiding model planning
for the added exact commands in typed/local-STT paths. No milliseconds/seconds
saved in live voice are asserted.

Historical database: first-dispatch median 35.014 ms, p95 344.919 ms (n=36);
executor submission-to-receipt median 14.418 ms, p95 16.996 ms (n=41);
WhatsApp execution median 29.143709 seconds (n=3, no p95). History lacks sufficient
phase-specific WhatsApp and live microphone samples for a safe bottleneck fix.
Long report-time samples include uncontrolled delays and are not a speech-latency
baseline. The database is changing as the user's existing app runs. No measured
WhatsApp/end-to-end improvement is claimed, and its contact checks were untouched.

## Real Windows smoke test (read-only first)

Restart JARVIS normally after addressing disk space:

```powershell
Set-Location 'D:\Mark-LVI-main\Mark-LVI-main'
& 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe' .\main.py
```

Ask/type `show calling status`, `show calling history`, and
`Jarvis, are you connected to Codex?`. Expect real configuration/login evidence,
not claims of connected telephone/conversational service or verified Codex quota.
Open Settings -> CALLING…; refresh must not dial or contact a provider.
Existing `show git status`, application sequencing and custom wake phrase can
then be checked. Do not test paid phone transport or real WhatsApp calls without
separate deliberate authorization. Native Gemini audio routing is still selected
by its model; test the microphone path separately from typed shortcuts.

## Backup, evidence and rollback

Backup: `D:\Mark-LVI-main\Mark-LVI-main\upgrade-backups\phase5-20261022-205644`.
Contains original source only, NOT credentials/private memory. Work/evidence:
`D:\Mark-LVI-main\Mark-LVI-main\downloads\phase5-work`:
`baseline-automation.log`, `baseline-providers.log`, `final-automation.log`,
`final-providers.log`, `performance.json`, `manifest.json`, `installation.json`.
`installation.json` lists every deployed file. No Git stage/commit/push occurred.

Rollback: stop JARVIS, preserve any subsequent edits, and restore only the five
modified existing files (core/command_router.py, core/prompt.txt,
providers/engine_pipeline.py, ui.py, tests/test_automation.py) from that backup.
Move the seven newly added files listed by installation.json into a new recovery
folder rather than deleting them. Do not restore/reset memory, settings, Git
history or credentials. Restart and run the baseline suites. Avoid a whole-tree
restore because other uncommitted work belongs to the owner.
