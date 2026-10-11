"""Entry point: `python run.py`."""
from __future__ import annotations

import asyncio
import sys

import uvicorn

from app.config import get_settings


def main() -> int:
    s = get_settings()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if not s.telegram_bot_token or not s.telegram_user_id:
        print("⚠  TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID are not set in .env — the bot will not start.\n"
              "   Copy .env.example to .env and fill them in (see README).")
    uvicorn.run("app.main:create_app", factory=True, host=s.api_host, port=s.api_port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
