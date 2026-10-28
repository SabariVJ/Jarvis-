# Secure Git/GitHub and performance upgrade

## Installed behavior

The new `github_git` plugin provides `status`, `diff`, `review`, `stage`, `checks`, `commit`, `prepare_push`, `push`, and `verify`. It uses the existing Git executable, Git Credential Manager for HTTPS, or an already approved SSH key/agent. It never accepts or stores a token in plugin settings, code, a remote URL, or its review records. Credential handling remains with Git's existing mechanism; see [GCM configuration](https://github.com/git-ecosystem/git-credential-manager/blob/main/docs/configuration.md).

The dedicated workflow is separate from the existing project inspection/test plugin and opt-in MCP integration. Git publication is not allowed in replayable/background recipes. Existing execution/turn identities, receipts, lifecycle checks and cancellation govern every command. It does not weaken Chrome ownership or WhatsApp call protections.

At delivery, Git 2.55.0.windows.3 and Git Credential Manager 2.9.0 were installed. The live project directory was not a Git worktree. No real Git initialization, project commit, remote configuration or push was performed. Therefore the real-project commit hash, branch and remote are unconfigured, and the push outcome is **not attempted**. Successful local commit hashes and verified mocked push outcomes were exercised only in temporary test repositories.

## Review and publication protocol

1. Ask for Git status or a diff, or request a review of exact project-relative files. `diff` can collect eligible changed paths; `review` takes an explicit list. No wildcard or directory staging is supported.
2. Read the complete generated `.diff` artifact. The response supplies its path, a bounded preview, file/blob/mode fingerprints and a review ID. Large previews are marked truncated; the complete artifact is under private `memory/git-reviews/`, not in the repository.
3. In a fresh user message, say/type the supplied `Stage review REVIEW_ID` phrase. A model-supplied `confirmed=true`, a bare “yes”, or review and staging in the same model turn is insufficient. The full artifact and source hashes must still match.
4. Ask explicitly to commit the reviewed changes with a message. Before committing, JARVIS scans the entire index, validates the exact approved blobs and modes, checks whitespace/Python syntax, exports the staged snapshot to an isolated temporary directory, and runs available supported tests. `unittest` is the default; `pytest` can be selected. MARK LVI's provider self-test also runs when present. Empty/no-test snapshots or failed checks block the commit.
5. Ask to prepare a push review. JARVIS shows the GitHub repository URL, local branch, exact commit, reviewed change manifest/digest and remote branch state. Then supply the exact fresh confirmation phrase: `Confirm push OWNER/REPOSITORY branch BRANCH review APPROVAL_ID`.
6. Push uses one immutable commit hash and one explicit branch refspec. It never force-pushes, mirrors, deletes branches, pushes extra tags, rewrites history or bypasses server branch protection. It then queries the remote branch and compares its object ID to the intended commit. Git's refspec and porcelain behavior are described in the [official push documentation](https://git-scm.com/docs/git-push).

Review approval expires after one hour; push confirmation expires after ten minutes. A changed branch, commit, index, destination or observed remote state invalidates push approval. Push approval is consumed before attempting the network operation. A rejection, cancellation or unknown outcome does not schedule a retry. Use `verify` in a fresh command to inspect the remote before preparing any new attempt. Successful results include `commit`, `branch`, `remote`, `url`, `remote_commit`, `push_outcome` and `verified`.

Staging writes immutable, already-reviewed blob IDs into the index instead of letting a final `git add` reread mutable files. It refuses pre-existing staged changes and does not reset or overwrite another user's staging. A partially completed staging attempt is left for inspection; it is never silently repaired or committed.

## Security boundaries

