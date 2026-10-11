"""Channel abstraction so Telegram can later be swapped for WhatsApp/Discord/etc."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Messenger(ABC):
    @abstractmethod
    async def send_text(self, text: str) -> bool: ...

    @abstractmethod
    async def send_voice(self, audio_path: Path) -> bool: ...

    @abstractmethod
    async def send_file(self, path: Path, caption: str = "") -> bool: ...

    @abstractmethod
    async def healthy(self) -> bool: ...
