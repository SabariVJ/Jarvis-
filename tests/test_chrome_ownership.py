"""Mock Windows process/window ownership, never closes an actual application."""
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import appidentity as ai, desktop_ownership as ownership, outcome
from core.command_router import canonical_arguments
from actions import open_app, computer_control
from test_automation import Isolated


class ChromeOwnershipTests(Isolated):
    def setUp(self):
        super().setUp()
        self.chrome, self.internal, self.renderer, self.whatsapp = 44001, 44002, 44003, 44004
        self.parent = 44005
        self.chrome_exe = r'C:\Program Files\Google\Chrome\Application\chrome.exe'
        self.alive = {os.getpid(), self.parent, self.internal, self.renderer}
        self.created = {pid: float(pid) for pid in (os.getpid(), self.parent, self.chrome, self.internal, self.renderer, self.whatsapp)}
        self.commands = {self.chrome: [self.chrome_exe], self.internal: [self.chrome_exe, '--remote-debugging-pipe', '--enable-automation'],
                         self.renderer: [self.chrome_exe, '--type=renderer'], self.whatsapp: ['WhatsApp.exe']}
        self.windows = {22002: self.info(22002, self.internal), 22004: self.info(22004, self.whatsapp, 'ApplicationFrameWindow')}
        self.front = 0
        self.launches, self.posts = [], []
        self.block_close = False
        self.fail_post = False
        self.processes = {}
        import psutil
        self.fake_psutil = SimpleNamespace(Process=self.process, process_iter=self.process_iter,
                                          pids=lambda: list(self.alive), NoSuchProcess=psutil.NoSuchProcess,
                                          AccessDenied=psutil.AccessDenied, STATUS_ZOMBIE='zombie')
        self.patches = [patch.object(ai, 'psutil', self.fake_psutil), patch.object(ai, '_PSUTIL', True),
            patch.object(ai, '_OS', 'Windows'), patch.object(ai, '_OWN_EXES', None),
            patch.object(ai, '_chain_pids', return_value={os.getpid(), self.parent, self.chrome, self.internal, self.renderer}),
            patch.object(ai, '_hwnds_for', side_effect=lambda pid: [h for h, w in self.windows.items() if w['pid'] == pid and w['visible']]),
            patch.object(ai, '_windows_by_pid', side_effect=lambda: {p: ['Browser'] for p in self.alive}),
            patch.object(ai, 'focus', side_effect=self.focus), patch.object(ai, '_post_close_windows', side_effect=self.close_other),
            patch.object(ownership, 'window_info', side_effect=lambda hwnd: dict(self.windows[hwnd]) if hwnd in self.windows else None),
            patch.object(ownership, 'foreground', side_effect=lambda: self.front),
            patch.object(ownership, '_mark_window', side_effect=self.mark),
            patch.object(ownership, '_post_window_close', side_effect=self.post),
            patch.object(open_app, '_SYSTEM', 'Windows'), patch.dict(open_app._LAUNCHERS, {'Windows': self.launch})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def info(self, hwnd, pid, kind='Chrome_WidgetWin_1'):
        return {'hwnd': hwnd, 'pid': pid, 'thread_id': pid + 1, 'class': kind, 'visible': True, 'nonce': 0}

    def process(self, pid):
        if pid not in self.alive:
            import psutil
            raise psutil.NoSuchProcess(pid)
        if pid not in self.processes:
            name = 'chrome.exe' if pid in {self.chrome, self.internal, self.renderer} else (
                'WhatsApp.exe' if pid == self.whatsapp else 'python.exe' if pid == os.getpid() else 'powershell.exe')
            exe = self.chrome_exe if name == 'chrome.exe' else sys.executable if pid == os.getpid() else name
            self.processes[pid] = SimpleNamespace(pid=pid, info={'pid': pid, 'name': name, 'exe': exe},
                exe=lambda p=pid, x=exe: x, name=lambda n=name: n,
                cmdline=lambda p=pid, x=exe: self.commands.get(p, [x]),
                create_time=lambda p=pid: self.created[p],
                parents=lambda: [self.process(self.parent)], is_running=lambda p=pid: p in self.alive,
                status=lambda: 'running', terminate=Mock(side_effect=AssertionError('Must never terminate a process')),
                kill=Mock(side_effect=AssertionError('Must never kill a process')))
        return self.processes[pid]

    def process_iter(self, *_):
        return [self.process(pid) for pid in sorted(self.alive)]

    def focus(self, match):
        handles = [h for h, w in self.windows.items() if w['pid'] == match.pid and w['visible']]
        if handles:
            self.front = handles[0]
        return bool(handles)

    def mark(self, hwnd, nonce):
        self.windows[hwnd]['nonce'] = nonce

    def launch(self, name):
        chrome = ownership.is_chrome(name)
        self.launches.append('Chrome' if chrome else 'WhatsApp')
        pid, hwnd = (self.chrome, 22001) if chrome else (self.whatsapp, 22004)
        self.alive.add(pid)
        self.windows[hwnd] = self.info(hwnd, pid, 'Chrome_WidgetWin_1' if chrome else 'ApplicationFrameWindow')
        self.front = hwnd
        return True

    def post(self, hwnd):
        self.posts.append(hwnd)
        if self.fail_post:
            return False
        if not self.block_close:
            self.windows.pop(hwnd, None)
        return True

    def close_other(self, pid):
        self.assertEqual(pid, self.whatsapp)
        for hwnd in [h for h, w in self.windows.items() if w['pid'] == pid]:
            self.windows.pop(hwnd)
        self.alive.discard(pid)
        return 1

    def command(self, verb, app):
        context = self.runtime.new_turn(f'{verb} {app}')
        if verb == 'Open':
            name, args = 'open_app', {'app_name': app}
            invoke = lambda: open_app.open_app(args)
        else:
            name, args = 'computer_control', {'action': 'close_app', 'app_name': app}
            invoke = lambda: computer_control.computer_control(args)
        result = self.runtime.execute(name, args, invoke, context)
        self.runtime.finish(context)
        return result

    def test_open_chrome_close_chrome_keeps_helpers_alive(self):
        self.assertIn('status=done', self.command('Open', 'Chrome'))
        self.assertIn(self.chrome, ai._chain_pids())
        self.assertIn(os.path.normcase(self.chrome_exe), ai.own_exes())
        self.assertIn(self.chrome, ai.own_pids())  # Process-kill guard stays conservative.
        matches = ai.running('Chrome')
        self.assertEqual([m.pid for m in matches], [self.chrome])
        self.assertFalse(matches[0].is_self)
        result = self.command('Close', 'Chrome')
        self.assertIn('status=done', result)
        self.assertIn('windows_closed=1', result)
        self.assertEqual(self.posts, [22001])
        self.assertIn(self.chrome, self.alive)  # Background browser process needn't exit.
        self.assertIn(self.internal, self.alive)
        self.assertIn(self.renderer, self.alive)
        self.assertIn(22002, self.windows)
        self.assertIn(os.getpid(), self.alive)
        for proc in self.processes.values():
            proc.terminate.assert_not_called()
            proc.kill.assert_not_called()

    def test_open_close_chrome_then_open_whatsapp(self):
        results = [self.command('Open', 'Chrome'), self.command('Close', 'Chrome'), self.command('Open', 'WhatsApp')]
        self.assertTrue(all(r.startswith('status=done ') for r in results), results)
        self.assertEqual(self.launches, ['Chrome', 'WhatsApp'])
        self.assertEqual(self.posts, [22001])

    def test_whatsapp_open_close_then_chrome_open_close(self):
        results = [self.command('Open', 'WhatsApp'), self.command('Close', 'WhatsApp'),
                   self.command('Open', 'Chrome'), self.command('Close', 'Chrome')]
        self.assertTrue(all(r.startswith('status=done ') for r in results), results)
        self.assertEqual(self.launches, ['WhatsApp', 'Chrome'])
        self.assertEqual(self.posts, [22001])

    def test_failed_close_does_not_claim_success(self):
        self.command('Open', 'Chrome')
        self.fail_post = True
        result = self.command('Close', 'Chrome')
        self.assertIn('status=unverified', result)
        self.assertIn(22001, self.windows)

    def test_window_remaining_after_request_is_unverified(self):
        self.command('Open', 'Chrome')
        self.block_close = True
        context = self.runtime.new_turn('Close Chrome')
        candidates = ai.running('Chrome', include_self=True)
        match = next(m for m in candidates if m.pid == self.chrome)
        from dataclasses import replace
        target = replace(match, window_identity=ownership.registered_windows(self.chrome)[0][0], window_context=context)
        gone, reason = ai.request_close(target, timeout=0)
        self.assertFalse(gone)
        self.assertIn('still present', reason)

    def test_only_one_window_is_closed(self):
        self.command('Open', 'Chrome')
        self.windows[22003] = self.info(22003, self.chrome)
        match = next(m for m in ai.running('Chrome', include_self=True) if m.pid == self.chrome)
        self.runtime.new_turn('Open Chrome')
        self.front = 22003
        self.assertTrue(ownership.record_open(match, 'Chrome'))
        self.assertIn('status=done', self.command('Close', 'Chrome'))
        self.assertEqual(self.posts, [22003])
        self.assertIn(22001, self.windows)

    def test_ambiguous_windows_ask_instead_of_closing_all(self):
        self.command('Open', 'Chrome')
        self.windows[22003] = self.info(22003, self.chrome)
        match = next(m for m in ai.running('Chrome', include_self=True) if m.pid == self.chrome)
        self.runtime.new_turn('Open Chrome')
        self.front = 0
        ownership.record_open(match, 'Chrome')
        result = self.command('Close', 'Chrome')
        self.assertIn('status=ask', result)
        self.assertEqual(self.posts, [])

    def test_internal_browser_cannot_gain_window_permission(self):
        self.runtime.new_turn('Open Chrome')
        internal = ai.Match(self.internal, 'chrome.exe', 100, True)
        self.assertFalse(ownership.record_open(internal, 'Chrome'))
        self.assertFalse(ownership.can_open(internal, 'Chrome'))
        self.assertFalse(ai.request_close(internal, timeout=0)[0])
        self.assertEqual(self.posts, [])

    def test_python_and_model_runtime_never_gain_chrome_permission(self):
        self.runtime.new_turn('Open Chrome')
        self.assertFalse(ownership.record_open(ai.Match(os.getpid(), 'python.exe', 100, True), 'Chrome'))
        model = ai.Match(self.parent, 'ollama.exe', 100, True)
        self.assertFalse(ownership.can_open(model, 'Chrome'))
        self.assertEqual(self.posts, [])

    def test_helper_with_no_window_is_never_terminated(self):
        match = ai.Match(self.renderer, 'chrome.exe', 100, True)
        self.assertFalse(ai.request_close(match, timeout=0)[0])
        self.process(self.renderer).terminate.assert_not_called()

    def test_pid_reuse_invalidates_window_permission(self):
        self.command('Open', 'Chrome')
        self.created[self.chrome] += 1
        result = self.command('Close', 'Chrome')
        self.assertIn('status=failed', result)
        self.assertEqual(self.posts, [])

    def test_hwnd_reuse_does_not_inherit_permission(self):
        self.command('Open', 'Chrome')
        self.windows[22001]['nonce'] = 0
        result = self.command('Close', 'Chrome')
        self.assertIn('status=failed', result)
        self.assertEqual(self.posts, [])

    def test_window_inspection_failure_never_counts_as_closed(self):
        self.command('Open', 'Chrome')
        context = self.runtime.new_turn('Close Chrome')
        match = next(m for m in ai.running('Chrome', include_self=True) if m.pid == self.chrome)
        from dataclasses import replace
        target = replace(match, window_identity=ownership.registered_windows(self.chrome)[0][0], window_context=context)
        with patch.object(ownership, 'window_info', side_effect=OSError('access denied')):
            gone, reason = ai.request_close(target, timeout=0)
        self.assertFalse(gone)
        self.assertIn('not verified', reason)
        self.assertEqual(self.posts, [])

    def test_stale_close_command_cannot_post_wm_close(self):
        self.command('Open', 'Chrome')
        old = self.runtime.new_turn('Close Chrome')
        match = next(m for m in ai.running('Chrome', include_self=True) if m.pid == self.chrome)
        from dataclasses import replace
        target = replace(match, window_identity=ownership.registered_windows(self.chrome)[0][0], window_context=old)
        self.runtime.new_turn('Open WhatsApp')
        self.assertFalse(ai.request_close(target, timeout=0)[0])
        self.assertEqual(self.posts, [])

    def test_completed_actions_cannot_replay_and_close_receipt_is_reused(self):
        self.command('Open', 'Chrome')
        context = self.runtime.new_turn('Close Chrome')
        args = {'action': 'close_app', 'app_name': 'Chrome'}
        def invoke():
            return computer_control.computer_control(args)
        first = self.runtime.execute('computer_control', args, invoke, context)
        second = self.runtime.execute('computer_control', args, invoke, {**context, 'request_id': 'new-id'})
        self.assertEqual(first, second)
        self.assertEqual(self.posts, [22001])
        self.runtime.finish(context)
        with self.assertRaises(InterruptedError):
            self.runtime.execute('computer_control', args, invoke, context)
        self.assertEqual(self.launches, ['Chrome'])

    def test_explicit_window_handle_is_part_of_duplicate_identity(self):
        first = canonical_arguments('computer_control', {'action': 'close_app', 'app_name': 'Chrome', 'window_handle': 1})
        second = canonical_arguments('computer_control', {'action': 'close_app', 'app_name': 'Google Chrome', 'window_handle': 2})
        self.assertNotEqual(first, second)

    def test_grant_does_not_bypass_changed_automation_role(self):
        self.command('Open', 'Chrome')
        self.commands[self.chrome].append('--remote-debugging-port=9222')
        self.assertIn('status=failed', self.command('Close', 'Chrome'))
        self.assertEqual(self.posts, [])

    def test_owned_popup_is_not_registered_as_browser_window(self):
        self.alive.add(self.chrome)
        self.windows[22001] = {**self.info(22001, self.chrome), 'owner': 1234}
        self.runtime.new_turn('Open Chrome')
        for label in ('Chrome', 'Notepad'):
            with self.subTest(display_label=label):
                self.assertFalse(ownership.record_open(ai.Match(self.chrome, 'chrome.exe', 100, True), label))
        self.assertEqual(self.posts, [])

    def test_registration_refusal_does_not_relax_process_protection(self):
        self.alive.add(self.chrome)
        self.windows[22001] = self.info(22001, self.chrome)
        self.runtime.new_turn('Open Chrome')
        with patch.object(ownership, '_mark_window', side_effect=OSError('access denied')):
            self.assertFalse(ownership.record_open(ai.Match(self.chrome, 'chrome.exe', 100, True), 'Chrome'))
        self.assertIn('status=failed', self.command('Close', 'Chrome'))
        self.assertEqual(self.posts, [])

    def test_private_profile_with_separate_argument_stays_protected(self):
        self.commands[self.internal] = [self.chrome_exe, '--user-data-dir', r'C:\Temp\jarvis_profiles\chrome']
        self.runtime.new_turn('Open Chrome')
        self.assertFalse(ownership.record_open(ai.Match(self.internal, 'chrome.exe', 100, True), 'Chrome'))

    def test_window_identity_change_after_post_is_not_reported_closed(self):
        self.command('Open', 'Chrome')
        context = self.runtime.new_turn('Close Chrome')
        match = next(m for m in ai.running('Chrome', include_self=True) if m.pid == self.chrome)
        from dataclasses import replace
        target = replace(match, window_identity=ownership.registered_windows(self.chrome)[0][0], window_context=context)
        def changed(hwnd):
            self.windows[hwnd]['nonce'] += 1
            return True
        with patch.object(ownership, '_post_window_close', side_effect=changed):
            gone, reason = ai.request_close(target, timeout=0)
        self.assertFalse(gone)
        self.assertIn('not confirmed', reason)

    def test_inspection_failure_after_post_is_unverified(self):
        self.command('Open', 'Chrome')
        context = self.runtime.new_turn('Close Chrome')
        match = next(m for m in ai.running('Chrome', include_self=True) if m.pid == self.chrome)
        from dataclasses import replace
        target = replace(match, window_identity=ownership.registered_windows(self.chrome)[0][0], window_context=context)
        self.block_close = True
        def inspect(hwnd):
            if self.posts:
                raise OSError('cannot inspect after posting')
            return dict(self.windows[hwnd])
        with patch.object(ownership, 'window_info', side_effect=inspect):
            gone, reason = ai.request_close(target, timeout=0)
        self.assertFalse(gone)
        self.assertIn('not verified', reason)
