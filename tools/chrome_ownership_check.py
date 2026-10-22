"""Read-only Chrome ownership/window diagnostic. Never registers or closes windows."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    options = parser.parse_args()
    import psutil
    from core import appidentity as ai, desktop_ownership as ownership
    project = options.project.resolve()
    jarvis = []
    processes = list(psutil.process_iter(['pid', 'name', 'exe', 'ppid']))
    for proc in processes:
        try:
            if ('python' in (proc.info['name'] or '').casefold()
                    and any(Path(a).name.casefold() == 'main.py' for a in proc.cmdline()[1:])
                    and Path(proc.cwd()).resolve() == project):
                jarvis.append(proc.pid)
        except psutil.Error:
            pass
    grants = {}
    database = project / 'memory' / 'automation.db'
    if database.exists():
        from contextlib import closing
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute("SELECT name FROM sqlite_master WHERE name='records'").fetchone():
                grants = {int(key): json.loads(data) for key, data in db.execute("SELECT id,data FROM records WHERE kind='desktop_app'")}
    print(json.dumps({'jarvis_pids': jarvis, 'database_exists': database.exists(), 'mode': 'read_only'}))
    for proc in processes:
        if not ownership.is_chrome(proc.info.get('name', '')):
            continue
        result = {'pid': proc.pid, 'parent_pid': proc.info['ppid']}
        try:
            result['descendant_of_jarvis'] = bool(set(jarvis) & {p.pid for p in proc.parents()})
            result['created'] = proc.create_time()
            try:
                ownership.process_identity(proc.pid, chrome_only=True)
                result['role'] = 'ordinary_browser_process'
            except Exception as exc:
                result['role'] = 'protected_or_uninspectable'
                result['reason'] = str(exc)
            result['visible_windows'] = [ownership.window_info(h) for h in ai._hwnds_for(proc.pid)]
            result['recorded_hwnds'] = [w['hwnd'] for w in grants.get(proc.pid, {}).get('windows', [])]
        except Exception as exc:
            result['inspection_error'] = type(exc).__name__
        print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
