"""Optional phone-call adapter interface. NOT free in general (needs a SIP trunk/provider) and not used by default.

To add one: subclass TelephonyProvider (e.g. Asterisk ARI / SIP), then return it from get_telephony().
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TelephonyProvider(ABC):
    @abstractmethod
    async def place_call(self, message: str) -> bool:
        """Call the user and speak `message`. Return True if the call was placed."""

    @abstractmethod
    async def healthy(self) -> bool: ...
