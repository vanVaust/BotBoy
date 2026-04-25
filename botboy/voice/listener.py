"""
VoiceListener — Vosk-based offline ASR with wake-word detection.

Requires: pip install vosk pyaudio
Falls back gracefully when dependencies are unavailable.
"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class VoiceEvent:
    text:      str
    is_final:  bool
    confidence: float = 1.0


class VoiceListener:
    """
    Offline voice recognition using Vosk.

    Architecture:
        Audio capture thread → Queue → Recognition thread → Callback

    Wake-word: if configured, only fires callback when wake-word
    is detected in the recognized text.
    """

    DEFAULT_WAKE_WORD = "botboy"
    DEFAULT_SAMPLE_RATE = 16000

    def __init__(
        self,
        callback: Optional[Callable[[VoiceEvent], None]] = None,
        model_path: str = "vosk-model-small-en-us",
        wake_word: str = DEFAULT_WAKE_WORD,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        self._callback    = callback
        self._model_path  = model_path
        self._wake_word   = wake_word.lower()
        self._sample_rate = sample_rate
        self._running     = False
        self._thread: Optional[threading.Thread] = None
        self._audio_queue: queue.Queue = queue.Queue()

        # Check dependencies
        self._vosk_available  = self._check_vosk()
        self._audio_available = self._check_pyaudio()

    @staticmethod
    def _check_vosk() -> bool:
        try:
            import vosk  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _check_pyaudio() -> bool:
        try:
            import pyaudio  # noqa: F401
            return True
        except ImportError:
            return False

    def is_available(self) -> bool:
        return self._vosk_available and self._audio_available

    def start(self) -> bool:
        """Start voice listener. Returns True if successfully started."""
        if not self.is_available():
            return False
        if self._running:
            return True
        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="botboy-voice"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _listen_loop(self) -> None:
        """Main recognition loop (runs in background thread)."""
        try:
            import vosk
            import pyaudio

            model = vosk.Model(self._model_path)
            rec   = vosk.KaldiRecognizer(model, self._sample_rate)

            pa     = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._sample_rate,
                input=True,
                frames_per_buffer=8192,
            )
            stream.start_stream()

            while self._running:
                data = stream.read(4096, exception_on_overflow=False)
                if len(data) == 0:
                    continue

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text   = result.get("text", "").strip()
                    if text:
                        self._fire(text, is_final=True)
                else:
                    partial = json.loads(rec.PartialResult())
                    text    = partial.get("partial", "").strip()
                    if text:
                        self._fire(text, is_final=False)

            stream.stop_stream()
            stream.close()
            pa.terminate()

        except Exception:
            self._running = False

    def _fire(self, text: str, is_final: bool) -> None:
        """Fire callback if wake-word is detected (or no wake-word configured)."""
        if self._wake_word and self._wake_word not in text.lower():
            return
        if self._callback:
            # Strip wake-word from text before passing to callback
            clean = text.lower().replace(self._wake_word, "").strip()
            self._callback(VoiceEvent(text=clean, is_final=is_final))

    def status(self) -> dict:
        return {
            "running": self._running,
            "vosk_available": self._vosk_available,
            "audio_available": self._audio_available,
            "wake_word": self._wake_word,
            "model_path": self._model_path,
        }
