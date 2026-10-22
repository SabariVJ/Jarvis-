"""Read-only phase timing report. Inclusive nested phases must not be summed."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path(os.environ.get('JARVIS_AUTOMATION_DB') or
                        Path(__file__).resolve().parents[1] / 'memory' / 'automation.db'))
    parser.add_argument('--limit', type=int, default=5)
    options = parser.parse_args()
    if not options.db.exists():
        print('No timing database exists yet; none was created.')
        return
    with closing(sqlite3.connect(options.db.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        runs = db.execute("SELECT execution_id,request_id,at FROM automation_events WHERE tool='whatsapp_control' AND event='tool_received' ORDER BY seq DESC LIMIT ?",
                          (max(1, min(options.limit, 30)),)).fetchall()
        for run in reversed(runs):
            rows = db.execute('SELECT * FROM automation_events WHERE execution_id=? AND (request_id IS ? OR request_id IS NULL) ORDER BY seq',
                              (run['execution_id'], run['request_id'])).fetchall()
            phases, started, dispatched, running, finished = {}, None, run['at'], None, None
            for row in rows:
                data = json.loads(row['details'])
                if row['event'] == 'command_started':
                    started = row['at']
                if row['tool'] == 'whatsapp_control' and row['event'] == 'tool_running':
                    running = row['at']
                if row['tool'] == 'whatsapp_control' and row['event'] == 'tool_finished':
                    finished = row['at']
                if row['event'] == 'performance_span':
                    phase = phases.setdefault(data['phase'], {'inclusive_seconds': 0, 'calls': 0})
                    phase['inclusive_seconds'] += data['seconds']
                    phase['calls'] += 1
            for phase in phases.values():
                phase['inclusive_seconds'] = round(phase['inclusive_seconds'], 4)
            print(json.dumps({'execution_id': run['execution_id'], 'request_id': run['request_id'],
                'command_to_dispatch_seconds': round(dispatched-started, 4) if started else None,
                'queue_seconds': round(running-dispatched, 4) if running else None,
                'execution_seconds': round(finished-running, 4) if finished and running else None,
                'phases': phases, 'note': 'Nested phases overlap. Native command-to-dispatch includes transport/planning/earlier steps; pure model_planning is measured only for local pipelines.'}))


if __name__ == '__main__':
    main()
