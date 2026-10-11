"""Optional local text-to-speech with Piper. Text replies always work even if this is unavailable."""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import VOICE_DIR, get_settings

log = logging.getLogger(__name__)


class TTSUnavailable(Exception):
    pass


class TextToSpeech(ABC):
    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    async def synthesize(self, text: str) -> Path:
        """Return a path to an audio file (ogg/opus preferred for Telegram voice notes)."""


class PiperTTS(TextToSpeech):
    def _binary(self) -> str | None:
        return shutil.which(get_settings().piper_binary)

    def available(self) -> bool:
        s = get_settings()
        return bool(self._binary() and s.piper_model_path and Path(s.piper_model_path).is_file())

    async def synthesize(self, text: str) -> Path:
        if not self.available():
            raise TTSUnavailable("Piper binary or model not configured")
        s = get_settings()
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        wav = VOICE_DIR / f"tts_{uuid.uuid4().hex}.wav"
        # argument list, no shell: the text goes through stdin only
        proc = await asyncio.create_subprocess_exec(
            self._binary(), "--model", s.piper_model_path, "--output_file", str(wav),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.communicate(text[:1500].encode("utf-8")), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            raise TTSUnavailable("Piper timed out")
        if proc.returncode != 0 or not wav.exists():
            raise TTSUnavailable("Piper failed")
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            ogg = wav.with_suffix(".ogg")
            p2 = await asyncio.create_subprocess_exec(
                ffmpeg, "-y", "-i", str(wav), "-c:a", "libopus", str(ogg),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await p2.wait()
            if p2.returncode == 0 and ogg.exists():
                wav.unlink(missing_ok=True)
                return ogg
        return wav


def get_tts() -> TextToSpeech:
    return PiperTTS()
