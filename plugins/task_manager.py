"""Persistent, bounded recipes. Interrupted side effects are never retried."""
import json
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
         "tool": string(), "arguments_json": string("JSON object of tool arguments")},
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
    "automation_status": {"status", "context", "performance"},
    "media_assistant": {"transcribe", "convert", "subtitles"},
}
_workers = {}
_lock = threading.RLock()
_scheduler = None


def validate_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError("A recipe needs between 1 and 20 steps")
    clean = []
    for step in steps:
        tool = step.get("tool")
        if tool not in ALLOWED:
            raise ValueError(f"{tool} is not supported in replayable workflows")
        args = json.loads(step.get("arguments_json", "{}"))
        if not isinstance(args, dict):
            raise ValueError("Each step needs a JSON object")
        if ALLOWED[tool] is not None and args.get("action") not in ALLOWED[tool]:
            raise ValueError(f"This {tool} action cannot be replayed")
        clean.append({"tool": tool, "arguments_json": json.dumps(args)})
    return clean


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
            step_context = {**context, "request_id": f"job:{job_id}:{index}", 'step_id': str(index)}
            result = rt.dispatch(step["tool"], json.loads(step["arguments_json"]), step_context)
            # Cancellation can arrive while the tool is executing. Preserve it
            # when saving our checkpoint instead of overwriting the newer flag.
            if rt.get("job", job_id).get("cancel_requested"):
                job["cancel_requested"] = True
            job["results"].append({"step": index, "result": result})
            job["active_step"] = None
            # Unknown / merely started / failed are not verified completion.
            if not str(result).startswith("status=done "):
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
    except Exception as exc:
        job.update(state="needs_attention", error=str(exc))
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
        if rt.notify:
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
    steps = validate_steps(recipe["steps"])
    job_id = uuid.uuid4().hex[:12]
    rt.put("job", job_id, {"name": name, "steps": steps, "next": 0, "active_step": None,
                           "results": [], "state": "queued", "created": time.time()})
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
            if trigger["type"] == "schedule":
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
                job["state"] = "needs_attention" if job.get("active_step") is not None else "paused"
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
                catalog = getattr(rt, "catalog", [])
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
            steps = validate_steps(steps)
            rt.put("recipe", name, {"steps": steps})
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
            if job["state"] != "paused" or job.get("active_step") is not None:
                raise ValueError("Only a paused job at a verified checkpoint can resume; uncertain steps need inspection")
            job.pop("cancel_requested", None)
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
