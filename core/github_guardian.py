"""Opt-in, read-only GitHub polling through the owner's installed gh CLI.

No import-time network, token extraction, model calls, publishing or new daemon.
Repository content is data, never execution authority. Checkpoints live in the
existing automation journal and scheduled reads use the existing task manager.
"""
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from urllib.parse import urlsplit

from core.git_security import redact
from core.process_runner import run_process

_serial = threading.RLock()
_polling = threading.Lock()
SLUG = r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}'
ACTIONS = {'guardian_status', 'guardian_connection', 'guardian_discover', 'guardian_next',
           'guardian_watch', 'guardian_unwatch', 'guardian_check', 'guardian_poll',
           'guardian_health', 'guardian_broken', 'guardian_pause', 'guardian_resume'}


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(SLUG, value) or value.split('/')[1] in {'.', '..'}:
        raise ValueError('Use an exact GitHub owner/repository, not a URL, path or command')
    return value.casefold()


def command(text):
    text = ' '.join(str(text).split()).rstrip('.!?').casefold()
    text = re.sub(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?', '', text)
    names = {
        'show github command center': 'status', 'show monitored repositories': 'status',
        'check github guardian connection': 'connection', 'discover github repositories': 'discover',
        'list my github repositories': 'discover', 'show more github repositories': 'next',
        'check all my monitored github projects': 'check', 'check monitored github projects': 'check',
        'show github project health': 'health', 'what changed in my repositories today': 'health',
        'show me the broken builds': 'broken', 'show broken github builds': 'broken',
        'pause github monitoring': 'pause', 'resume github monitoring': 'resume',
    }
    if text in names:
        return {'action': 'guardian_' + names[text]}
    match = re.fullmatch(r'(watch|unwatch) github (' + SLUG.casefold() + r')', text)
    if match:
        return {'action': 'guardian_' + match[1], 'repository': slug(match[2])}
    return None


def text(value, limit=140):
    return re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', ' ', redact(str(value or '')))[:limit]


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def environment():
    # gh manages its own approved OS credential store; never retrieve a token.
    allowed = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT',
               'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'HOME', 'PROGRAMFILES', 'PROGRAMFILES(X86)'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(GH_HOST='github.com', GH_PROMPT_DISABLED='1', GH_PAGER='cat', NO_COLOR='1')
    return env


class ApiError(ValueError):
    def __init__(self, message, status=None, headers=None):
        super().__init__(message)
        self.status, self.headers = status, headers or {}


def response(raw):
    raw = raw.replace('\r\n', '\n')
    match = re.match(r'HTTP/\S+ (\d{3})[^\n]*\n(.*?)\n\n(.*)\Z', raw, re.S)
    if not match:
        raise ApiError('GitHub CLI returned no complete HTTP response; authentication/network access unverified')
    status = int(match[1])
    headers = {}
    for row in match[2].splitlines():
        if ':' in row:
            k, v = row.split(':', 1)
            headers[k.lower()] = v.strip()
    body = None
    if status not in {204, 304}:
        # gh appends its diagnostic after the JSON on nonzero HTTP responses.
        try:
            body, _ = json.JSONDecoder().raw_decode(match[3].lstrip())
        except (ValueError, TypeError):
            if status < 400:
                raise ApiError('GitHub returned malformed or truncated JSON', status)
    return status, headers, body


class GhClient:
    def __init__(self, rt, context):
        self.rt, self.context = rt, context
        self.executable = shutil.which('gh.exe' if os.name == 'nt' else 'gh')
        if not self.executable:
            raise ApiError('GitHub CLI gh is not installed/on PATH. Install the official CLI, then run gh auth login --hostname github.com using your owner account. Git/GCM access does not establish GitHub API access. No account was changed.')
        self.executable = str(Path(self.executable).resolve(strict=True))
        version = self.run(['--version'])
        if version.returncode or not version.stdout.startswith('gh version ') or 'github.com/cli/cli' not in version.stdout:
            raise ApiError('Installed gh does not report the official GitHub CLI interface')
        if os.name == 'nt':
            ps = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
            env = environment()
            env['JARVIS_GH_EXE'] = self.executable
            check = run_process([ps, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
                "$s=Get-AuthenticodeSignature -LiteralPath $env:JARVIS_GH_EXE; $n=if($s.SignerCertificate){$s.SignerCertificate.GetNameInfo('SimpleName',$false)}else{''}; if($s.Status -eq 'Valid' -and $n -in @('GitHub','GitHub, Inc.')){Write-Output 'GITHUB_SIGNATURE_VALID';exit 0};exit 1"],
                context=context, timeout=15, env=env, max_output_bytes=2048, allow_truncated=False)
            if check.returncode or check.stdout.strip() != 'GITHUB_SIGNATURE_VALID':
                raise ApiError('GitHub CLI publisher signature is not verified; no API request sent')

    def run(self, args):
        return run_process([self.executable, *args], context=self.context, timeout=15,
                           env=environment(), max_output_bytes=512000, allow_truncated=False)

    def get(self, endpoint, etag=None):
        # Only internal fixed endpoints and vetted GitHub pagination links enter.
        if endpoint.startswith(('http:', 'https:', '-', '/')) or any(c in endpoint for c in '\r\n\\#'):
            raise ValueError('Only relative fixed GitHub API paths are supported')
        with _serial:
            self.rt.check_cancelled(self.context)
            rate = self.rt.get('guardian', 'rate', {})
            if time.time() < rate.get('next_at', 0):
                raise ApiError('GitHub API backoff active until ' + str(int(rate['next_at'])), 429)
            args = ['api', '--hostname', 'github.com', '--method', 'GET', '--include',
                    '--header', 'Accept: application/vnd.github+json',
                    '--header', 'X-GitHub-Api-Version: 2022-11-28']
            if etag and isinstance(etag, str) and len(etag) < 300 and not re.search(r'[\r\n]', etag):
                args += ['--header', 'If-None-Match: ' + etag]
            done = self.run([*args, endpoint])
            try:
                status, headers, body = response(done.stdout)
            except ApiError:
                self.rt.put('guardian', 'rate', {'next_at': time.time() + 60})
                raise
            minimum = 0
            try:
                minimum = max(0, float(headers.get('x-poll-interval', 0)))
                if headers.get('x-ratelimit-remaining') == '0':
                    minimum = max(minimum, float(headers.get('x-ratelimit-reset', 0)) - time.time())
                if status == 429 or (status == 403 and ('retry-after' in headers or 'rate limit' in str((body or {}).get('message', '')).casefold())):
                    previous = rate.get('failures', 0)
                    minimum = max(minimum, float(headers.get('retry-after', 0)), min(3600, 60 * 2 ** min(previous, 6)))
                    self.rt.put('guardian', 'rate', {'next_at': time.time() + minimum, 'failures': previous + 1})
                    raise ApiError('GitHub rate limit; read deferred without retry', status, headers)
            except (TypeError, OverflowError):
                raise ApiError('Invalid GitHub rate headers; read blocked')
            if minimum:
                self.rt.put('guardian', 'rate', {'next_at': time.time() + minimum})
            if status == 304:
                return status, headers, None
            if done.returncode or status != 200:
                explanation = {401: 'GitHub authentication failed; run gh auth status --hostname github.com',
                    403: 'GitHub read permission denied for this endpoint', 404: 'GitHub resource unavailable or private read permission missing'}.get(status, 'GitHub API read failed')
                raise ApiError(explanation + ' (HTTP ' + str(status) + ')', status, headers)
            return status, headers, body


def identify(client, rt, context):
    try:
        _, _, user = client.get('user')
        if not isinstance(user, dict) or type(user.get('id')) is not int or not isinstance(user.get('login'), str):
            raise ApiError('GitHub did not return a verified account identity')
        result = {'connected': True, 'id': user['id'], 'login': text(user['login'], 39), 'checked_at': time.time(),
                  'git_credentials': 'separate Git/GCM capability', 'publish_permission': 'not_verified', 'api_method': 'GET only'}
        rt.check_cancelled(context)
        rt.put('guardian', 'connection', result)
        return result
    except Exception:
        rt.put('guardian', 'connection', {'connected': False, 'checked_at': time.time(), 'error': 'Account/transport verification failed; no monitoring read authorized'})
        raise


def repository_meta(data):
    if not isinstance(data, dict) or not data.get('full_name') or type(data.get('id')) is not int:
        raise ApiError('Malformed repository metadata')
    permissions = data.get('permissions') or {}
    return {'repository': slug(data['full_name']), 'id': data['id'], 'private': data.get('private') is True,
            'read_access_verified': True,
            'default_branch': text(data.get('default_branch'), 200),
            'permissions': {key: permissions.get(key) is True for key in ('pull', 'push', 'admin', 'maintain', 'triage')},
            'workflow_write_permission': 'not_verified', 'publishing_approved': False}


def discover(client, rt, account, more=False):
    prior = rt.get('guardian', 'discovery', {})
    endpoint = 'user/repos?per_page=50&sort=updated'
    if more:
        if prior.get('account_id') != account['id'] or not prior.get('next'):
            raise ValueError('No next discovery page for this verified account')
        endpoint = prior['next']
    _, headers, rows = client.get(endpoint)
    if not isinstance(rows, list):
        raise ApiError('Repository discovery returned an unexpected shape')
    next_page = None
    for url in re.findall(r'<([^>]+)>;\s*rel="next"', headers.get('link', '')):
        parsed = urlsplit(url)
        if parsed.scheme == 'https' and parsed.netloc == 'api.github.com' and parsed.path == '/user/repos' and not parsed.fragment:
            next_page = parsed.path.lstrip('/') + '?' + parsed.query
    result = {'account_id': account['id'], 'repositories': [repository_meta(row) for row in rows[:50]],
              'next': next_page, 'partial': next_page is not None, 'selected_for_monitoring': False, 'at': time.time()}
    rt.put('guardian', 'discovery', result)
    return result


def fields(kind, body):
    """Persist no issue bodies, code snippets, workflow logs, tokens or raw API output."""
    if kind == 'metadata':
        return repository_meta(body)
    if kind == 'runs':
        body = body.get('workflow_runs') if isinstance(body, dict) else None
    if not isinstance(body, list):
        raise ApiError('GitHub returned an unexpected ' + kind + ' response')
    result = []
    for item in body[:30]:
        if not isinstance(item, dict):
            raise ApiError('Malformed GitHub list entry')
        if kind == 'issues' and 'pull_request' in item:
            continue
        keys = {'branches': ('name',), 'commits': ('sha',), 'issues': ('number', 'title', 'updated_at'),
                'pulls': ('number', 'title', 'updated_at', 'draft'), 'runs': ('id', 'name', 'status', 'conclusion', 'updated_at'),
                'releases': ('id', 'tag_name', 'published_at'), 'deployments': ('id', 'environment', 'created_at'),
                'dependabot': ('number', 'state'), 'code_scanning': ('number', 'state'), 'deployment_status': ('id', 'state', 'created_at')}[kind]
        row = {key: text(item[key]) if isinstance(item.get(key), str) else item.get(key)
               for key in keys if isinstance(item.get(key), (str, int, bool))}
        if kind == 'branches':
            row['sha'] = text((item.get('commit') or {}).get('sha'), 64)
        if kind in {'issues', 'pulls'}:
            row['assignees'] = [text(user.get('login'), 39) for user in item.get('assignees', [])[:20] if isinstance(user, dict)]
            row['review_requested'] = [text(user.get('login'), 39) for user in item.get('requested_reviewers', [])[:20] if isinstance(user, dict)]
            row['labels'] = [text(label.get('name'), 40) for label in item.get('labels', [])[:20] if isinstance(label, dict)]
        if kind == 'dependabot':
            row['severity'] = text((item.get('security_advisory') or {}).get('severity'), 20)
        if kind == 'code_scanning':
            row['severity'] = text((item.get('rule') or {}).get('security_severity_level'), 20)
        result.append(row)
    return result


def watch_current(rt, repository, revision, context):
    rt.check_cancelled(context)
    current = rt.get('guardian_watch', repository, {})
    if not current.get('enabled') or current.get('revision') != revision or rt.get('guardian', 'settings', {}).get('paused'):
        raise InterruptedError('Monitoring selection paused, removed or changed; stale read discarded')
    return current


def _poll(repository, client, rt, context, account):
    repository = slug(repository)
    selected = rt.get('guardian_watch', repository, {})
    revision = selected.get('revision')
    watch_current(rt, repository, revision, context)
    if selected.get('account_id') != account['id']:
        raise ValueError('GitHub account changed; explicitly reselect this repository under the verified account')
    old = rt.get('guardian_health', repository, {})
    endpoints = {'metadata': '', 'branches': '/branches?per_page=30', 'commits': '/commits?per_page=10',
        'issues': '/issues?state=open&per_page=30', 'pulls': '/pulls?state=open&per_page=30',
        'runs': '/actions/runs?per_page=20', 'releases': '/releases?per_page=10',
        'deployments': '/deployments?per_page=10', 'dependabot': '/dependabot/alerts?state=open&per_page=30',
        'code_scanning': '/code-scanning/alerts?state=open&per_page=30'}
    observed, unavailable, partial = {}, {}, []
    started = time.perf_counter()
    for kind, suffix in endpoints.items():
        watch_current(rt, repository, revision, context)
        key = digest([account['id'], revision, repository, kind])
        cached = rt.get('guardian_cache', key, {})
        try:
            status, headers, data = client.get('repos/' + repository + suffix, cached.get('etag'))
            if status == 304:
                if 'data' not in cached:
                    raise ApiError('Conditional response has no matching checkpoint')
                value = cached['data']
            else:
                value = fields(kind, data)
            if kind == 'metadata' and (value['repository'] != repository or value['id'] != selected['repository_id']):
                raise ApiError('Repository identity changed; owner must reselect exact repository')
            if 'rel="next"' in headers.get('link', '') or (status == 304 and cached.get('partial')):
                partial.append(kind)
            observed[kind] = value
            with rt.side_effect('guardian_read_checkpoint', context):
                watch_current(rt, repository, revision, context)
                rt.put('guardian_cache', key, {'etag': headers.get('etag', cached.get('etag')), 'data': value,
                                              'partial': kind in partial})
        except ApiError as exc:
            if kind == 'metadata' or exc.status in {401, 429} or exc.headers.get('retry-after'):
                raise
            unavailable[kind] = str(exc)
    # Deployment objects do not establish deployment success. Verify latest state.
    for deployment in observed.get('deployments', [])[:3]:
        if type(deployment.get('id')) is not int:
            continue
        watch_current(rt, repository, revision, context)
        try:
            _, _, body = client.get('repos/' + repository + '/deployments/' + str(deployment['id']) + '/statuses?per_page=1')
            state = fields('deployment_status', body)
            deployment['verified_status'] = state[0].get('state') if state else 'unknown'
        except ApiError as exc:
            if exc.status in {401, 429}:
                raise
            deployment['verified_status'] = 'unavailable'
    failed = [run for run in observed.get('runs', []) if run.get('conclusion') in {'failure', 'timed_out', 'startup_failure', 'action_required'}]
    critical = [row for kind in ('dependabot', 'code_scanning') for row in observed.get(kind, []) if row.get('severity') in {'critical', 'high'}]
    stale = []
    for issue in observed.get('issues', []):
        try:
            if time.time() - datetime.fromisoformat(issue['updated_at'].replace('Z', '+00:00')).timestamp() > 30 * 86400:
                stale.append(issue)
        except (ValueError, KeyError, TypeError):
            pass
    suggestions = (['Inspect the observed failed workflows; tests were not run by JARVIS.'] if failed else [])
    if critical:
        suggestions.append('Review the observed security alerts before prioritizing feature work.')
    if not suggestions:
        suggestions = ['Review assigned issues or open pull requests; this is a heuristic suggestion, not a code review.']
    result = {'repository': repository, 'account_id': account['id'], 'at': time.time(),
        'facts': observed, 'unavailable': unavailable, 'partial_sections': partial,
        'failed_builds': failed, 'critical_alerts': critical,
        'active_bugs': [row for row in observed.get('issues', []) if any('bug' == label.casefold() for label in row.get('labels', []))],
        'assigned_to_me': [row for row in observed.get('issues', []) if account['login'] in row.get('assignees', [])],
        'review_requests': [row for row in observed.get('pulls', []) if account['login'] in row.get('review_requested', [])],
        'stale_issues_30_days': stale,
        'status': 'attention' if failed or critical or any(d.get('verified_status') in {'failure', 'error'} for d in observed.get('deployments', [])) else 'partial' if unavailable or partial else 'observed_no_failure',
        'suggestions_heuristic_not_tested': suggestions, 'code_reviewed': False, 'tests_run': False,
        'local_remote_comparison': 'not_configured_use_existing_github_git_status',
        'seconds': round(time.perf_counter() - started, 6), 'revision': revision}
    fingerprint = digest(observed)
    result['fingerprint'] = fingerprint
    with rt.side_effect('guardian_health_checkpoint', context):
        watch_current(rt, repository, revision, context)
        rt.put('guardian_health', repository, result)
        rt.delete('guardian_error', repository)
        changed = old.get('fingerprint') is not None and old['fingerprint'] != fingerprint
        important = result['status'] == 'attention'
        if (changed or (important and not old)) and fingerprint != old.get('fingerprint'):
            event_id = digest([revision, fingerprint])
            # One stable aggregate event; reconnect never re-announces baseline.
            event = {'id': event_id, 'repository': repository, 'at': result['at'], 'severity': 'attention' if important else 'routine'}
            rt.put('guardian_event', event_id, event)
            if result['at'] - old.get('last_notice_at', 0) >= 900:
                from core.local_notifications import emit
                emit('guardian-' + digest(repository)[:12], 'needs_attention' if important else 'progress',
                    'A monitored project needs attention. Open GitHub Command Center.' if important else
                    'A monitored project changed. Open GitHub Command Center for verified facts.',
                    rt=rt, sequence=event_id)
                result['last_notice_at'] = result['at']
            else:
                result['last_notice_at'] = old.get('last_notice_at', 0)
            rt.put('guardian_health', repository, result)
        else:
            result['last_notice_at'] = old.get('last_notice_at', 0)
            rt.put('guardian_health', repository, result)
    rt.trace('performance_span', context, phase='guardian_repository_read', seconds=result['seconds'],
             api_sections=len(observed), unavailable_sections=len(unavailable))
    return result


def poll(repository, client, rt, context, account):
    revision = rt.get('guardian_watch', repository, {}).get('revision')
    try:
        return _poll(repository, client, rt, context, account)
    except InterruptedError:
        raise
    except Exception as exc:
        with rt.side_effect('guardian_read_failure', context):
            watch_current(rt, repository, revision, context)
            rt.put('guardian_error', repository, {'at': time.time(), 'error': text(exc, 500),
                                                'last_success_retained_not_current': True})
        raise


def snapshot(rt):
    with rt.db() as db:
        events = [json.loads(row[0]) for row in db.execute(
            'SELECT data FROM records WHERE kind=? ORDER BY rowid DESC LIMIT 30', ('guardian_event',)).fetchall()]
    return {'identity': rt.get('guardian', 'connection', {'connected': False, 'state': 'not_checked'}),
        'settings': rt.get('guardian', 'settings', {'paused': False}),
        'watched': rt.records('guardian_watch'), 'health': rt.records('guardian_health'),
        'read_failures': rt.records('guardian_error'),
        'recent_events': events, 'event_window': 'Latest 30 observed changes, not exhaustive GitHub history or a daily digest',
        'monitoring_requires': 'JARVIS running, PC awake, network and authenticated gh available; no offline hosted monitor',
        'coding': 'Phase 6A is read-only; drafting/provider selection is not enabled by Guardian',
        'external_notifications': 'not connected by Guardian; existing local routes only',
        'codex': 'existing sandbox gate unchanged; Guardian does not execute Codex'}


def handle(parameters, rt, context):
    rt.check_cancelled(context)
    action = parameters.get('action')
    if action not in ACTIONS:
        raise ValueError('Unknown Guardian action')
    if action in {'guardian_status', 'guardian_health', 'guardian_broken'}:
        view = snapshot(rt)
        if action == 'guardian_broken':
            return {'observed_failed_builds': {repo: {'builds': report.get('failed_builds', []), 'at': report.get('at'),
                    'monitoring_enabled': view['watched'].get(repo, {}).get('enabled', False)} for repo, report in view['health'].items()},
                    'read_failures': view['read_failures'], 'fresh_network_check': False}
        return view
    if action in {'guardian_watch', 'guardian_unwatch', 'guardian_pause', 'guardian_resume'}:
        request = command(context.get('user_text', ''))
        expected = {'action': action}
        if action.endswith(('watch', 'unwatch')):
            expected['repository'] = slug(parameters.get('repository'))
        if context.get('lifecycle') == 'job' or context.get('input_final') is False or request != expected:
            raise InterruptedError('A fresh explicit Guardian selection/pause command is required; repository text or old context cannot approve it')
        if action in {'guardian_pause', 'guardian_resume', 'guardian_unwatch'}:
            with rt.side_effect('guardian_selection', context):
                if action == 'guardian_unwatch':
                    repository = expected['repository']
                    rt.update('guardian_watch', repository, {'enabled': False, 'revision': uuid.uuid4().hex})
                    rt.update('trigger', 'guardian:' + repository, {'enabled': False})
                else:
                    rt.put('guardian', 'settings', {'paused': action == 'guardian_pause'})
            return snapshot(rt)
    if action == 'guardian_check' and not any(row.get('enabled') for row in rt.records('guardian_watch').values()):
        return {'health': {}, 'message': 'No repositories selected. Discover first, then explicitly watch github owner/repository.', 'network_requests': 0}
    if action == 'guardian_poll':
        selection = rt.get('guardian_watch', slug(parameters.get('repository')), {})
        if not selection.get('enabled') or parameters.get('watch_revision') != selection.get('revision'):
            raise InterruptedError('Scheduled read belongs to a removed or replaced selection; no API request sent')
    try:
        client = GhClient(rt, context)
    except Exception as exc:
        rt.put('guardian', 'connection', {'connected': False, 'checked_at': time.time(), 'error': text(exc, 500)})
        raise
    account = identify(client, rt, context)
    if action == 'guardian_connection':
        return account
    if action in {'guardian_discover', 'guardian_next'}:
        return discover(client, rt, account, action == 'guardian_next')
    if action == 'guardian_watch':
        repository = slug(parameters.get('repository'))
        _, _, body = client.get('repos/' + repository)
        meta = repository_meta(body)
        if meta['repository'] != repository:
            raise ValueError('Repository redirected/renamed; select the exact returned repository explicitly')
        with rt.side_effect('guardian_selection', context):
            existing = rt.get('guardian_watch', repository, {})
            if existing.get('enabled') and existing.get('account_id') == account['id']:
                return {'already_watched': repository, 'existing_checkpoint_preserved': True}
            if sum(1 for row in rt.records('guardian_watch').values() if row.get('enabled')) >= 10 and not existing.get('enabled'):
                raise ValueError('Guardian is bounded to ten selected repositories; remove one before adding another')
            selected = {'enabled': True, 'revision': uuid.uuid4().hex, 'account_id': account['id'],
                        'repository_id': meta['id'], 'at': time.time(), 'permissions': meta['permissions']}
            rt.put('guardian_watch', repository, selected)
            from plugins.task_manager import register_guardian_interval
            register_guardian_interval(repository, rt)
        return {'watching': repository, 'interval_seconds': 900, 'first_sync': 'scheduled', 'requires': 'JARVIS running', 'remote_writes': False}
    if action in {'guardian_poll', 'guardian_check'}:
        if not _polling.acquire(blocking=False):
            raise ValueError('A Guardian check is already running; no duplicate check started')
        try:
            selected = [slug(parameters.get('repository'))] if action == 'guardian_poll' else [repo for repo, row in rt.records('guardian_watch').items() if row.get('enabled')]
            if len(selected) > 10:
                raise ValueError('At most ten repositories per manual check; use the individually scheduled checks')
            return {'health': {repo: poll(repo, client, rt, context, account) for repo in selected}, 'tests_executed': False}
        finally:
            _polling.release()
    raise ValueError('Unsupported Guardian request')
