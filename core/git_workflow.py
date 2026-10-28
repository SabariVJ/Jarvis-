"""Git CLI workflow: reviewed snapshots, isolated checks, immutable push approval."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse

from core.automation_runtime import get_runtime
from core.process_runner import run_process
from core.git_security import allowed_path, file_bytes, inspect_bytes, redact

_locks, _locks_guard = {}, threading.Lock()


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True).encode()
    return hashlib.sha256(value).hexdigest()


def normalized(text):
    return ' '.join(re.findall(r'[a-z0-9]+', str(text).casefold()))


def github_url(url):
    if url.startswith('git@github.com:'):
        path = url[len('git@github.com:'):]
    else:
        parsed = urlparse(url)
        if (parsed.hostname != 'github.com' or parsed.query or parsed.fragment or parsed.password or parsed.port
                or parsed.scheme not in {'https', 'ssh'} or parsed.username not in (None, 'git')
                or (parsed.scheme == 'https' and parsed.username)):
            raise ValueError('Use a credential-free HTTPS or git@github.com SSH repository URL')
        path = parsed.path.lstrip('/')
    path = path.removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', path) or '..' in path:
        raise ValueError('Provide an exact GitHub owner/repository destination')
    return path


class GitWorkflow:
    def __init__(self, project, context):
        self.root = Path(project).resolve(strict=True)
        self.rt, self.context = get_runtime(), dict(context)
        self.rt.check_cancelled(self.context)
        if self.context.get('lifecycle') == 'job':
            raise ValueError('Git publication cannot run from a replayable/background recipe')
        with _locks_guard:
            self.lock = _locks.setdefault(str(self.root).casefold(), threading.RLock())
        self.env = {k: v for k, v in os.environ.items() if k not in {
            'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
            'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_ASKPASS', 'SSH_ASKPASS'}
            and not k.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}
        self.env.update(GIT_LITERAL_PATHSPECS='1', GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never',
                        PYTHONIOENCODING='utf-8', QT_QPA_PLATFORM='offscreen')
        result = self.git('rev-parse', '--show-toplevel', check=False)
        if result.returncode:
            raise ValueError('This folder is not a Git repository. Choose an existing clone, or explicitly initialize it '
                             'with git init -b BRANCH and configure git remote add origin GITHUB_URL. No repository was created.')
        if Path(result.stdout.strip()).resolve() != self.root:
            raise ValueError('The selected project must be the exact Git worktree root')

    def git(self, *args, check=True, timeout=40, binary=False):
        result = run_process(['git', '-c', 'core.quotePath=false', '-c', 'http.followRedirects=false', '-C', str(self.root), *args], cwd=self.root,
                             context=self.context, timeout=timeout, env=self.env,
                             max_output_bytes=8 * 1024 * 1024, allow_truncated=False, binary_output=binary)
        if check and result.returncode:
            raise ValueError('Git command failed: ' + redact(result.stdout))
        return result

    def authority(self, action):
        self.rt.check_cancelled(self.context)
        text = self.context.get('user_text', '').casefold().strip()
        if action == 'commit' and not re.match(r'^(?:(?:hey )?jarvis[, ]+)?(?:please )?(?:git )?commit\b', text):
            raise ValueError('A fresh explicit commit command is required')

    def head(self):
        result = self.git('rev-parse', '--verify', 'HEAD', check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def branch(self):
        result = self.git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
        if result.returncode:
            raise ValueError('Detached HEAD cannot be committed/pushed by this workflow')
        return result.stdout.strip()

    def index_changes(self):
        return [name for name in self.git('diff', '--cached', '--name-only', '-z').stdout.split('\0') if name]

    def safe_config(self, files):
        # Git for Windows registers LFS globally. It must not block ordinary
        # source files that don't use a filter. Check active worktree AND index
        # attributes before any hash/export that could invoke one.
        for start in range(0, len(files), 50):
            chunk = files[start:start + 50]
            for cached in ([], ['--cached']):
                values = self.git('check-attr', *cached, '-z', 'filter', '--', *chunk).stdout.split('\0')
                if values and values[-1] == '':
                    values.pop()
                if len(values) % 3:
                    raise ValueError('Git attribute response was incomplete')
                for offset in range(0, len(values), 3):
                    if values[offset + 2] not in {'unspecified', 'unset'}:
                        raise ValueError('Active Git content filters require manual review: ' + values[offset])

    def status(self):
        return {'project': str(self.root), 'branch': self.branch(), 'commit': self.head(),
                'status': self.git('status', '--short', '--branch').stdout, 'pushed': False}

    def branches(self):
        current = self.git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
        return {'current_branch': current.stdout.strip() if not current.returncode else None,
                'detached_head': bool(current.returncode),
                'branches': self.git('branch', '--list', '--no-color').stdout}

    def remotes(self):
        # Redact userinfo/tokens even in malformed legacy remote configuration.
        text = self.git('remote', '-v').stdout
        text = re.sub(r'([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@', r'\1[redacted]@', text)
        text = re.sub(r'([a-zA-Z][a-zA-Z0-9+.-]*://[^\s?#]+)[?#][^\s]*', r'\1[redacted-query]', text)
        return {'remotes': redact(text)}

    def log(self):
        if not self.head():
            return {'commits': [], 'note': 'Repository has no commits yet'}
        return {'commits': redact(self.git('log', '-10', '--format=%h %s').stdout)}

    def connectivity(self, remote='origin'):
        data = {'git_tool_enabled': True, 'local_repository': True, 'project': str(self.root),
                **self.branches(), **self.remotes(), 'remote_name': remote,
                'remote_read_access_verified': False, 'authentication': 'not_verified',
                'push_permission': 'not_verified', 'github_api': 'not_tested_by_git_cli',
                'browser_login': 'not_inspected_not_evidence_of_git_authentication'}
        try:
            url, repository = self.destination(remote)
            data.update(remote_url=url, repository=repository)
            # Read-only network probe. Existing destination validation rejects
            # URL rewrites/custom SSH commands; credentials remain with Git/GCM.
            branch = data['current_branch'] or 'HEAD'
            data['remote_branch_commit'] = self.remote_head(url, branch)
            data['remote_read_access_verified'] = True
            data['note'] = ('Remote read access verified; public repositories may allow anonymous reads. '
                            'This does not verify credential identity or permission to push.')
        except ValueError as exc:
            data['failure'] = redact(str(exc))
        return data

    def review(self, files):
        if not isinstance(files, list) or not 1 <= len(files) <= 100 or len(files) != len(set(files)):
            raise ValueError('Select 1–100 explicit, unique project-relative files; no wildcard staging')
        self.safe_config(files)
        if self.index_changes():
            raise ValueError('The Git index already contains staged changes. Review it manually first; it was not changed')
        manifest, differences, total = [], [], 0
        for name in files:
            allowed_path(name)
            data = file_bytes(self.root, name)
            total += len(data or b'')
            if total > 20 * 1024 * 1024:
                raise ValueError('Review exceeds the bounded 20 MiB file-content limit')
            tracked = self.git('ls-files', '--error-unmatch', '--', name, check=False).returncode == 0
            if data is None and not tracked:
                raise ValueError(f'File is neither present nor tracked: {name}')
            if tracked and self.head():
                old = self.git('show', 'HEAD:' + name, check=False, binary=True)
                if old.returncode == 0:
                    inspect_bytes(name, old.stdout)
            oid = self.git('hash-object', '--path=' + name, '--', name).stdout.strip() if data is not None else None
            mode = self.git('ls-files', '--stage', '-z', '--', name).stdout.split()[0] if tracked else '100644'
            if mode not in {'100644', '100755'}:
                raise ValueError('Symlinks/submodules cannot be reviewed by this workflow')
            manifest.append({'file': name, 'sha256': digest(data) if data is not None else None, 'blob': oid, 'mode': mode})
            if tracked:
                differences.append(self.git('diff', '--no-ext-diff', '--no-textconv', '--', name).stdout)
            else:
                differences.append('NEW FILE ' + name + '\n' + (data or b'').decode('utf-8-sig'))
        token = secrets.token_hex(4)
        full_diff = '\n'.join(differences)
        review_dir = self.rt.path.parent / 'git-reviews'
        review_dir.mkdir(exist_ok=True)
        review_file = review_dir / (token + '.diff')
        with review_file.open('x', encoding='utf-8', newline='\n') as handle:
            handle.write(full_diff)
        record = {'root': str(self.root), 'head': self.head(), 'branch': self.branch(), 'manifest': manifest,
                  'digest': digest(manifest), 'created': time.time(), 'state': 'reviewed',
                  'execution_id': self.context.get('execution_id'), 'review_file': str(review_file),
                  'diff_sha256': digest(full_diff.encode('utf-8'))}
        self.rt.put('git_review', token, record)
        return {'review_id': token, 'project': str(self.root), 'branch': record['branch'], 'changes': manifest,
                'changes_digest': record['digest'], 'diff': full_diff[:12000], 'diff_preview_truncated': len(full_diff) > 12000,
                'review_file': str(review_file), 'full_diff_sha256': record['diff_sha256'],
                'confirmation_required': 'Stage review ' + token, 'staged': False}

    def diff(self, files=None):
        if files:
            return self.review(files)
        tracked = self.git('diff', '--name-only', '-z', 'HEAD', check=False) if self.head() else None
        names = (tracked.stdout.split('\0') if tracked and tracked.returncode == 0 else [])
        names += self.git('ls-files', '--others', '--exclude-standard', '-z').stdout.split('\0')
        safe, excluded = [], []
        for name in dict.fromkeys(n for n in names if n):
            try:
                allowed_path(name)
                safe.append(name)
            except ValueError:
                excluded.append(name)
        if not safe:
            return {'changes': [], 'excluded_paths': excluded, 'staged': False}
        result = self.review(safe)
        result['excluded_paths'] = excluded
        return result

    def load_review(self, token):
        record = self.rt.get('git_review', token)
        if not record or record['root'] != str(self.root) or record['branch'] != self.branch() or record['head'] != self.head():
            raise ValueError('Review belongs to another project/branch/HEAD, or is no longer current')
        if time.time() - record['created'] > 3600:
            raise ValueError('Review expired; inspect the changes again')
        return record

    def require_phrase(self, phrase, previous_execution):
        text = normalized(self.context.get('user_text', ''))
        text = re.sub(r'^(?:hey )?jarvis ', '', text)
        text = re.sub(r'^please ', '', text)
        if text != normalized(phrase) or previous_execution == self.context.get('execution_id'):
            raise ValueError('Fresh user confirmation required: ' + phrase)

    def index_entries(self, with_modes=False):
        entries = []
        for row in self.git('ls-files', '--stage', '-z').stdout.split('\0'):
            if row:
                metadata, name = row.split('\t', 1)
                mode, oid, stage = metadata.split()
                if stage != '0' or mode not in {'100644', '100755'}:
                    raise ValueError('Unmerged entries, symlinks and submodules require manual Git review')
                entries.append((name, (mode, oid) if with_modes else oid))
        return entries

    def validate_staged(self, record):
        wanted = {x['file']: x['blob'] for x in record['manifest']}
        changed = set(self.index_changes())
        if changed != set(wanted):
            raise ValueError('Staged paths do not exactly match the reviewed file list')
        entries = dict(self.index_entries(with_modes=True))
        modes = {item['file']: item['mode'] for item in record['manifest']}
        for name, oid in wanted.items():
            if entries.get(name) != ((modes[name], oid) if oid is not None else None):
                raise ValueError('Staged content changed since review: ' + name)

    def stage(self, token):
        record = self.load_review(token)
        self.safe_config([item['file'] for item in record['manifest']])
        self.require_phrase('Stage review ' + token, record['execution_id'])
        review_file = Path(record['review_file'])
        if not review_file.is_file() or digest(review_file.read_bytes()) != record['diff_sha256']:
            raise ValueError('The full review artifact changed or disappeared; create a fresh review')
        if record['state'] != 'reviewed' or self.index_changes():
            raise ValueError('Review was already used or the index changed; nothing was staged')
        for item in record['manifest']:
            data = file_bytes(self.root, item['file'])
            if (digest(data) if data is not None else None) != item['sha256']:
                raise ValueError('File changed after review: ' + item['file'])
        self.rt.update('git_review', token, {'state': 'staging'}, expected={'state': 'reviewed'})
        # Write immutable approved objects, then update only those cache entries.
        # Unlike `git add`, the final index update never rereads mutable files.
        # A race can leave an unreferenced object, never an unreviewed staged blob.
        for item in record['manifest']:
            if item['blob'] is None:
                self.git('update-index', '--force-remove', '--', item['file'])
            else:
                oid = self.git('hash-object', '-w', '--path=' + item['file'], '--', item['file']).stdout.strip()
                if oid != item['blob']:
                    raise ValueError('File changed while staging; no unreviewed blob was added to the index')
                inspect_bytes(item['file'], self.git('cat-file', 'blob', oid, binary=True).stdout)
                self.git('update-index', '--add', '--cacheinfo', item['mode'] + ',' + oid + ',' + item['file'])
        self.validate_staged(record)
        self.scan_index()
        self.rt.update('git_review', token, {'state': 'staged'})
        return {'review_id': token, 'staged_changes': record['manifest'], 'changes_digest': record['digest']}

    def scan_index(self):
        entries = self.index_entries()
        self.safe_config([name for name, _ in entries])
        if len(entries) > 1000:
            raise ValueError('Index exceeds the bounded 1000-file security review')
        total = 0
        for name, oid in entries:
            allowed_path(name)
            content = self.git('cat-file', 'blob', oid, binary=True).stdout
            total += len(content)
            if total > 20 * 1024 * 1024:
                raise ValueError('Index exceeds the 20 MiB security review limit')
            inspect_bytes(name, content)
        self.git('-c', 'core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
                 'diff', '--cached', '--check')
        return self.git('write-tree').stdout.strip()

    def checks(self, token, test_kind='unittest'):
        record = self.load_review(token)
        if record['state'] not in {'staged', 'checked'}:
            raise ValueError('Stage an explicitly approved review first')
        self.validate_staged(record)
        tree = self.scan_index()
        if test_kind not in {'unittest', 'pytest'}:
            raise ValueError('Choose unittest or pytest')
        with tempfile.TemporaryDirectory(prefix='jarvis-git-checks-') as folder:
            snapshot = Path(folder)
            self.git('checkout-index', '--all', '--prefix=' + str(snapshot) + os.sep)
            config = snapshot / 'config'
            if (snapshot / 'memory' / 'config_manager.py').exists():
                config.mkdir(exist_ok=True)
                (config / 'api_keys.json').write_text('{}', encoding='utf-8')
            tests = snapshot / 'tests'
            if not tests.is_dir() or not list(tests.glob('test*.py')):
                raise ValueError('No tests available in the staged snapshot; commit is blocked until tests are supplied')
            command = [sys.executable, '-B', '-m', test_kind] + (['-q'] if test_kind == 'pytest' else ['discover', '-s', 'tests', '-v'])
            test_env = {k: v for k, v in self.env.items() if not re.search(
                r'TOKEN|SECRET|PASSWORD|API_?KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL|SSH_AUTH_SOCK', k, re.I)}
            result = run_process(command, cwd=snapshot, context=self.context, env=test_env, timeout=300,
                                 max_output_bytes=2 * 1024 * 1024, allow_truncated=False)
            if result.returncode or 'Ran 0 tests' in result.stdout:
                raise ValueError('Staged-snapshot tests failed; no commit was made. ' + redact(result.stdout))
            suites = [test_kind]
            if (snapshot / 'providers' / '_tests.py').exists():
                extra = run_process([sys.executable, '-B', '-m', 'providers._tests'], cwd=snapshot,
                                    context=self.context, env=test_env, timeout=180,
                                    max_output_bytes=2 * 1024 * 1024, allow_truncated=False)
                if extra.returncode:
                    raise ValueError('Provider checks failed in the staged snapshot; no commit was made')
                suites.append('providers._tests')
        if tree != self.git('write-tree').stdout.strip():
            raise ValueError('The index changed while tests ran; checks must be repeated')
        self.rt.update('git_review', token, {'state': 'checked', 'checked_tree': tree, 'checked_at': time.time()})
        return {'review_id': token, 'tree': tree, 'security_checks': 'passed', 'test_suites': suites, 'tests': 'passed'}

    def commit(self, token, message, test_kind='unittest'):
        self.authority('commit')
        if not str(message).strip() or len(message) > 500 or '\0' in message:
            raise ValueError('Provide a commit message of 1–500 characters')
        inspect_bytes('commit-message.txt', str(message).encode('utf-8'))
        git_dir = Path(self.git('rev-parse', '--absolute-git-dir').stdout.strip())
        if any((git_dir / flag).exists() for flag in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer')):
            raise ValueError('Finish the in-progress merge/rebase/cherry-pick manually before using automated commits')
        self.checks(token, test_kind)  # Mandatory fresh checks of the exact staged tree.
        record = self.load_review(token)
        self.validate_staged(record)
        self.rt.check_cancelled(self.context)
        self.rt.update('git_review', token, {'state': 'committing'}, expected={'state': 'checked'})
        self.git('commit', '-m', message, timeout=60)
        commit = self.head()
        parents = self.git('rev-list', '--parents', '-n', '1', 'HEAD').stdout.split()[1:]
        if parents != ([record['head']] if record['head'] else []):
            raise ValueError(f'Commit {commit} exists but its parent history changed; push is blocked for manual inspection')
        if self.git('rev-parse', 'HEAD^{tree}').stdout.strip() != record['checked_tree']:
            raise ValueError(f'Commit {commit} exists but a hook/concurrent operation changed its tree; push is blocked for manual inspection')
        managed = {**record, 'commit': commit, 'base': record['head'], 'review_id': token,
                   'branch': self.branch(), 'state': 'committed'}
        self.rt.put('git_commit', commit, managed)
        self.rt.update('git_review', token, {'state': 'committed', 'commit': commit})
        return {'commit': commit, 'branch': managed['branch'], 'remote': None, 'push_outcome': 'not_requested'}

    def destination(self, remote):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', remote):
            raise ValueError('Provide an existing remote name such as origin')
        if self.git('config', '--get-regexp', r'^url\..*\.(insteadof|pushinsteadof)$', check=False).returncode == 0:
            raise ValueError('Git URL rewriting requires manual review before publishing')
        if self.git('config', '--get-regexp', r'^(push\.pushoption|remote\..*\.(mirror|receivepack))$', check=False).returncode == 0:
            raise ValueError('Mirror/custom receive-pack/push-option configuration requires manual review')
        if self.git('config', '--get', 'core.sshCommand', check=False).returncode == 0 or any(os.environ.get(k) for k in ('GIT_SSH', 'GIT_SSH_COMMAND')):
            raise ValueError('Custom SSH commands require manual review; use the normal SSH agent or Git Credential Manager')
        urls = self.git('remote', 'get-url', '--push', '--all', remote).stdout.splitlines()
        if len(urls) != 1:
            raise ValueError('Exactly one push destination is required')
        repository = github_url(urls[0])
        return urls[0], repository

    def remote_head(self, url, branch):
        result = self.git('ls-remote', '--heads', url, 'refs/heads/' + branch, check=False, timeout=45)
        if result.returncode:
            raise ValueError('Remote access failed: ' + redact(result.stdout) + '\nSetup: run git credential-manager github login '
                             'for HTTPS, or ssh -T git@github.com for SSH. Confirm repository access, write permission and organization SSO authorization.')
        rows = [r.split() for r in result.stdout.splitlines() if r.strip()]
        if any(len(row) != 2 or row[1] != 'refs/heads/' + branch for row in rows) or len(rows) > 1:
            raise ValueError('Remote branch response was ambiguous')
        if rows and not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', rows[0][0]):
            raise ValueError('Remote did not return a valid commit object ID')
        return rows[0][0] if rows else None

    def prepare_push(self, remote='origin'):
        head, branch = self.head(), self.branch()
        managed = self.rt.get('git_commit', head or '')
        if not managed or managed['root'] != str(self.root) or managed['branch'] != branch:
            raise ValueError('Only a commit created from this reviewed/tested workflow can be prepared for push')
        if self.index_changes():
            raise ValueError('The index has additional staged changes; review/commit those separately first')
        url, repository = self.destination(remote)
        observed = self.remote_head(url, branch)
        if observed not in {managed['base'], head}:
            raise ValueError('Remote is not the reviewed commit base; diverged/additional history needs manual review. No force push is available')
        token = secrets.token_hex(4)
        phrase = f'Confirm push {repository} branch {branch} review {token}'
        record = {'root': str(self.root), 'remote': remote, 'url': url, 'repository': repository, 'branch': branch,
                  'commit': head, 'remote_before': observed, 'changes': managed['manifest'], 'changes_digest': managed['digest'],
                  'phrase': phrase, 'state': 'pending', 'created': time.time(), 'execution_id': self.context.get('execution_id')}
        self.rt.put('git_push', token, record)
        return {**{k: v for k, v in record.items() if k not in {'execution_id', 'state'}}, 'approval_id': token,
                'confirmation_required': phrase, 'pushed': False}

    def push(self, token):
        record = self.rt.get('git_push', token)
        if not record or record['root'] != str(self.root) or record['state'] != 'pending' or time.time() - record['created'] > 600:
            raise ValueError('Push approval missing, consumed or expired; prepare a fresh review')
        self.require_phrase(record['phrase'], record['execution_id'])
        if self.head() != record['commit'] or self.branch() != record['branch'] or self.index_changes():
            raise ValueError('Commit, branch or staged changes changed after confirmation was prepared')
        url, repository = self.destination(record['remote'])
        if url != record['url'] or repository != record['repository']:
            raise ValueError('Push destination changed after review')
        before = self.remote_head(url, record['branch'])
        if before != record['remote_before']:
            raise ValueError('Remote changed after review; prepare and confirm a fresh push')
        self.rt.update('git_push', token, {'state': 'consumed'}, expected={'state': 'pending'})
        # Explicit immutable source and one branch. No force, mirror, deletion,
        # tags, implicit configured refspec, or branch-protection bypass.
        result = self.git('-c', 'push.followTags=false', '-c', f'remote.{record["remote"]}.mirror=false',
                          'push', '--porcelain', '--no-follow-tags', url,
                          record['commit'] + ':refs/heads/' + record['branch'], check=False, timeout=60)
        if result.returncode:
            raise ValueError('Push rejected or authentication failed; no retry was attempted. ' + redact(result.stdout) +
                             ' Use git credential-manager github login for HTTPS and confirm write/SSO permissions. '
                             'For protected branches use the repository-required pull request workflow.')
        observed = self.remote_head(url, record['branch'])
        verified = observed == record['commit']
        self.rt.update('git_push', token, {'state': 'verified' if verified else 'unverified', 'remote_commit': observed})
        return {'commit': record['commit'], 'branch': record['branch'], 'remote': record['remote'],
                'url': url, 'remote_commit': observed, 'push_outcome': 'verified' if verified else 'unverified', 'verified': verified}

    def verify(self, remote='origin'):
        url, _ = self.destination(remote)
        branch, head = self.branch(), self.head()
        observed = self.remote_head(url, branch)
        return {'commit': head, 'branch': branch, 'remote': remote, 'url': url, 'remote_commit': observed,
                'verified': bool(head and observed == head), 'push_outcome': 'verified' if head and observed == head else 'not_verified'}
