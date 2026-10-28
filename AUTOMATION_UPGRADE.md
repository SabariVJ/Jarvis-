# JARVIS Windows automation upgrade

## Load the upgrade

Close JARVIS normally, then run in PowerShell:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
& 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe' .\main.py
```

Your existing settings and credentials are unchanged. New plugins are discovered by the existing plugin system; check plugin settings if a tool is disabled.

## Improvements

- WhatsApp calls have no extra confirmation popup. Exact conversation-header verification remains mandatory. A persistent 30-second global call cooldown and same-turn/contact guard prevent duplicate calls, including reconnect attempts. Uncertain call outcomes are not retried.
- Desktop operations are serialized, with bounded waits and cooperative cancellation. Say “stop” or type “stop” to cancel pending work; already-completed actions cannot be undone.
- Persistent action receipts suppress duplicate executions. Structured results distinguish verified success from uncertainty.
- Chrome, Discord and VS Code resolve through executable identity and known installation paths. Weak editor-title matches are rejected.
- Exact app-opening commands bypass model planning for typed commands and the local speech pipeline. Gemini Live retains its native speech path. No end-to-end latency benchmark was performed.

## Ten new plugins

| Plugin | What to request |
|---|---|
| task_manager | Plan and save a multi-step task; start it, inspect status, cancel or resume a safe checkpoint; schedule a recipe or watch a selected folder. |
| desktop_agent | List windows, inspect accessible controls, focus or arrange a window, interact with a verified control. |
| knowledge_library | Index a selected documents folder, search it with source references, forget an index. |
| research_assistant | Research a topic using public pages, fetch a public URL, write a sourced Markdown report. |
| document_studio | Create a Word/Markdown report, summarize selected documents, combine matching-column CSV files into CSV or Excel. |
| project_assistant | Inspect a project, check Git status, run explicitly requested tests, draft a proposed patch. |
| automation_status | Show system load, tool timings and automation context. |
| media_assistant | Convert a selected media file, transcribe audio, draft subtitles. |
| integration_hub | Use explicitly configured, allowlisted MCP services. |
| plugin_workshop | Draft and statically check a new plugin outside the live plugins folder. |

Examples: “Open VS Code”; “Voice call Ganesh on WhatsApp”; “Show automation performance”; “Index D:\\Documents\\Notes in the knowledge library”; “Convert D:\\Audio\\meeting.wav to D:\\Audio\\meeting.flac”. Supply actual existing paths and new output filenames.

## Boundaries and setup

- Reusable workflows allow up to 20 steps and two workers. Calls, messages and arbitrary GUI clicks are excluded from replay. Unverified steps stop for attention. Schedules and folder watches run only while JARVIS is running. None were enabled during installation.
- Recording captures successful JARVIS-issued tool actions, not a manual mouse demonstration. An interrupted uncertain step is not automatically replayed.
- Desktop control depends on application accessibility support; stale or ambiguous controls are rejected. Generic WhatsApp mutations are blocked in favor of the dedicated contact-verifying plugin. This is not universal visual control of every application.
- Knowledge indexing is opt-in, local and bounded, with common credential/config folders excluded. Text-based PDFs and DOCX are supported; scanned PDFs need OCR elsewhere. Do not deliberately index sensitive folders.
- AI summaries, research reports, transcription and drafts use your configured provider and may upload the selected content and incur provider charges. Those paid/provider flows were not exercised during tests. Subtitle timing is a draft requiring review.
- Project tests execute code from the selected project: use trusted projects. Proposed patches are saved for review, not automatically applied. Plugin draft validation checks syntax/metadata, not security; generated plugins are not automatically installed.
- MCP accounts are not connected automatically. In integration_hub settings, set servers_json to a mapping such as `{"service":{"url":"https://YOUR-SERVER/mcp","allowed_tools":["tool_name"],"token_env":"SERVICE_TOKEN"}}`. Set the token in your environment, never in tool arguments. Only allow tools you intend JARVIS to use. HTTPS is required except localhost.
- New output artifacts refuse to overwrite existing files. Runtime receipts and task metadata are stored in memory/automation.db; they can contain tool results and selected paths, so treat that local file as private.

## Verification and recovery

32 offline automation/integration tests passed, including mocked WhatsApp safety checks, real synthetic FFmpeg conversion, and an in-process MCP server. The existing provider suite passed 99 checks. Main-module import succeeded. No live WhatsApp calls, paid provider requests, or complete real desktop workflows were tested. Restart and test application opening first, then make a call only when you intend to contact that person.

The installation report records a timestamped backup folder and per-file hashes. Backups contain original overwritten source files; newly added paths are recorded separately. To roll back, stop JARVIS, restore only those original files and remove only the recorded newly added files after checking for your subsequent edits. Do not delete your config or memory folders. Added Python dependencies are not automatically removed by source rollback.
