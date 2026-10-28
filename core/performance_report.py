"""Read-only, content-free latency summaries. Nested spans are inclusive."""
from collections import defaultdict
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import statistics


def distribution(values):
    values = sorted(value for value in values if isinstance(value, (int, float)) and value >= 0)
    return {'samples': len(values), 'median_seconds': round(statistics.median(values), 6) if values else None,
            'p95_seconds': round(values[math.ceil(.95 * len(values)) - 1], 6) if len(values) >= 20 else None}


def performance_report(path, limit=20000):
    path = Path(path)
    if not path.is_file():
        return {'measurements': {}, 'note': 'No timing database; none was created'}
    values, commands, received, running, dispatched, submitted = defaultdict(list), {}, {}, {}, set(), {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM automation_events ORDER BY seq DESC LIMIT ?',
                          (max(1, min(int(limit), 100000)),)).fetchall()[::-1]
    for row in rows:
        event, identity = row['event'], row['execution_id']
        data = json.loads(row['details'])
        key = (identity, row['request_id'], row['tool'])
        if event == 'command_started':
            commands[identity] = data.get('submitted_at', row['at'])
        elif event == 'intent_recognized' and identity in commands:
            values['command_to_intent:' + data.get('route', 'unknown')].append(row['at'] - commands[identity])
        elif event == 'tool_received':
            received[key] = row['at']
            if key in submitted:
                values['dispatch_submission_to_receipt_includes_executor_queue'].append(row['at'] - submitted[key])
            if identity in commands and identity not in dispatched:
                values['command_to_first_dispatch_includes_transport_and_planning'].append(row['at'] - commands[identity])
                dispatched.add(identity)
        elif event == 'tool_running':
            running[key] = row['at']
            if key in received:
                values['dispatch_to_running_includes_lock_wait'].append(row['at'] - received[key])
        elif event == 'tool_finished' and key in running:
            values['execution:' + str(row['tool'])].append(row['at'] - running[key])
        elif event == 'performance_span':
            values['inclusive_phase:' + str(data.get('phase'))].append(data.get('seconds'))
        elif event == 'outcome_reported':
            channel = str(data.get('channel', 'unknown'))
            values['tool_finish_to_report:' + channel].append(data.get('seconds_since_tool_finish'))
            if data.get('verified') and identity in commands:
                values['command_to_verified_report:' + channel].append(row['at'] - commands[identity])
        elif event in {'dispatch_submitted', 'tool_invocation'}:
            submitted[key] = row['at']
    return {'event_rows': len(rows), 'measurements': {key: distribution(items) for key, items in sorted(values.items())},
            'limitations': ['p95 omitted below 20 samples; nearest-rank estimator otherwise.',
                            'Inclusive nested phases overlap; do not sum them.',
                            'Native audio planning/transport cannot be separated without provider telemetry.',
                            'Reporting metrics distinguish UI activity log from assistant transcript; neither measures speaker playback.',
                            'Historical samples mix successes/failures and loading states; not a controlled benchmark.']}
