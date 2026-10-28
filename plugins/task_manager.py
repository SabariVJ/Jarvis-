"""Persistent, bounded recipes. Interrupted side effects are never retried."""
import json
import re
from pathlib import Path
import threading
import time
import uuid

from core import outcome
from core.automation_runtime import get_runtime
from plugins._automation_common import declaration, string, report

PLUGIN = declaration("task_manager",
    "Plan, save and run multi-step workflows with checkpoints. Track, cancel or resume jobs. "
    "Record successful JARVIS actions (not physical mouse movements) as a recipe. "
    "Schedule an explicitly requested recipe or watch a named folder. Calls and messages cannot be replayed in recipes.",
    {"action": string(choices=["plan", "save", "start", "status", "list", "cancel", "resume",
                               "record_start", "record_save", "schedule", "watch_folder", "triggers", "disable_trigger"]),
     "name": string("Recipe name"), "goal": string("Task to plan"), "id": string("Job or trigger ID"),
     "steps": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
         "tool": string(), "arguments_json": string("JSON object of tool arguments"),
         'id': string('Unique step ID; defaults to step_1, step_2, ...'),
         'depends_on': {'type': 'ARRAY', 'items': string('Earlier step ID; default previous step')},
         'max_attempts': {'type': 'INTEGER', 'description': '1 by default; at most 2, explicit user-approved read-only retries only'}},
         "required": ["tool", "arguments_json"]}},
     "at": string("ISO-8601 time with timezone offset; one-shot schedule"),
     "folder": string("Exact folder to watch; immediate children only")})

# Bounded replay surface. Interactive calls, communications and arbitrary shell
# commands are deliberately absent; those use their ordinary foreground tools.
ALLOWED = {
    "open_app": None,
    "knowledge_library": {"index", "search", "sources"},
    "research_assistant": {"research", "fetch"},
    "document_studio": {"report", "combine_csv", "summarize"},
    "desktop_agent": {"inspect", "windows", "focus", "arrange"},
    "project_assistant": {"inspect", "git_status", "test", "propose_fix"},
    "github_git": {"guardian_poll"},
    "automation_status": {"status", "context", "performance"},
    "media_assistant": {"transcribe", "convert", "subtitles"},
}
_workers = {}
_lock = threading.RLock()
_scheduler = None
RETRYABLE = {'knowledge_library': {'search', 'sources'}, 'desktop_agent': {'inspect', 'windows'},
             'project_assistant': {'inspect', 'git_status'},
             'automation_status': {'status', 'context', 'performance', 'capabilities'}}


def register_guardian_interval(repository, rt):
    """Called only after Guardian's fresh selection gate; same recipes/worker pool."""
    from core.github_guardian import slug
    repository = slug(repository)
    if not rt.get('guardian_watch', repository, {}).get('enabled'):
        raise ValueError('Select the repository explicitly before scheduling reads')
    name = 'guardian:' + repository
    steps = validate_steps([{'tool': 'github_git', 'arguments_json': json.dumps(
        {'action': 'guardian_poll', 'repository': repository,
         'watch_revision': rt.get('guardian_watch', repository)['revision']})}])
    rt.put('recipe', name, {'steps': steps, 'retry_approved': False})
    rt.put('trigger', name, {'name': name, 'enabled': True, 'type': 'guardian_interval',
                            'repository': repository, 'interval': 900, 'next_at': time.time()})


def launch_supervised(job_id, target, rt):
    """One-shot supervised worker using the SAME persistent jobs and slot pool.

    Not a recipe/scheduler capability: only the fresh approved Codex handler
    calls this seam. Interrupted runs are inspected, never relaunched/resumed.
    """
    with _lock:
        job = rt.get('job', job_id, {})
        if job.get('kind') != 'codex' or job.get('state') != 'queued' or not job.get('start_consumed'):
            raise ValueError('Supervised task is not approved/queued')
        if job_id in _workers or len(_workers) >= 2:
            raise ValueError('Workflow capacity reached or task already running')
        context = rt.job_context(job_id)
        def execute():
            try:
                target(job_id, rt, context)
            finally:
                with _lock:
                    if _workers.get(job_id) is threading.current_thread():
                        _workers.pop(job_id, None)
        thread = threading.Thread(target=execute, daemon=True, name='jarvis-codex-' + job_id)
        _workers[job_id] = thread
        thread.start()


