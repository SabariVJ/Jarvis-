"""Offline wake tests: no microphone, normal AI request, call, or live settings."""
import ast
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from core import outcome
from core.command_router import resolve
from core.wake_phrases import DEFAULT_PHRASE, matches, normalize, settings_command
from core.wake_word import WakeWordDetector
from memory import config_manager as config
from plugins import wake_settings
from test_automation import Isolated


def final(text=DEFAULT_PHRASE, confidence=.99):
    return {'text': text, 'result': [{'word': word, 'conf': confidence} for word in text.split()]}


class WakeTests(Isolated):
    def setUp(self):
        super().setUp()
        self.folder = self.root / 'config'
        self.folder.mkdir()
        self.file = self.folder / 'api_keys.json'
        self.original = {'other_setting': {'value': 42}, 'wake_word_enabled': True}
        self.file.write_text(json.dumps(self.original), encoding='utf-8')
        self.config_patch = patch.multiple(config, CONFIG_DIR=self.folder, CONFIG_FILE=self.file)
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        super().tearDown()

    def detector(self, **kwargs):
        callback = Mock()
        detector = WakeWordDetector(callback, **kwargs)
        detector._running = True
        return detector, callback

    def test_phrase_normalization_and_harmless_variants(self):
        for text in ("WAKE UP JARVIS, DADDY’S HOME!", 'wake up jarvis daddys home',
                     'wake up jar vis daddy is home', 'wake up jarvis daddies home'):
            self.assertTrue(matches(text, DEFAULT_PHRASE, False), text)

    def test_complete_phrase_wakes_once_per_utterance(self):
        detector, callback = self.detector()
        self.assertTrue(detector._accept(final(), 0))
        self.assertFalse(detector._accept(final(), 0))
        callback.assert_called_once()

    def test_full_phrase_tolerates_homophone_confidence_split(self):
        detector, callback = self.detector()
        result = final()
        result['result'][-2]['conf'] = .5
        self.assertTrue(detector._accept(result, 0))
        callback.assert_called_once()

    def test_actual_host_wake_transition_runs_once_from_sleep(self):
        import time
        source = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(source) if isinstance(node, ast.FunctionDef) and node.name == 'wake')
        env = {'time': time}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'wake-host', 'exec'), env)
        host = SimpleNamespace(_awake=False, ui=Mock(muted=False))
        detector = WakeWordDetector(lambda: env['wake'](host))
        detector._running = True
        host._wake_detector = detector
        self.assertTrue(detector._accept(final(), 0))
        self.assertTrue(host._awake)
        self.assertFalse(detector._accept(final(), detector._generation))
        host.ui.write_log.assert_called_once()

    def test_unknown_vocabulary_does_not_silently_shorten_phrase(self):
        detector, _ = self.detector()
        detector._model = Mock()
        detector._model.vosk_model_find_word.return_value = -1
        with self.assertRaises(ValueError):
            detector.configure('Unknown vocabulary', False)
        self.assertEqual(detector._phrase, DEFAULT_PHRASE)

    def test_fallback_is_optional(self):
        detector, callback = self.detector(fallback_enabled=False)
        self.assertFalse(detector._accept(final('Hey Jarvis'), 0))
        detector.configure(DEFAULT_PHRASE, True)
        self.assertTrue(detector._accept(final('Hey Jarvis'), detector._generation))
        callback.assert_called_once()

    def test_unrelated_partial_low_confidence_or_extended_speech_never_wakes(self):
        detector, callback = self.detector()
        for text in ('Jarvis', "daddy's home", 'what time is dinner',
                     "do not wake up jarvis daddy's home", DEFAULT_PHRASE + ' and call someone'):
            self.assertFalse(detector._accept(final(text), 0))
        self.assertFalse(detector._accept(final(confidence=.2), 0))
        self.assertFalse(detector._accept({'partial': normalize(DEFAULT_PHRASE)}, 0))
        callback.assert_not_called()

    def test_old_queued_audio_is_invalidated_after_phrase_change(self):
        detector, callback = self.detector()
        detector.configure('Hello computer', False)
        self.assertFalse(detector._accept(final(), 0))
        self.assertFalse(detector._accept(final(), detector._generation))
        self.assertTrue(detector._accept(final('Hello computer'), detector._generation))
        callback.assert_called_once()

    def test_no_inference_queue_while_awake_or_disabled(self):
        detector, _ = self.detector()
        detector.set_listening(False)
        detector.feed(np.zeros((1024, 1), dtype=np.int16))
        self.assertTrue(detector._queue.empty())
        detector._running = False
        detector.feed(np.zeros(1024, dtype=np.int16))
        self.assertTrue(detector._queue.empty())

    def test_missing_model_start_does_not_download(self):
        detector = WakeWordDetector(Mock(), notify=Mock())
        with patch('core.wake_word.is_ready', return_value=False), patch('requests.get') as network:
            self.assertFalse(detector.start())
        network.assert_not_called()

    def test_settings_persist_in_fresh_process_and_preserve_other_keys(self):
        config.save_wake_config(DEFAULT_PHRASE, False)
        data = json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(data['other_setting'], self.original['other_setting'])
        self.assertTrue(data['wake_word_enabled'])
        result = subprocess.run([sys.executable, '-B', '-c',
            "from pathlib import Path; import sys; from memory import config_manager as c; "
            "c.CONFIG_FILE=Path(sys.argv[1]); print(c.get_wake_config()['phrase'])", str(self.file)],
            capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), DEFAULT_PHRASE)

    def test_corrupt_settings_are_not_overwritten(self):
        self.file.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            config.save_wake_config(DEFAULT_PHRASE)
        self.assertEqual(self.file.read_text(encoding='utf-8'), '{broken')

    def test_explicit_voice_request_updates_and_reports_current_phrase(self):
        context = self.runtime.new_turn('Change my wake phrase to Hello computer')
        result = wake_settings.run({'action': 'set', 'phrase': 'Hello computer'}, session_memory=context)
        self.assertIn('status=done', result)
        self.assertEqual(config.get_wake_config()['phrase'], 'Hello computer')
        context = self.runtime.new_turn('What is your wake phrase?')
        report = wake_settings.run({'action': 'get'}, session_memory=context)
        self.assertIn('Hello computer', wake_settings.summarize(report))

    def test_setter_rejects_invented_phrase_or_fallback_and_old_execution(self):
        context = self.runtime.new_turn('Set my wake phrase to Hello computer')
        for parameters in ({'action': 'set', 'phrase': 'Call everybody'},
                           {'action': 'set', 'phrase': 'Hello computer', 'fallback_enabled': False}):
            self.assertIn('status=failed', wake_settings.run(parameters, session_memory=context))
        self.runtime.finish(context)
        self.assertIn('status=failed', wake_settings.run({'action': 'set', 'phrase': 'Hello computer'}, session_memory=context))
        self.assertNotIn('wake_phrase', json.loads(self.file.read_text(encoding='utf-8')))

    def test_fallback_can_be_changed_without_changing_phrase(self):
        config.save_wake_config(DEFAULT_PHRASE, True)
        context = self.runtime.new_turn('Disable Hey Jarvis fallback')
        result = wake_settings.run({'action': 'set', 'fallback_enabled': False}, session_memory=context)
        self.assertIn('status=done', result)
        self.assertEqual(config.get_wake_config(), {'phrase': DEFAULT_PHRASE, 'fallback_enabled': False})

    def test_wake_commands_route_to_settings_and_block_other_tools(self):
        request = 'Set my wake phrase to ' + DEFAULT_PHRASE
        route = resolve(request, [wake_settings.PLUGIN])
        self.assertEqual(route[0][0], 'wake_settings')
        self.assertIsNone(resolve(request, []))
        context = self.runtime.new_turn(request)
        unrelated = Mock(return_value=outcome.done('web_search'))
        result = self.runtime.execute('web_search', {'query': 'wake word custom'}, unrelated, context)
        unrelated.assert_not_called()
        self.assertIn('status=failed', result)

    def test_receipt_reuse_does_not_apply_setting_twice(self):
        context = self.runtime.new_turn('Set my wake phrase to Hello computer')
        parameters = {'action': 'set', 'phrase': 'Hello computer'}
        with patch.object(wake_settings, 'save_wake_config', wraps=config.save_wake_config) as save:
            for _ in range(2):
                self.runtime.execute('wake_settings', parameters,
                    lambda: wake_settings.run(parameters, session_memory=context), context)
        save.assert_called_once()

    def test_sleeping_microphone_only_feeds_local_detector(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(source) if isinstance(node, ast.AsyncFunctionDef) and node.name == '_listen_audio')
        callback = next(node for node in method.body if isinstance(node, ast.FunctionDef) and node.name == 'callback')
        detector = Mock()
        host = SimpleNamespace(_wake_enabled=True, _awake=False, _wake_detector=detector)
        environment = {'self': host}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[])), 'callback', 'exec'), environment)
        # Host has no cloud/queue/speaking attributes: any fall-through fails.
        environment['callback'](np.zeros((1024, 1), dtype=np.int16), 1024, None, None)
        detector.feed.assert_called_once()

    def test_local_stt_settings_never_calls_planning_model(self):
        from providers.engine_pipeline import PipelineEngine
        async def exercise():
            model = Mock(caps={'tools': True, 'tool_format': 'openai'})
            model.chat.side_effect = AssertionError('Wake settings must not trigger unrelated planning or search')
            engine = PipelineEngine(model, config=SimpleNamespace(system_instruction='',
                tools=[{'function_declarations': [wake_settings.PLUGIN]}]))
            engine._history = [{'role': 'user', 'content': 'Set my wake phrase to Hello computer'}]
            await engine._generate()
            self.assertEqual(list(engine._awaiting.values()), ['wake_settings'])
            model.chat.assert_not_called()
            await engine.close()
        asyncio.run(exercise())

    def test_qt_settings_dialog_saves_phrase_and_fallback(self):
        from PyQt6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QLabel, QLineEdit, QCheckBox, QPushButton
        app = QApplication.instance() or QApplication([])
        source = ast.parse((Path(__file__).resolve().parents[1] / 'ui.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(source) if isinstance(node, ast.FunctionDef) and node.name == '_edit_wake_phrase')
        env = dict(QDialog=QDialog, QVBoxLayout=QVBoxLayout, QLabel=QLabel, QLineEdit=QLineEdit,
                   QCheckBox=QCheckBox, QPushButton=QPushButton, C=SimpleNamespace(DARK='#001122', TEXT='#eeeeee'))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'wake-ui', 'exec'), env)
        host = QWidget()
        host.on_wake_phrase = config.save_wake_config
        host._log_sig = Mock()
        host._refresh_wake_btns = Mock()
        def interact(dialog):
            dialog.findChild(QLineEdit, 'wake_phrase_input').setText('Hello computer')
            dialog.findChild(QCheckBox, 'wake_fallback_input').setChecked(False)
            dialog.findChild(QPushButton, 'wake_phrase_save').click()
            return 0
        with patch.object(QDialog, 'exec', interact):
            env['_edit_wake_phrase'](host)
        self.assertEqual(config.get_wake_config(), {'phrase': 'Hello computer', 'fallback_enabled': False})
        host.close()