- Private key files, API-key/credential files, `.env*`, automation databases/journals, runtime/private memory, backup folders, sensitive tool configuration and symlinks/junctions are excluded independently of `.gitignore`.
- `.gitignore` contained inline comments on API-key and OAuth patterns, which Git treated as part of those patterns. Those rules were corrected. A temporary-repository check confirmed `config/api_keys.json` was not ignored by the previous file and is ignored by the corrected one. Runtime memory is now ignored while root Python source modules remain eligible for explicit review.
- The scanner checks working files, old content shown in diffs, staged blobs and commit messages for common token/private-key formats and credential assignments, including YAML. It withholds detected values. Python syntax checks do not import or execute source.
- Review is limited to 100 explicit UTF-8 text files, at most 2 MiB per file/20 MiB of selected content. Index security review is bounded to 1000 files/20 MiB. Binary assets, symlinks, submodules and unsupported encodings need a separate manual workflow.
- Active Git content filters are blocked before hashing/export. Installed but unused Git LFS configuration is supported; its global installation alone does not block normal source files.
- URL rewriting, mirror/custom receive-pack/push-option settings, credential-bearing URLs and custom SSH command overrides require manual review. Automatic publication supports `github.com` HTTPS/SSH destinations, not arbitrary hosts or GitHub Enterprise.
- Only a workflow-created commit whose direct base matches the remote branch can be prepared automatically. Multiple unpublished/unreviewed commits or divergent history require manual review. Existing merge/rebase/cherry-pick states block automatic commits.
- Tests execute trusted project code. The staged snapshot excludes untracked personal configuration and uses an empty temporary MARK configuration fixture. Common API/credential environment variables are removed from test subprocesses. This is not an OS sandbox: trusted tests and Git hooks can execute code under your account. Do not run this workflow on untrusted repositories. Other manually configured external integrations are outside this dedicated Git approval protocol.
- Pattern scanning is defense in depth, not a proof that every possible secret/private document is absent. Human review is mandatory. If an existing index/history contains credentials, stop, rotate them as appropriate and review remediation manually; this tool does not rewrite history.

## Natural-language and voice examples

- “Jarvis, show Git status.”
- “Show Git diff.”
- “Review core/command_router.py and tests/test_performance_metrics.py in this project.”
- Use the returned `Stage review ...` phrase after reading the full artifact.
- “Commit the reviewed changes with message Improve Git command routing.”
- “Prepare a GitHub push review for origin.”
- Use the returned repository/branch/review confirmation phrase, then ask “Verify the remote commit.”

Read-only typed and local-speech shortcuts bypass model planning when available. Other operations use the existing model/tool dispatcher. Keep `github_git` enabled and included in the selected provider's tool budget. Exact confirmation IDs are best typed if speech recognition changes their characters; no fuzzy confirmation matching is used.

## Git setup and real Windows verification

Restart JARVIS normally:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
$py = 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe'
& $py .\main.py
```

First ask for Git status. In the current ZIP-style project folder, the expected result is a clear “not a Git repository” setup message; no repository should be created automatically.

Choose either an existing trusted clone or your own GitHub repository/fork and branch. Do not assume the original upstream source repository belongs to your account. If you deliberately choose to initialize this existing folder, replace these placeholders first and run the commands yourself:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
$branch = 'YOUR-CHOSEN-BRANCH'
$remoteUrl = 'https://github.com/YOUR-OWNER/YOUR-REPOSITORY.git'
git init -b $branch
git config user.name 'YOUR NAME'
git config user.email 'YOUR APPROVED COMMIT EMAIL'
git remote add origin $remoteUrl
git credential-manager github login
git ls-remote --heads origin
```

Use an existing SSH remote/key instead if that is your approved mechanism; manually check it with `ssh -T git@github.com`. Private repositories/organizations may require write permission and SSO authorization. GitHub “repository not found” can indicate either a wrong URL or missing permission. Protected branches may require a pull request. The plugin reports these errors; it does not change permissions or protection rules.

Review explicit source/test files with JARVIS, read the artifact, confirm staging, and request a local commit. For an initial commit, the staged snapshot must include all source/test dependencies required to run its tests. Confirm no private files appear in the staged manifest. Preparing a push only displays the review; it must not publish anything. Send the exact confirmation only when you intend to publish to that displayed repository and branch.

After publication, independently verify:

```powershell
git rev-parse HEAD
git branch --show-current
git remote get-url --push origin
git ls-remote --heads origin $branch
```

The returned remote object ID must equal the reported commit. Avoid a second push if the first attempt's outcome is unknown; verify first.

## WhatsApp profiling and optimization

