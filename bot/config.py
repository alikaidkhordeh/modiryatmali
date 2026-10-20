from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import FrozenSet, Optional
from zoneinfo import ZoneInfo

try:  # python-dotenv is optional; .env is also read by systemd (EnvironmentFile)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_ids: FrozenSet[int]
    anthropic_api_key: Optional[str]
    anthropic_model: str
    db_path: str
    tz: ZoneInfo
    webapp_url: str  # base URL of the mini-app page (docs/index.html on GitHub Pages); "" = off


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN در فایل .env خالی است. توکن را از @BotFather بگیر و در .env بگذار.")

    raw_ids = os.getenv("ALLOWED_USER_IDS", "")
    ids = frozenset(
        int(x) for x in re.split(r"[,\s]+", raw_ids) if x.strip().lstrip("-").isdigit()
    )

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip() or None
    model = os.getenv("ANTHROPIC_MODEL", "").strip() or "claude-haiku-5-5"
    db_path = os.getenv("DB_PATH", "").strip() or "data/expenses.db"
    tz = ZoneInfo(os.getenv("BOT_TZ", "").strip() or "Asia/Tehran")

    return Settings(
        bot_token=token,
        allowed_ids=ids,
        anthropic_api_key=api_key,
        anthropic_model=model,
        db_path=db_path,
        tz=tz,
        webapp_url=os.getenv("WEBAPP_URL", "").strip(),
    )
