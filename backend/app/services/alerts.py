"""
Алерты: Telegram + локальный лог (без авто-патча кода).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("scalper.alerts")


def _append_log(line: str) -> None:
    s = get_settings()
    p = Path(s.alert_log_path)
    if not str(p).strip():
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning("alert log write failed: %s", e)


def send_alert(title: str, body: str, *, level: str = "warning", extra: dict[str, Any] | None = None) -> None:
    s = get_settings()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    msg = f"[{ts}] [{level}] {title}: {body}"
    if extra:
        msg += f" | {extra}"
    logger.log(logging.WARNING if level != "info" else logging.INFO, msg)
    _append_log(msg)

    token = (s.alert_telegram_bot_token or "").strip()
    chat = (s.alert_telegram_chat_id or "").strip()
    if not token or not chat:
        return
    text = f"*{title}*\n{body}"
    if extra:
        text += f"\n`{extra}`"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        httpx.post(
            url,
            json={"chat_id": chat, "text": text[:3900], "parse_mode": "Markdown"},
            timeout=15.0,
        )
    except Exception as e:
        logger.warning("telegram alert failed: %s", e)
