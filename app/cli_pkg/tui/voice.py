"""Mic recording to a WAV tempfile for POST /stt (sounddevice)."""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path
from typing import Any


class VoiceError(RuntimeError):
    """Mic unavailable or recording failed."""


class VoiceRecorder:
    """Record mono 16-bit PCM via sounddevice until stop()."""

    def __init__(self, *, sample_rate: int = 16_000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._stream: Any = None
        self._frames: list[bytes] = []
        self._path: Path | None = None
        self._recording = False

    @property
    def recording(self) -> bool:
        return self._recording

    def start(self) -> Path:
        if self._recording:
            raise VoiceError("already recording")
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise VoiceError("sounddevice not installed") from exc

        self._frames = []
        tmp = tempfile.NamedTemporaryFile(prefix="aio-voice-", suffix=".wav", delete=False)
        tmp.close()
        self._path = Path(tmp.name)

        def callback(indata, frames, time, status) -> None:  # noqa: ARG001
            self._frames.append(indata.copy().tobytes())

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
            self._path = None
            raise VoiceError(f"mic unavailable: {exc}") from exc
        self._recording = True
        return self._path

    def stop(self) -> Path:
        if not self._recording:
            raise VoiceError("not recording")
        self._recording = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        path = self._path
        if path is None:
            raise VoiceError("no output path")
        raw = b"".join(self._frames)
        self._frames = []
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(raw)
        return path

    def cancel(self) -> None:
        if not self._recording:
            return
        try:
            self.stop()
        except VoiceError:
            pass
        if self._path and self._path.exists():
            try:
                self._path.unlink()
            except OSError:
                pass
        self._path = None