def validate_steps(steps, allow_retries=False):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError("A recipe needs between 1 and 20 steps")
    clean = []
    identities = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError('Each step must be an object')
        tool = step.get("tool")
        if tool not in ALLOWED:
            raise ValueError(f"{tool} is not supported in replayable workflows")
        args = json.loads(step.get("arguments_json", "{}"))
        if not isinstance(args, dict):
            raise ValueError("Each step needs a JSON object")
        if ALLOWED[tool] is not None and args.get("action") not in ALLOWED[tool]:
            raise ValueError(f"This {tool} action cannot be replayed")
        identity = step.get('id', 'step_' + str(index + 1))
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', identity) or identity in identities:
            raise ValueError('Step IDs must be unique bounded identifiers')
        depends = step.get('depends_on', [clean[-1]['id']] if clean else [])
        if not isinstance(depends, list) or any(not isinstance(dep, str) or dep not in identities for dep in depends) or len(depends) != len(set(depends)):
            raise ValueError('Dependencies must name unique earlier steps; forward references/cycles are not allowed')
        attempts = step.get('max_attempts', 1)
        if type(attempts) is not int or attempts not in (1, 2):
            raise ValueError('max_attempts must be 1 or 2')
        if attempts > 1 and (not allow_retries or args.get('action') not in RETRYABLE.get(tool, set())):
            raise ValueError('Retries require explicit user approval and a supported read-only action')
        clean.append({'id': identity, 'depends_on': depends, 'max_attempts': attempts,
                      "tool": tool, "arguments_json": json.dumps(args)})
        identities.add(identity)
    return clean


def _progress(job):
    existing = job.get('progress')
    if existing is not None:
        return existing
    return [{'id': step.get('id', 'step_' + str(index + 1)),
             'depends_on': step.get('depends_on', ['step_' + str(index)] if index else []),
             'state': 'verified' if index < job.get('next', 0) else 'pending', 'attempts': 0}
            for index, step in enumerate(job['steps'])]


def _available(rt, step):
    provider = getattr(rt, 'catalog_provider', None)
    catalog = provider() if callable(provider) else getattr(rt, 'catalog', None)
    if catalog is not None and step['tool'] not in {tool['name'] for tool in catalog}:
        raise ValueError('Workflow tool is disabled or unavailable: ' + step['tool'])


def authorize_retry(rt, name, args, context):
    rt.check_cancelled(context)
    if context.get('lifecycle') != 'job':
        raise InterruptedError('Only explicitly approved read-only workflow retries are supported')
    job = rt.get('job', context['task_id'], {})
    index, attempt = int(context['step_id']), context['retry_attempt']
    steps = job.get('steps', [])
    if type(attempt) is not int or index < 0 or index >= len(steps):
        raise InterruptedError('Invalid workflow retry identity')
    step = steps[index]
    if (not job.get('retry_approved') or not 1 <= attempt < step.get('max_attempts', 1)
            or name != step['tool'] or args != json.loads(step['arguments_json'])
            or args.get('action') not in RETRYABLE.get(name, set())):
        raise InterruptedError('Retry is not authorized for this operation')
    previous = rt.lookup_receipt(name, args, {**context, 'retry_attempt': attempt - 1})
    if not previous or not str(previous.get('result', '')).startswith('status=failed '):
        raise InterruptedError('Only a definite failed read can retry; uncertain/running results cannot')


