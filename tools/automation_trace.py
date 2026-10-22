"""Read-only automation trace viewer. Does not start JARVIS or initialize its DB."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'memory' / 'automation.db')
    parser.add_argument('--limit', type=int, default=60)
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--processes', action='store_true', help='Also inspect actual WhatsApp/Chrome process identities')
    options = parser.parse_args()
    if options.processes:
        from core import appidentity
        for app in ('WhatsApp', 'Google Chrome'):
            matches = [m for m in appidentity.running(app, include_self=True) if m.score >= 30]
            print(json.dumps({'app': app, 'processes': [
                {'pid': m.pid, 'matched': m.label, 'score': m.score} for m in matches]}))
    if not options.db.exists():
        print('No automation database exists at this path yet. It was not created by this viewer.')
        return
    with sqlite3.connect(options.db.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if options.summary:
            counts = [dict(r) for r in db.execute('SELECT name,status,count(*) AS count FROM receipts GROUP BY name,status')] if 'receipts' in tables else []
            print(json.dumps({'database': str(options.db), 'receipt_counts': counts,
                              'trace_available': 'automation_events' in tables}, ensure_ascii=True))
            return
        if 'automation_events' not in tables:
            print('Legacy receipts exist, but they contain no execution/source trace. Historical relaunch cause cannot be determined from them.')
            return
        rows = db.execute('SELECT * FROM automation_events ORDER BY seq DESC LIMIT ?',
                          (max(1, min(options.limit, 5000)),)).fetchall()
        for row in reversed(rows):
            event = dict(row)
            event['at'] = datetime.fromtimestamp(event['at'], timezone.utc).isoformat()
            event['details'] = json.loads(event['details'])
            print(json.dumps(event, ensure_ascii=True))


if __name__ == '__main__':
    main()
