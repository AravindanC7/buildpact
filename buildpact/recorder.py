"""Meeting recorder: listens through the presenter laptop's microphone.

`!record` in the team server starts it, `!stop` ends it; the bot then transcribes the audio and the
mediator turns it into notes and next steps. Recording is always announced in the channel first.

Uses `sounddevice` (pip install sounddevice). On macOS the app running the bot (Terminal / Cursor)
must be allowed to use the microphone: System Settings → Privacy & Security → Microphone.
"""
from __future__ import annotations

import io
import threading
import time
import wave

RATE = 16000          # 16 kHz mono is plenty for speech and keeps uploads small
MAX_SECONDS = 15 * 60


class Recorder:
    def __init__(self):
        self._chunks: list[bytes] = []
        self._stream = None
        self._lock = threading.Lock()
        self.started_at: float | None = None
        self.by: str | None = None

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self, by: str) -> None:
        try:
            import sounddevice as sd
        except ImportError as e:  # pragma: no cover - depends on the presenter's machine
            raise RuntimeError("Recording needs `pip install sounddevice` on the laptop running the bot.") from e
        if self.active:
            raise RuntimeError("Already recording.")
        self._chunks = []

        def callback(indata, frames, t, status):
            with self._lock:
                self._chunks.append(bytes(indata))

        try:
            self._stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", callback=callback)
            self._stream.start()
        except Exception as e:  # pragma: no cover
            self._stream = None
            raise RuntimeError(f"Couldn't open the microphone ({e}). On macOS, allow the app running the bot "
                               "under System Settings → Privacy & Security → Microphone.") from e
        self.started_at, self.by = time.time(), by

    def elapsed(self) -> float:
        return time.time() - self.started_at if self.started_at else 0.0

    def stop(self) -> bytes:
        """Stop and return the recording as WAV bytes."""
        if not self.active:
            raise RuntimeError("Not recording.")
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        with self._lock:
            pcm = b"".join(self._chunks)
            self._chunks = []
        return to_wav(pcm)


def to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