def _run_job(job_id, rt, context):
    job = rt.get("job", job_id)
    if not job or job['state'] not in {'queued', 'running', 'checkpoint', 'paused'}:
        rt.trace('worker_rejected', context, job_id=job_id, reason='job_not_active')
        return
    try:
        if context.get('lifecycle') != 'job':
            context = rt.job_context(job_id, context)
        rt.check_cancelled(context)
        job['execution_id'] = context['execution_id']
        job['steps'] = validate_steps(job['steps'], allow_retries=job.get('retry_approved', False))
        job['progress'] = _progress(job)
        rt.trace('worker_started', context)
        if rt.dispatch is None:
            raise RuntimeError("Workflow dispatcher is not connected; restart JARVIS")
        for index in range(job["next"], len(job["steps"])):
            current = rt.get("job", job_id)
            if current.get("cancel_requested"):
                raise InterruptedError("Cancelled before next step")
            rt.check_cancelled(context)
            job.update(state="running", active_step=index, next=index)
            job = rt.update("job", job_id, job, preserve=("cancel_requested",),
                            expected={'execution_id': context['execution_id']})
            step = job["steps"][index]
            _available(rt, step)
            states = {item['id']: item['state'] for item in job['progress']}
            if any(states.get(dep) != 'verified' for dep in step['depends_on']):
                raise ValueError('Step dependency is not verified')
            progress = job['progress'][index]
            for attempt in range(step['max_attempts']):
                rt.check_cancelled(context)
                progress.update(state='running', attempts=attempt + 1)
                job = rt.update('job', job_id, job, preserve=('cancel_requested',), expected={'execution_id': context['execution_id']})
                progress = job['progress'][index]
                rt.trace('workflow_step', context, step_id=index, state='running', attempt=attempt)
                step_context = {**context, "request_id": f"job:{job_id}:{index}:{attempt}", 'step_id': str(index), 'retry_attempt': attempt}
                result = rt.dispatch(step["tool"], json.loads(step["arguments_json"]), step_context)
                if outcome.is_verified(result):
                    progress['state'] = 'verified'
                    break
                progress['state'] = 'failed'
                if not str(result).startswith('status=failed ') or attempt + 1 == step['max_attempts']:
                    break
                # Cancellation-aware bounded backoff, only for opted-in reads.
                for _ in range(5):
                    rt.check_cancelled(context)
                    time.sleep(.05)
            rt.trace('workflow_step', context, step_id=index, state=progress['state'], attempts=progress['attempts'])
            # Cancellation can arrive while the tool is executing. Preserve it
            # when saving our checkpoint instead of overwriting the newer flag.
            if rt.get("job", job_id).get("cancel_requested"):
                job["cancel_requested"] = True
            job["results"].append({"step": index, "result": result})
            job["active_step"] = None
            # Unknown / merely started / failed are not verified completion.
            if not outcome.is_verified(result):
                job.update(state="needs_attention", error="Step was not verified; it will not be retried automatically")
                job = rt.update("job", job_id, job, preserve=("cancel_requested",),
                                expected={'execution_id': context['execution_id']})
                return
            job["next"] = index + 1
            job["state"] = "checkpoint"
            job = rt.update("job", job_id, job, preserve=("cancel_requested",),
                            expected={'execution_id': context['execution_id']})
        if rt.get('job', job_id).get('cancel_requested'):
            raise InterruptedError('Cancelled at checkpoint')
        job["state"] = "completed"
    except InterruptedError as exc:
        job.update(state="paused", error=str(exc))
        for item in job.get('progress', []):
            if item['state'] == 'pending':
                item['state'] = 'cancelled'
            elif item['state'] == 'running':
                item['state'] = 'failed'
                job['state'] = 'needs_attention'
    except Exception as exc:
        job.update(state="needs_attention", error=str(exc))
        active = job.get('active_step')
        if active is not None and active < len(job.get('progress', [])):
            job['progress'][active]['state'] = 'failed'
    finally:
        if rt.get('job', job_id, {}).get('execution_id') != context.get('execution_id'):
            rt.trace('worker_rejected', context, job_id=job_id, reason='execution_ownership_changed')
            return
        rt.update("job", job_id, job, preserve=("cancel_requested",),
                  expected={'execution_id': context['execution_id']})
        rt.finish(context, 'completed' if job['state'] == 'completed' else 'paused')
        rt.trace('worker_finished', context, state=job['state'], next=job['next'])
        with _lock:
            if _workers.get(job_id) is threading.current_thread():
                _workers.pop(job_id, None)
        if callable(getattr(rt, 'dashboard_notify', None)):
            from core.local_notifications import emit
            event = 'completed' if job['state'] == 'completed' else 'needs_attention'
            emit(job_id, event, f"Workflow {job['name']} is {job['state']}. Job ID: {job_id}.",
                 verified=job['state'] == 'completed', rt=rt)
        elif rt.notify:
            rt.notify(f"Workflow {job['name']} is {job['state']}. Job ID: {job_id}.")


def _launch(job_id, rt, resume=False):
    with _lock:
        if job_id in _workers:
            raise ValueError("This job is already running")
        if len(_workers) >= 2:
            raise ValueError("Two workflows are already running; wait or cancel one")
        context = rt.job_context(job_id, resume=resume)
        thread = threading.Thread(target=_run_job, args=(job_id, rt, context), daemon=True,
                                  name=f"jarvis-job-{job_id[:8]}")
        _workers[job_id] = thread
        thread.start()


def _start(name, rt):
    recipe = rt.get("recipe", name)
    if not recipe:
        raise ValueError("No saved recipe with that name")
    steps = validate_steps(recipe["steps"], allow_retries=recipe.get('retry_approved', False))
    for step in steps:
        _available(rt, step)
    job_id = uuid.uuid4().hex[:12]
    rt.put("job", job_id, {"name": name, "steps": steps, "next": 0, "active_step": None,
                           "results": [], "state": "queued", "created": time.time(),
                           'retry_approved': recipe.get('retry_approved', False)})
    _launch(job_id, rt)
    return job_id


