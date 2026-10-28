# Calling awareness and channel selection upgrade

## Cause and implementation

The telephone status plugin only described its own unconfigured provider. General calling questions and bare recipient requests had no unified deterministic route, leaving the model to confuse disabled telephone service with the separate enabled WhatsApp plugin. Short replies also lacked a narrowly scoped calling intent.

Added `calling_assistant`, a read-only plugin backed by actual enabled action schemas. It never dials. It distinguishes voice, video, telephone, incoming, status and ending support. Direct explicit WhatsApp calls still use the existing WhatsApp executor. Bare calls ask for the channel/type. Informational questions never call.

Pending clarification lives only in process memory for 60 seconds, bound to its originating execution and cancellation generation. Only the immediate, exact, final user reply can produce a current-turn grant for a single recipient and mode. Topic changes, cancellation, interruption, reconnect, restart and completion consume or invalidate it. Repeated tool invocations cannot extend its deadline. No old conversation or persistent receipt reconstructs authorization.

WhatsApp authorization now requires the exact explicit request or a valid runtime follow-up, not just the words 'call' and a contact inside an informational sentence. Native partial transcripts cannot authorize calls. A final transcription event, including an empty final event, is required. Existing persistent 30-second cooldown, same-turn guard, receipts, exact accessibility/header checks, self-call guard and no-uncertain-retry behavior remain.

Local pipeline replies for calling diagnostics and WhatsApp results come directly from tool evidence, avoiding another model rewrite. Native Gemini Live selection and narration remain model-mediated; declarations and dynamic guidance are improved but are not a deterministic guarantee. Execution authorization remains local.

## Files

New: `core/calling_intents.py`, `plugins/calling_assistant.py`, `tests/test_calling_awareness.py`, this report.

Modified: `core/automation_runtime.py`, `core/command_router.py`, `providers/engine_pipeline.py`, `main.py`, `plugins/whatsapp_control.py`, `plugins/calling_manager.py`, `core/prompt.txt`, `tests/test_automation.py`.

WhatsApp UI discovery/search/call-click implementation was not changed. Its declaration and evidence-based narration were updated. Existing cooldown test inputs now explicitly name WhatsApp, since bare call requests correctly require clarification.

Backup: `D:\Mark-LVI-main\Mark-LVI-main\upgrade-backups\calling-awareness-20261022-231814` (115 original source files). Installation checks every original source fingerprint before overwriting anything. No credentials, settings, memory or live automation DB are copied from the test stage.

## Verification

Baseline: 246 automation tests passed in 132.153 seconds; 99 provider checks passed.

Final: **277 automation tests passed in 131.856 seconds; 99 provider checks passed; zero final failures.** This includes automation, integration, replay, Chrome ownership, Git, wake, Codex, calling and workflow suites. Whole-suite duration is not an application performance measurement.

31 new offline regressions cover capabilities, direct voice/video routing, missing/disabled tools, channel and recipient follow-ups, final/partial native transcription, expiry, topic change, cancellation, reconnect, restart, stale callbacks, model-invented actions, misleading model narration, completed-call replay, same-turn and persistent cooldown guards, telephone unavailability, microphone separation and unknown call states.

Final complete suite results and durations: `downloads/calling-awareness-work/golden-automation.log`, `golden-providers.log`, `golden-results.json`. Final installation requires both suites to pass. Source syntax and existing secret checks run before installation. Installed hashes and actual enabled discovery are checked using a temporary database by `installed_smoke.py`.

Intermediate test issues: one new summary fixture initially used a JSON format rather than the actual WhatsApp receipt format; the fixture was corrected. A manually selected replay-test invocation failed import because that legacy module expects discovery's tests-directory path. The supported full discovery runner is used for final verification. No production failure was suppressed or test skipped for either issue.

No real calls, paid services, credential changes, commits or pushes were performed. Existing tests mock desktop actions and telephone transports. Existing provider suite emits an upstream pynvml deprecation warning.

## Measured performance

Warm offline routing, 2,000 samples per command, seconds (median / nearest-rank p95):

| Request | Before | After | Routing change |
| --- | --- | --- | --- |
| Can you call someone? | 0.000021 / 0.000045 | 0.000014 / 0.000033 | Model fallback to deterministic diagnostic |
| Call Ganesh | 0.000020 / 0.000037 | 0.000020 / 0.000043 | Model fallback to deterministic clarification |
| Video call Ganesh on WhatsApp | 0.000024 / 0.000036 | 0.000020 / 0.000043 | Deterministic preserved |
| Open Chrome | 0.000009 / 0.000018 | 0.000010 / 0.000022 | Deterministic preserved |

Evidence: `downloads/calling-awareness-work/performance.json`. These tiny parser differences are not an end-to-end voice speedup. The important verified change is avoiding a planning model call on supported local-text/STT questions and follow-ups. No new UIA search, microphone, provider planning or live call timings were measured. Read-only inspection of the existing automation DB found three historical WhatsApp execution samples, median 29.143709 seconds, p95 unavailable below 20 samples. These are not a controlled before/after measurement. Existing WhatsApp timing instrumentation remains available; no contact-search optimization is claimed without a safe representative desktop sample.

## Safe Windows verification

Restart the installed JARVIS normally to discover the new plugin:

```powershell
Set-Location 'D:\Mark-LVI-main\Mark-LVI-main'
& 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe' .\main.py
```

Non-calling tests first:

1. Ask 'Can you call someone?' Expect WhatsApp capability and a recipient question, no call.
2. Say 'Cancel'. Then say an unrelated topic. No call should occur.
3. Ask 'Call Ganesh'. Expect a WhatsApp voice/video question. Say 'Cancel', not a mode, unless intentionally authorizing a real call.
4. Ask 'Can you make video calls?' Expect available controls with live reliability explicitly unverified. Cancel.
5. Ask 'Call this mobile number normally'. Expect telephone unavailable and WhatsApp alternative; no number dialed.
6. Ask 'Call status' and 'End the WhatsApp call'. Expect unknown state and unavailable verified automatic ending, not a connected/ended claim.
7. Ask the general question, wait more than 60 seconds or reconnect, then give a name. It must not authorize a call.

Only when you intentionally want a real call, with a consenting saved contact on a different WhatsApp account: use 'Call Appa on WhatsApp', or complete a fresh prompt with the exact recipient, or choose 'Video' after 'Call Ganesh'. These replies authorize real calls; they are not harmless diagnostic commands. Verify the contact/header and actual UI state manually. A button click is reported only as initiation attempted, never proof of ringing or connection. Do not retry an uncertain attempt; make a new explicit request only after inspecting WhatsApp and waiting for the cooldown. No live desktop or video-call verification was performed by this upgrade.

## Limitations

- Paid telephone calling stays disabled; no subscriptions, provisioning or transport changes.
- The current WhatsApp plugin exposes no verified incoming answering, call-state monitoring or end-call action. Those remain unavailable rather than guessed.
- Microphone availability is independent of telephone audio and is not tested by this diagnostic.
- Native Gemini Live requires finalized input transcription before a call. If finality is unavailable or a model invokes too early, the call is safely refused; repeat an explicit command through local text/STT. No native audio deterministic-routing claim is made.
- The grammar is deliberately conservative. Ambiguous, compound or unsupported call wording may require restating the exact contact, channel and type. No fuzzy substitution or automatic replay is permitted.
- Enabled action declarations establish available controls, not a verified live connection or guaranteed video reliability.
