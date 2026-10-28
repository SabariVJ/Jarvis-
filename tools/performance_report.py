"""Read-only CLI for command, dispatch, UI and outcome-reporting timings."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.performance_report import performance_report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path(__file__).resolve().parents[1] / 'memory/automation.db')
    options = parser.parse_args()
    print(json.dumps(performance_report(options.db), indent=2))