def _folder_signature(folder):
    root = Path(folder).resolve(strict=True)
    # Metadata only: contents are not read by the watcher.
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                  for p in root.iterdir() if p.is_file())[:5000]


def tick(rt):
    """One scheduler pass; mark a trigger consumed BEFORE starting work."""
    for key, trigger in rt.records("trigger").items():
        if not trigger.get("enabled"):
            continue
        try:
            if trigger['type'] == 'guardian_interval':
                from memory.config_manager import get_plugin_enabled
                if (not get_plugin_enabled('github_git') or rt.get('guardian', 'settings', {}).get('paused')
                        or not rt.get('guardian_watch', trigger['repository'], {}).get('enabled')):
                    continue
                if len(_workers) >= 2 or time.time() < trigger.get('next_at', 0):
                    continue
                if any(job.get('name') == trigger['name'] and job.get('state') in {'queued', 'running', 'checkpoint'}
                       for job in rt.records('job').values()):
                    continue
                ready = True
                # Reserve a single interval BEFORE launch; no missed-interval burst.
                trigger['next_at'] = max(time.time() + 900, rt.get('guardian', 'rate', {}).get('next_at', 0))
            elif trigger["type"] == "schedule":
                ready = time.time() >= trigger["at"]
                if ready:
                    trigger["enabled"] = False
            else:
                signature = json.dumps(_folder_signature(trigger["folder"]))
                ready = signature != trigger["signature"]
                # Require one unchanged polling interval before reacting.
                if ready and signature != trigger.get("pending"):
                    trigger["pending"] = signature
                    rt.put("trigger", key, trigger)
                    continue
                if ready:
                    trigger["signature"] = signature
                    trigger.pop("pending", None)
            if ready:
                rt.put("trigger", key, trigger)
                trigger["last_job"] = _start(trigger["name"], rt)
                rt.put("trigger", key, trigger)
        except Exception as exc:
            trigger.update(enabled=False, error=str(exc))
            rt.put("trigger", key, trigger)
            if rt.notify:
                rt.notify(f"Automation trigger {key} stopped: {exc}")


def start_scheduler(rt=None):
    global _scheduler
    rt = rt or get_runtime()
    with _lock:
        if _scheduler and _scheduler.is_alive():
            return
        # A crash can occur after a click but before its receipt. Don't replay.
        for key, job in rt.records("job").items():
            if job["state"] in {"running", "queued", "checkpoint"}:
                if job.get('kind') == 'codex':
                    job.update(state='needs_attention', error='Codex was interrupted; inspect artifacts. No automatic resume/replay.')
                    rt.put('job', key, job)
                    if job.get('execution_id'):
                        rt.finish({'execution_id': job['execution_id']}, 'interrupted')
                    continue
                job["state"] = "needs_attention" if job.get("active_step") is not None else "paused"
                if job.get('steps'):
                    job['progress'] = _progress(job)
                    active = job.get('active_step')
                    if active is not None:
                        step = job['steps'][active]
                        receipt = rt.lookup_receipt(step['tool'], json.loads(step['arguments_json']),
                            {'lifecycle': 'job', 'task_id': key, 'step_id': str(active),
                             'retry_attempt': max(0, job['progress'][active].get('attempts', 1) - 1)})
                        if receipt and outcome.is_verified(receipt.get('result', '')):
                            job['progress'][active]['state'] = 'verified'
                            job.update(next=active + 1, active_step=None, state='paused')
                        else:
                            job['progress'][active]['state'] = 'failed'
                rt.put("job", key, job)
                if job.get('execution_id'):
                    rt.finish({'execution_id': job['execution_id'], 'task_id': key, 'source': 'recovery'}, 'interrupted')
                rt.trace('worker_recovered_without_replay', {'task_id': key, 'source': 'recovery'}, state=job['state'])
        def loop():
            while True:
                try:
                    tick(rt)
                except Exception as exc:
                    if rt.notify:
                        rt.notify(f"Automation scheduler stopped: {exc}")
                    return
                time.sleep(10)
        _scheduler = threading.Thread(target=loop, name="jarvis-scheduler", daemon=True)
        _scheduler.start()