Existing receipts showed tool durations of **19.453, 29.141 and 55.344 seconds**, not an exact recorded 34-second run. The long run's trace included these approximate event gaps:

| Region | Observed gap |
|---|---:|
| Tool running → app launch request | 11.38 s |
| Launch request → window focus request | 20.08 s |
| Focus request → search-field click request | 18.54 s |
| Search input request → tool completion | 5.12 s |

Another run spent approximately 11.86 seconds between focus and search-field click. These are event-region timings, not proof that all of a gap was one UIA query; they include loading/readiness and focus work. Queue waits in these traces were only about 0.01 seconds.

The upgrade targets Windows handles belonging to actual WhatsApp processes before wrapping them in accessibility objects, avoiding whole-desktop UIA enumeration. Contact/search/header/call scans request the relevant control types instead of materializing every descendant. Exact saved-contact matching, live header checks, active-call checks, the 30-second cooldown, same-turn guard and execution authorization remain in place. No call retries were added.

New nested `performance_span` events separately measure window detection, search-field discovery, contact search/results, accessibility queries, header/chat selection, active-call/call-control discovery and WhatsApp execution. Local pipelines additionally record client-side model stream wait, excluding speech synthesis and tool execution. That metric includes API transport; native Gemini server-side planning cannot be isolated from transport at this boundary. The report labels native command-to-dispatch latency accordingly. Nested spans overlap and must not be summed.

A read-only comparison with WhatsApp not visible measured detection at 0.0698 s before and 0.0509 s after. It did not exercise a loaded chat/search tree and does not establish an end-to-end WhatsApp speedup. A separate verified subprocess improvement replaced the unconditional 150 ms polling sleep with a cancellable wait: ten `git --version` commands took **1.6217 s before and 0.4476 s after** in this session. This improves Git command overhead, not a measured voice-call duration.

To measure a real non-calling WhatsApp operation after restart, explicitly request searching/opening a saved contact of your choice, then inspect:

```powershell
cd 'D:\Mark-LVI-main\Mark-LVI-main'
$py = 'C:\Users\ADMIN\AppData\Local\Python\pythoncore-3.11-64\python.exe'
& $py .\tools\whatsapp_timings.py --limit 5
```

The report does not output contact names or message contents. Use `--db` if `JARVIS_AUTOMATION_DB` points elsewhere. Measure with WhatsApp already open and cold-started separately. Actual contact-search/call latency improvements still need this live verification. No real call was placed during development/testing.

## Tests, changes and recovery

**115 tests passed, 0 failed** across the complete automation, Chrome ownership, replay, Git security/workflow, performance and integration suites. This includes 31 Git tests, five performance tests and the existing 79 tests. The provider suite reported **99 checks passed, 0 failed**, with its optional Piper checks skipped. Git tests create only temporary local repositories/commits and mock every remote operation. Coverage includes verified push results, authentication failure, protected/rejected push, secret detection, immutable staging races, changed review artifacts, cancelled actions, stale receipts, active/inactive filters and private environment removal. The changed-source security scan found no findings under the implemented policy.

Modified files: `.gitignore`, `core/process_runner.py`, `core/command_router.py`, `main.py`, `providers/engine_pipeline.py`, `plugins/whatsapp_control.py`, `tests/test_automation.py`.

Added files: `core/git_security.py`, `core/git_workflow.py`, `core/automation_metrics.py`, `plugins/github_git.py`, `tests/test_git_workflow.py`, `tests/test_performance_metrics.py`, `tools/whatsapp_timings.py`, and this report.

The automation runtime, task manager, Chrome ownership/close implementation, existing project assistant and MCP integration source are unchanged. Backups were made before editing under `upgrade-backups/github-performance-20261022-153106`; the installation manifest records another deployment backup and exact hashes. No credentials, settings, `.git` metadata or existing database/memory were copied or reset during installation. Future approved Git reviews and performance events append normal local records.

To restore source, stop JARVIS and restore only backed-up files; remove only the newly added paths listed in the manifest after checking for subsequent edits. Preserve config/memory and repository metadata. Runtime identity/review records are data, not instructions to replay actions.
