from __future__ import annotations

from app.integrations.telephony.base import TelephonyProvider


def get_telephony() -> TelephonyProvider | None:
    """No provider is bundled. Return an implementation here once you add one (Asterisk/SIP/Twilio...)."""
    return None
