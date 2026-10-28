"""Local configurable wake phrases; sleeping PCM never goes to a cloud model.

One lazy Vosk model, bounded worker queue, final matching, and an arm latch.
No inference while awake/off. No download during start().
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from core.wake_phrases import DEFAULT_PHRASE, matches, recognition_phrases, validate

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / 'downloads/wake-runtime'
MODEL_NAME = 'vosk-model-small-en-us-0.15'
MODEL_DIR = ROOT / 'downloads/wake-models' / MODEL_NAME
MODEL_URL = 'https://alphacephei.com/vosk/models/' + MODEL_NAME + '.zip'
SAMPLE_RATE = 16000
DEFAULT_THRESHOLD = .75
_install_lock = threading.Lock()


def is_installed():
    return (RUNTIME_DIR / 'vosk/__init__.py').is_file() or importlib.util.find_spec('vosk') is not None


def is_ready():
    return is_installed() and all((MODEL_DIR / name).is_file() for name in ('am/final.mdl', 'conf/model.conf', 'graph/HCLr.fst', 'graph/Gr.fst'))


def install_and_download(logger=print, notify=None):
    """Explicit setup only; project-local files avoid filling the system disk."""
    with _install_lock:
        tell = notify or logger
        try:
            temp_root = ROOT / 'downloads/wake-temp'
            temp_root.mkdir(parents=True, exist_ok=True)
            if not (RUNTIME_DIR / 'vosk/__init__.py').is_file():
                import os
                env = {**os.environ, 'TEMP': str(temp_root), 'TMP': str(temp_root)}
                tell('Installing the local custom-wake recognizer on the project drive…')
                result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--no-deps',
                    '--target', str(RUNTIME_DIR), 'vosk==0.3.45', 'srt==3.5.3'], capture_output=True,
                    text=True, encoding='utf-8', errors='replace', env=env, timeout=240)
                if result.returncode:
                    return False, 'Wake runtime setup failed: ' + (result.stderr or result.stdout)[-400:]
            if not is_ready():
                import requests
                tell('Downloading the offline English wake model (about 40 MB)…')
                MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='wake-setup-', dir=temp_root) as folder:
                    archive = Path(folder) / 'model.zip'
                    size = 0
                    with requests.get(MODEL_URL, stream=True, timeout=(15, 90), allow_redirects=False) as response:
                        response.raise_for_status()
                        if response.status_code != 200:
                            raise ValueError('Model server returned a redirect or unexpected response')
                        with archive.open('wb') as stream:
                            for block in response.iter_content(1024 * 256):
                                size += len(block)
                                if size > 100 * 1024 * 1024:
                                    raise ValueError('Wake model archive exceeds the size limit')
                                stream.write(block)
                    with zipfile.ZipFile(archive) as package:
                        if sum(item.file_size for item in package.infolist()) > 250 * 1024 * 1024:
                            raise ValueError('Expanded wake model exceeds the size limit')
                        for item in package.infolist():
                            name = Path(item.filename)
                            destination = (MODEL_DIR.parent / name).resolve()
                            if (name.is_absolute() or not name.parts or name.parts[0] != MODEL_NAME
                                    or not destination.is_relative_to(MODEL_DIR.resolve())
                                    or ':' in item.filename or '\\' in item.filename
                                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                                raise ValueError('Unsafe model archive path')
                        package.extractall(MODEL_DIR.parent)
            return (True, 'Custom wake recognizer installed. Audio stays local.') if is_ready() else (False, 'Wake model files are incomplete')
        except Exception as exc:
            return False, 'Custom wake setup failed: ' + str(exc)


class WakeWordDetector:
    def __init__(self, on_detect, threshold=DEFAULT_THRESHOLD, logger=print, notify=None,
                 phrase=DEFAULT_PHRASE, fallback_enabled=True):
        self._on_detect, self._logger = on_detect, logger
        self._notify = notify or (lambda _: None)
        self._threshold = threshold
        self._queue = queue.Queue(maxsize=20)
        self._lock = threading.RLock()
        self._thread = None
        self._model = None
        self._ready = self._running = False
        self._armed = True
        self._generation = 0
        self._phrase, self._fallback = validate(phrase), bool(fallback_enabled)
        self._last_detect = float('-inf')

    @property
    def ready(self):
        return self._ready

    def configure(self, phrase, fallback_enabled):
        phrase = validate(phrase)
        with self._lock:
            if self._model:
                self._check_vocabulary(phrase, fallback_enabled)
            self._phrase, self._fallback = phrase, bool(fallback_enabled)
            self._generation += 1
            self._drain()

    def set_listening(self, enabled):
        with self._lock:
            self._armed = bool(enabled)
            self._generation += 1
            self._drain()

    def _check_vocabulary(self, phrase, fallback):
        phrases = recognition_phrases(phrase, fallback)
        valid = [text for text in phrases if all(self._model.vosk_model_find_word(word) >= 0 for word in text.split())]
        if not any(matches(text, phrase, False) for text in valid):
            raise ValueError('Wake phrase has words missing from the offline English model; choose ordinary English words')
        if fallback and 'hey jarvis' not in valid:
            raise ValueError('The offline model does not support the Hey Jarvis fallback')
        return valid

    def start(self):
        with self._lock:
            if self._running:
                return True
            if self._thread and self._thread.is_alive():
                self._notify('Previous wake worker is still stopping; retry shortly')
                return False
            try:
                if not is_ready():
                    raise RuntimeError('Custom wake model is not installed; use WAKE WORD: DOWNLOAD')
                if str(RUNTIME_DIR) not in sys.path:
                    sys.path.insert(0, str(RUNTIME_DIR))
                from vosk import Model, SetLogLevel
                SetLogLevel(-1)
                self._model = Model(str(MODEL_DIR))
                self._check_vocabulary(self._phrase, self._fallback)
                self._running = self._ready = self._armed = True
                self._thread = threading.Thread(target=self._loop, daemon=True, name='CustomWakeThread')
                self._thread.start()
                self._logger('Local wake detector ready for: ' + self._phrase)
                return True
            except Exception as exc:
                self._model = None
                self._notify('Wake detector unavailable: ' + str(exc) + '. Use WAKE NOW.')
                return False

    def stop(self):
        with self._lock:
            self._running = self._ready = False
            self._armed = False
            self._generation += 1
            self._drain()
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        if not self._thread or not self._thread.is_alive():
            self._model = None

    def feed(self, frame_int16):
        if not self._running or not self._armed:
            return
        try:
            data = frame_int16[:, 0] if getattr(frame_int16, 'ndim', 1) > 1 else frame_int16
            self._queue.put_nowait((self._generation, data.copy().tobytes()))
        except (queue.Full, AttributeError):
            pass

    def _accept(self, result, generation):
        """Final text only; confidence + full-phrase match + one wake per arm."""
        text, words = result.get('text', ''), result.get('result', [])
        confidence = [float(word.get('conf', 0)) for word in words]
        # Grammar alternatives such as daddy's/daddies share acoustic evidence.
        # Require a strong complete phrase, not .75 on every homophone token.
        if not confidence or min(confidence) < .35 or sum(confidence) / len(confidence) < self._threshold:
            return False
        with self._lock:
            if (not self._running or not self._armed or generation != self._generation
                    or time.monotonic() - self._last_detect < 1.5
                    or not matches(text, self._phrase, self._fallback)):
                return False
            self._armed = False
            self._last_detect = time.monotonic()
            self._drain()
            self._on_detect()
            return True

    def _loop(self):
        from vosk import KaldiRecognizer
        recognizer, generation = None, -1
        while self._running:
            item = self._queue.get()
            if item is None:
                return
            current, data = item
            try:
                with self._lock:
                    if current != self._generation or not self._armed:
                        continue
                    if current != generation:
                        grammar = self._check_vocabulary(self._phrase, self._fallback) + ['[unk]']
                        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE, json.dumps(grammar))
                        recognizer.SetWords(True)
                        generation = current
                if recognizer.AcceptWaveform(data):
                    self._accept(json.loads(recognizer.Result()), current)
            except Exception as exc:
                self._logger('Local wake detection error: ' + str(exc))

    def _drain(self):
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