def run(parameters, player=None, session_memory=None):
    rt = get_runtime()
    action = parameters.get("action")
    name = str(parameters.get("name", "")).strip()[:100]
    key = parameters.get("id", "")
    try:
        if action in {"save", "record_save", "plan"}:
            if not name:
                raise ValueError("Provide a recipe name")
            if action == "plan":
                from core import gemini
                provider = getattr(rt, 'catalog_provider', None)
                catalog = provider() if callable(provider) else getattr(rt, "catalog", [])
                available = [t for t in catalog if t["name"] in ALLOWED]
                plan = gemini.as_json("Plan up to 20 concrete tool steps for this user goal. "
                    "Return JSON {steps:[{tool: name, arguments_json: string containing a JSON object}]}. "
                    "Use only the supplied tools and supported actions. Never invent paths or missing details. "
                    "If required details are missing return {question: ...}.\nTOOLS:\n" + json.dumps(available) +
                    "\nGOAL:\n" + str(parameters.get("goal", "")), tier=gemini.SMART, timeout_ms=60000)
                if not plan or plan.get("question"):
                    return outcome.ask("task_manager", (plan or {}).get("question", "Could not create a valid plan; provide concrete steps"))
                steps = plan.get("steps")
            elif action == "record_save":
                steps = rt.record_finish()
            else:
                steps = parameters.get("steps")
            user_text = (session_memory or rt.context()).get('user_text', '')
            retry_approved = (not re.search(r"\b(no|not|never|without)\b|don't", user_text, re.I)
                and bool(re.search(r'\b(?:allow|enable|use) (?:one )?(?:read[- ]only )?retr(?:y|ies)\b|\bretry (?:failed )?read[- ]only\b', user_text, re.I)))
            steps = validate_steps(steps, allow_retries=retry_approved)
            for step in steps:
                _available(rt, step)
            rt.put("recipe", name, {"steps": steps, 'retry_approved': retry_approved})
            return report("task_manager", {"saved": name, "steps": steps, "executed": False})
        if action == "record_start":
            rt.record_start()
            return report("task_manager", {"recording": "successful JARVIS tool actions only"})
        if action == "start":
            return outcome.started("task_manager", job_id=_start(name, rt))
        if action == "list":
            return report("task_manager", {"recipes": rt.records("recipe"), "jobs": rt.records("job")})
        if action == "status":
            return report("task_manager", rt.get("job", key) or {"error": "Unknown job ID"})
        if action in {"cancel", "resume"}:
            job = rt.get("job", key)
            if not job:
                raise ValueError("Unknown job ID")
            if action == "cancel":
                rt.update("job", key, {"cancel_requested": True})
                return report("task_manager", {"cancel_requested": key, "effect": "before the next step"})
            if job.get('kind') == 'codex':
                raise ValueError('Codex tasks cannot be automatically resumed; inspect results and prepare a fresh explicitly approved task')
            if job["state"] != "paused" or job.get("active_step") is not None:
                raise ValueError("Only a paused job at a verified checkpoint can resume; uncertain steps need inspection")
            job.pop("cancel_requested", None)
            for item in job.get('progress', []):
                if item['state'] == 'cancelled':
                    item['state'] = 'pending'
            rt.put("job", key, job)
            _launch(key, rt, resume=True)
            return outcome.started("task_manager", job_id=key)
        if action in {"schedule", "watch_folder"}:
            if not rt.get("recipe", name):
                raise ValueError("Save the recipe first")
            key = uuid.uuid4().hex[:12]
            trigger = {"name": name, "enabled": True}
            if action == "schedule":
                from datetime import datetime
                at = datetime.fromisoformat(parameters.get("at", ""))
                if at.tzinfo is None or at.timestamp() <= time.time():
                    raise ValueError("Provide a future ISO time including its timezone offset")
                trigger.update(type="schedule", at=at.timestamp())
            else:
                folder = str(Path(parameters.get("folder", "")).resolve(strict=True))
                trigger.update(type="folder", folder=folder, signature=json.dumps(_folder_signature(folder)))
            rt.put("trigger", key, trigger)
            start_scheduler(rt)
            return report("task_manager", {"trigger_id": key, "requires": "JARVIS running", **trigger})
        if action == "triggers":
            return report("task_manager", rt.records("trigger"))
        if action == "disable_trigger":
            trigger = rt.get("trigger", key)
            if not trigger:
                raise ValueError("Unknown trigger")
            trigger["enabled"] = False
            rt.put("trigger", key, trigger)
            return report("task_manager", {"disabled": key})
        raise ValueError("Unknown task action")
    except Exception as exc:
        return outcome.failed("task_manager", str(exc))
