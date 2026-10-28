"""Local execution receipts, shared context, desktop exclusion and cancellation.

No network or GUI work at import time. The SQLite journal survives reconnects.
Receipts are written BEFORE execution; uncertain work is never replayed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from core import outcome

DESKTOP_TOOLS = {"open_app", "computer_control", "computer_settings", "desktop_control",
                 "whatsapp_control", "desktop_agent", "browser_control"}


class AutomationRuntime:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("JARVIS_AUTOMATION_DB") or
                         Path(__file__).resolve().parents[1] / "memory" / "automation.db")
        self._init_lock = threading.Lock()
        self._ready = False
        self._desktop = threading.RLock()
        self._state_lock = threading.RLock()
        self._context = {"turn_id": uuid.uuid4().hex}
        self._generation = 0
        self.dispatch = None
        self.notify = None
        self._recording = None
        self._local = threading.local()
        self._calling_pending = None
        self._calling_reply = None
        self._calling_grant = None
        self._context = self.get("context", "session", self._context)
        # Conversation restoration is not execution authorization. Retain the
        # old IDs for diagnosis, but never resume foreground work on startup.
        if self._context.get('execution_id'):
            previous = self.get('execution', self._context['execution_id'], {})
            if previous.get('state') in {'active', 'draining'}:
                self.update('execution', self._context['execution_id'], {'state': 'interrupted'})
        self._context.pop('execution_id', None)
        self._context.pop('task_id', None)
        self._context['source'] = 'idle'
        self.put("context", "session", self._context)

    @contextmanager
    def db(self):
        with self._init_lock:
            if not self._ready:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.path, timeout=10)
                try:
                    conn.executescript('''
                        PRAGMA journal_mode=WAL;
                        CREATE TABLE IF NOT EXISTS receipts
                          (id TEXT PRIMARY KEY, name TEXT, status TEXT, result TEXT,
                           started REAL, elapsed REAL);
                        CREATE TABLE IF NOT EXISTS guards (id TEXT PRIMARY KEY, at REAL);
                        CREATE TABLE IF NOT EXISTS records
                          (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id));
                        CREATE TABLE IF NOT EXISTS automation_events
                          (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, task_id TEXT,
                           turn_id TEXT, execution_id TEXT, request_id TEXT, source TEXT,
                           event TEXT, tool TEXT, details TEXT);
                    ''')
                finally:
                    conn.close()
                self._ready = True
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, kind, key, data):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)",
                       (kind, key, json.dumps(data, ensure_ascii=False)))

    def get(self, kind, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else default

    def records(self, kind):
        with self.db() as db:
            rows = db.execute("SELECT id,data FROM records WHERE kind=?", (kind,)).fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    def delete(self, kind, key):
        with self.db() as db:
            db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, key))

    def update(self, kind, key, changes, preserve=(), expected=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
            data = json.loads(row[0]) if row else {}
            if expected and any(data.get(k) != v for k, v in expected.items()):
                raise InterruptedError('Stale worker cannot update a newer execution')
            merged = {**data, **changes}
            for field in preserve:
                if data.get(field):
                    merged[field] = data[field]
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", (kind, key, json.dumps(merged)))
        return merged

    def trace(self, event, context=None, name=None, **details):
        context = context or getattr(self._local, 'context', None) or self.context()
        with self.db() as db:
            db.execute('INSERT INTO automation_events(at,task_id,turn_id,execution_id,request_id,source,event,tool,details) '
                       'VALUES (?,?,?,?,?,?,?,?,?)',
                       (time.time(), context.get('task_id'), context.get('turn_id'), context.get('execution_id'),
                        context.get('request_id'), context.get('source'), event, name, json.dumps(details, default=str)))

    def new_turn(self, text, source='user'):
        submitted_at = time.time()
        with self._state_lock:
            old = self._context.copy()
            if old.get('execution_id'):
                self.finish(old, 'superseded')
            identity = uuid.uuid4().hex
            self._context.update(turn_id=identity, execution_id=identity, task_id=identity,
                                 source=source, lifecycle='foreground', user_text=str(text)[:2000],
                                 input_final=source != 'speech_live')
            from core.calling_intents import advance, followup
            advance(self, old, text, self._context['input_final'])
            from core.command_router import authorization_plan as plan
            self.put('execution', identity, {'state': 'active', 'lifecycle': 'foreground',
                     'turn_id': identity, 'task_id': identity, 'source': source,
                     'plan': followup(self, self._context) or plan(text), 'plan_next': 0})
            self.put("context", "session", self._context)
            context = self.context()
            self.trace('command_started', context, submitted_at=submitted_at)
            return context

    def set_route(self, context, steps):
        with self._state_lock:
            self.check_cancelled(context)
            execution = self.get('execution', context['execution_id'])
            if execution.get('plan_next', 0) or execution.get('batches'):
                raise InterruptedError('Cannot replace a command plan after dispatch')
            self.update('execution', context['execution_id'], {'plan': steps,
                        'step_progress': [{'state': 'pending'} for _ in steps]})

    def report_outcome(self, context, channel):
        """A report timestamp, not proof that an unverified action succeeded."""
        with self.db() as db:
            row = db.execute("SELECT at,details FROM automation_events WHERE execution_id=? AND event='tool_finished' ORDER BY seq DESC LIMIT 1",
                             (context.get('execution_id'),)).fetchone()
        if row:
            verified = json.loads(row['details']).get('state') == 'done'
            self.trace('outcome_reported', context, channel=channel, verified=verified,
                       seconds_since_tool_finish=max(0, time.time() - row['at']))

    def update_turn_text(self, text, final=True):
        with self._state_lock:
            self._context['user_text'] = str(text)[:2000]
            self._context['input_final'] = bool(final)
            from core.calling_intents import refresh, followup
            refresh(self, text, final)
            from core.command_router import authorization_plan as plan
            if self._context.get('execution_id'):
                self.update('execution', self._context['execution_id'], {'plan': followup(self, self._context) or plan(text)})

    def clear_calling_context(self):
        from core.calling_intents import clear
        with self._state_lock:
            clear(self)

    def finish(self, context, state='completed'):
        identity = context.get('execution_id')
        if identity:
            with self._state_lock:
                current = self.get('execution', identity)
                if current and current['state'] in {'active', 'draining'}:
                    self.update('execution', identity, {'state': state})
                    self.trace('execution_' + state, context)
                if state in {'cancelled', 'interrupted'} and identity == self._context.get('execution_id'):
                    self.clear_calling_context()
                elif state == 'completed' and (self._calling_grant or {}).get('execution_id') == identity:
                    self._calling_grant = None
                    self._calling_reply = None

    def admit_batch(self, context):
        with self._state_lock:
            self.check_cancelled(context)
            execution = self.get('execution', context['execution_id'])
            if execution['state'] != 'active':
                raise InterruptedError('Command already finished accepting tool batches')
            batch = uuid.uuid4().hex
            self.update('execution', context['execution_id'], {'batches': execution.get('batches', []) + [batch]})
            return {**context, 'batch_id': batch}

    def release_batch(self, context):
        if not context.get('batch_id'):
            return
        with self._state_lock:
            execution = self.get('execution', context['execution_id'], {})
            batches = [b for b in execution.get('batches', []) if b != context['batch_id']]
            self.update('execution', context['execution_id'], {'batches': batches})
            if execution.get('state') == 'draining' and not batches:
                self.finish(context)

    def end_turn(self, context):
        with self._state_lock:
            execution = self.get('execution', context.get('execution_id', ''), {})
            if execution.get('state') == 'active':
                if execution.get('batches'):
                    self.update('execution', context['execution_id'], {'state': 'draining'})
                else:
                    self.finish(context)

    def job_context(self, job_id, parent=None, resume=False):
        job = self.get('job', job_id)
        if not job or job['state'] not in {'queued', 'running', 'checkpoint', 'paused'}:
            raise InterruptedError('Job is no longer authorized')
        identity = job.get('execution_id')
        if not identity or resume:
            identity = uuid.uuid4().hex
            self.update('job', job_id, {'execution_id': identity})
            self.put('execution', identity, {'state': 'active', 'lifecycle': 'job', 'task_id': job_id,
                     'turn_id': (parent or self.context()).get('turn_id'), 'source': 'workflow'})
        execution = self.get('execution', identity)
        return {'execution_id': identity, 'task_id': job_id, 'turn_id': execution['turn_id'],
                'lifecycle': 'job', 'source': 'workflow', 'generation': self._generation}

    def context(self, request_id=None):
        with self._state_lock:
            return {**self._context, "request_id": request_id, "generation": self._generation}

    def cancel(self, all_jobs=False):
        with self._state_lock:
            self._generation += 1
            self.clear_calling_context()
            self.finish(self._context, 'cancelled')
            if all_jobs:
                for key, job in self.records('job').items():
                    if job.get('state') in {'queued', 'running', 'checkpoint', 'paused'}:
                        self.update('job', key, {'cancel_requested': True})
            self.trace('cancellation_requested', self._context, all_jobs=all_jobs)

    def check_cancelled(self, context):
        context = context or getattr(self._local, 'context', None)
        if context and context.get('lifecycle') != 'job' and context.get("generation", self._generation) != self._generation:
            raise InterruptedError("Task cancelled; no further steps will run.")
        if context:
            execution = self.get('execution', context.get('execution_id', ''))
            draining_batch = (execution and execution.get('state') == 'draining'
                              and context.get('batch_id') in execution.get('batches', []))
            if not execution or not (execution.get('state') == 'active' or draining_batch):
                self.trace('action_rejected', context, reason='execution_not_active')
                raise InterruptedError('Execution is completed, cancelled or superseded; action rejected')
            if context.get('lifecycle') == 'job':
                job = self.get('job', context['task_id'], {})
                if job.get('cancel_requested') or job.get('state') in {'completed', 'needs_attention'}:
                    raise InterruptedError('Workflow is no longer authorized')
                if job.get('execution_id') != context['execution_id']:
                    raise InterruptedError('Stale workflow worker rejected')

    def effect(self, event, context=None, **details):
        """Check at the side-effect boundary, including inside delayed handlers."""
        context = context or getattr(self._local, 'context', None)
        if context:
            self.check_cancelled(context)
        self.trace(event, context, **details)

    @contextmanager
    def bound_context(self, context):
        previous = getattr(self._local, 'context', None)
        self._local.context = context
        try:
            yield
        finally:
            self._local.context = previous

    @contextmanager
    def side_effect(self, event, context=None, **details):
        # Atomically order the last authorization check and the short OS call
        # against new_turn/cancel. Do not hold this across loading/polling.
        with self._state_lock:
            self.effect(event, context, **details)
            yield

    def authorize(self, name, args, context):
        self.check_cancelled(context)
        if name == 'whatsapp_control' and str(args.get('action', '')).endswith('_call'):
            if (context.get('command_source') or context.get('source')) == 'speech_live' and not context.get('input_final'):
                raise InterruptedError('A finished user transcript is required before authorizing a call')
            from core.calling_intents import permits_followup, authorization
            from core.command_router import canonical_arguments
            followup_allowed = permits_followup(self, args, context)
            explicit = authorization(context.get('user_text', '')) or []
            explicit_allowed = any(n == name and canonical_arguments(n, a) == canonical_arguments(name, args)
                                   for n, a in explicit)
            if context.get('lifecycle') == 'job' or not (followup_allowed or explicit_allowed):
                raise InterruptedError('A fresh explicit user request naming this contact is required for a call')
        execution = self.get('execution', context['execution_id'])
        steps = execution.get('plan')
        if steps is not None:
            if execution.get('plan_blocked'):
                raise InterruptedError('A preceding step was not verified; dependent actions are blocked')
            from core.command_router import canonical_arguments
            signature = (name, canonical_arguments(name, args))
            allowed = [(n, canonical_arguments(n, a)) for n, a in steps]
            if signature not in allowed:
                raise InterruptedError('Tool action is outside the current explicit app command')
            index = execution.get('plan_next', 0)
            if index < len(allowed) and signature != allowed[index]:
                raise InterruptedError('Earlier steps must finish before this app action')
        elif name == 'open_app' and context.get('lifecycle') != 'job':
            from core.command_router import canonical_app, APPS
            app = canonical_app(args.get('app_name', ''))
            text = context.get('user_text', '').casefold()
            aliases = [alias for alias, target in APPS.items() if canonical_app(target) == app] + [app]
            if not any(alias in text for alias in aliases) or not re.search(r'\b(open|launch|start|use|switch)\b', text):
                raise InterruptedError('Opening this application is not authorized by the current command')

    def bind_model_call(self, name, args, context):
        """Keep native-provider call IDs attached to their original command.

        Local pipeline messages carry full provenance. Native Live messages do
        not, so remember observed IDs as well; reused IDs may cross commands
        only when the new user explicitly requests that same app/call action.
        """
        request = context.get('request_id')
        if not request:
            return
        from core.command_router import canonical_arguments
        payload = json.dumps([request, name, canonical_arguments(name, args)], sort_keys=True)
        key = hashlib.sha256(payload.encode()).hexdigest()
        with self._state_lock:
            previous = self.get('provider_call', key)
            if previous and previous['execution_id'] != context.get('execution_id'):
                execution = self.get('execution', context.get('execution_id', ''), {})
                explicit = any(n == name and canonical_arguments(n, a) == canonical_arguments(name, args)
                               for n, a in execution.get('plan') or [])
                if name == 'whatsapp_control' and str(args.get('action', '')).endswith('_call'):
                    self.authorize(name, args, context)
                    explicit = True
                if not explicit:
                    self.trace('tool_invocation_rejected', context, name, reason='call_id_belongs_to_previous_execution',
                               original_execution_id=previous['execution_id'])
                    raise InterruptedError('This model call belongs to a previous command; not replayed')
            self.put('provider_call', key, {'execution_id': context.get('execution_id'), 'turn_id': context.get('turn_id')})

    @contextmanager
    def desktop(self, context=None):
        waiting = time.perf_counter()
        if not self._desktop.acquire(timeout=20):
            raise TimeoutError("Another desktop action is still running.")
        com = None
        try:
            self.trace('performance_span', context, phase='desktop_lock_wait', parent=None,
                       seconds=round(time.perf_counter() - waiting, 6))
            if os.name == "nt":
                import pythoncom
                pythoncom.CoInitialize()
                com = pythoncom
            self.check_cancelled(context)
            yield
        finally:
            if com:
                com.CoUninitialize()
            self._desktop.release()

    def reserve_call(self, contact, context=None):
        """Atomically reserve one call attempt across threads/processes/restarts."""
        context = context or {}
        if context:
            action = 'voice_call'
            execution = self.get('execution', context.get('execution_id', ''), {})
            planned = execution.get('plan') or []
            index = execution.get('plan_next', 0)
            if index < len(planned) and planned[index][0] == 'whatsapp_control':
                candidate = planned[index][1].get('action')
                if candidate in {'voice_call', 'video_call'}:
                    action = candidate
            self.authorize('whatsapp_control', {'action': action, 'contact': contact}, context)
        token = context.get("turn_id") or context.get("request_id")
        key = "call:" + hashlib.sha256((str(token) + ":" + contact.casefold()).encode()).hexdigest()
        now = time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if token and db.execute("SELECT 1 FROM guards WHERE id=?", (key,)).fetchone():
                return "This request already attempted a call. A new explicit request is required."
            row = db.execute("SELECT at FROM guards WHERE id='last_call'").fetchone()
            if row and now - row[0] < 30:
                return "Call cooldown is active. Wait 30 seconds after the last attempt and ask again."
            db.execute("INSERT OR REPLACE INTO guards VALUES ('last_call',?)", (now,))
            if token:
                db.execute("INSERT OR REPLACE INTO guards VALUES (?,?)", (key, now))
        return None

    def receipt_id(self, name, args, context):
        from core.command_router import canonical_arguments
        payload = json.dumps([name, canonical_arguments(name, args)], sort_keys=True, ensure_ascii=False)
        scope = str(context.get('execution_id')) + ':' + str(context.get('step_id', ''))
        if context.get('lifecycle') == 'job':
            scope = 'job:' + context['task_id'] + ':' + str(context.get('step_id', ''))
        if context.get('retry_attempt'):
            scope += ':retry:' + str(context['retry_attempt'])
        return hashlib.sha256((scope + payload).encode()).hexdigest()

    def lookup_receipt(self, name, args, context):
        with self.db() as db:
            row = db.execute('SELECT * FROM receipts WHERE id=?', (self.receipt_id(name, args, context),)).fetchone()
        return dict(row) if row else None

    def execute(self, name, args, invoke, context=None):
        context = context or self.context()
        self.check_cancelled(context)
        request_id = context.get("request_id")
        from core.command_router import canonical_arguments
        payload = json.dumps([name, canonical_arguments(name, args)], sort_keys=True, ensure_ascii=False)
        execution = self.get('execution', context.get('execution_id', ''), {})
        steps = execution.get('plan')
        if steps is not None and 'step_id' not in context:
            index = execution.get('plan_next', 0)
            matching = [i for i, (n, a) in enumerate(steps)
                        if n == name and canonical_arguments(n, a) == canonical_arguments(name, args)]
            if index in matching:
                context = {**context, 'step_id': str(index)}
            elif any(i < index for i in matching):
                context = {**context, 'step_id': str(max(i for i in matching if i < index))}
        # Some providers reuse IDs such as call_open_app on every user turn.
        # Provider call IDs can change on reconnect or be reused. They are
        # diagnostic only; execution + explicit recipe step + canonical args
        # determine idempotency, even when the provider supplies no call ID.
        if context.get('retry_attempt'):
            from plugins.task_manager import authorize_retry
            authorize_retry(self, name, args, context)
        receipt = self.receipt_id(name, args, context)
        self.trace('tool_received', context, name, arguments_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                   app=canonical_arguments(name, args).get('app_name'), action=args.get('action'), receipt=receipt)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM receipts WHERE id=?", (receipt,)).fetchone()
            if not previous:
                db.execute("INSERT INTO receipts VALUES (?,?, 'running', NULL, ?, NULL)",
                           (receipt, name, time.time()))
        if previous:
            self.trace('receipt_reused', context, name, receipt=receipt, state=previous['status'])
            return previous["result"] or outcome.unverified(name, "Earlier execution is still running or was interrupted; not replayed")
        start = time.monotonic()
        attempted = False
        invocation_started = None
        previous_context = getattr(self._local, 'context', None)
        self._local.context = context
        try:
            if name in DESKTOP_TOOLS:
                with self.desktop(context):
                    self.authorize(name, args, context)
                    self.trace('tool_running', context, name, receipt=receipt)
                    attempted = True
                    invocation_started = time.perf_counter()
                    result = invoke()
            else:
                self.authorize(name, args, context)
                self.trace('tool_running', context, name, receipt=receipt)
                attempted = True
                invocation_started = time.perf_counter()
                result = invoke()
            result = str(result or outcome.unverified(name, "Tool returned no result"))
        except Exception as exc:
            result = outcome.failed(name, f"{type(exc).__name__}: {exc}")
        finally:
            self._local.context = previous_context
            if invocation_started is not None:
                self.trace('performance_span', context, phase='tool_execution', parent=None,
                           seconds=round(time.perf_counter() - invocation_started, 6))
        status = "done" if outcome.is_verified(result) else "reported"
        if outcome.is_problem(result):
            status = "problem"
        with self.db() as db:
            db.execute("UPDATE receipts SET status=?,result=?,elapsed=? WHERE id=?",
                       (status, result, time.monotonic() - start, receipt))
        with self._state_lock:
            execution = self.get('execution', context['execution_id'], {})
            if attempted and execution.get('plan') is not None:
                index = execution.get('plan_next', 0)
                progress = execution.get('step_progress') or [{'state': 'pending'} for _ in execution['plan']]
                if index < len(progress):
                    progress[index] = {'state': 'verified' if status == 'done' else 'failed'}
                self.update('execution', context['execution_id'], {
                    'plan_next': index + 1 if status == 'done' else index,
                    'plan_blocked': status != 'done', 'step_progress': progress})
            owns_foreground = self._context.get('execution_id') == context.get('execution_id')
            if owns_foreground:
                self._context.update(last_tool=name, last_result=result[:1200])
                if status == "done" and args.get("contact"):
                    self._context["last_contact"] = args["contact"]
                if status == "done" and args.get("app_name"):
                    self._context["last_app"] = args["app_name"]
            if owns_foreground and self._recording is not None and name not in {"task_manager", "automation_status"}:
                if status == "done":
                    self._recording.append({"tool": name, "arguments_json": json.dumps(args)})
        self.trace('tool_finished', context, name, receipt=receipt, state=status, result=result[:1800])
        return result

    def record_start(self):
        with self._state_lock:
            if self._recording is not None:
                raise ValueError("Already recording JARVIS tool actions")
            self._recording = []

    def record_finish(self):
        with self._state_lock:
            if self._recording is None:
                raise ValueError("No recording is active")
            steps, self._recording = self._recording, None
            return steps

    def stats(self):
        with self.db() as db:
            rows = db.execute("SELECT name,COUNT(*) AS calls,ROUND(AVG(elapsed),3) AS mean_seconds,"
                              "SUM(status='problem') AS problems FROM receipts GROUP BY name").fetchall()
        return [dict(row) for row in rows]


_instance = None
_lock = threading.Lock()


def get_runtime():
    global _instance
    with _lock:
        if _instance is None:
            _instance = AutomationRuntime()
        return _instance
