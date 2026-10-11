"""Speech-to-text. Provider is pluggable; faster-whisper (local, free) is the default."""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)


class TranscriptionUnavailable(Exception):
    """STT engine not installed/disabled."""


class Transcriber(ABC):
    @abstractmethod
    async def transcribe(self, audio_path: Path) -> str: ...


class FasterWhisperTranscriber(Transcriber):
    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except ImportError as e:
                raise TranscriptionUnavailable(
                    "faster-whisper isn't installed (pip install -r requirements-voice.txt)") from e
            s = get_settings()
            log.info("loading whisper model", extra={"model": s.whisper_model})
            self._model = WhisperModel(s.whisper_model, device=s.whisper_device, compute_type=s.whisper_compute_type)
        return self._model

    def _run(self, path: Path) -> str:
        model = self._load()
        segments, _info = model.transcribe(str(path), vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def transcribe(self, audio_path: Path) -> str:
        return await asyncio.to_thread(self._run, audio_path)


def get_transcriber() -> Transcriber | None:
    s = get_settings()
    return FasterWhisperTranscriber() if s.voice_input_enabled else None
