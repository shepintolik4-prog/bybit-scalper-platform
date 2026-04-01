from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("scalper.telegram")


@dataclass(frozen=True)
class TelegramMessage:
    chat_id: int
    text: str


class TelegramBot:
    """
    Minimal Telegram control bot (long polling).

    Uses settings:
    - ALERT_TELEGRAM_BOT_TOKEN / ALERT_TELEGRAM_CHAT_ID for notifications
    - TELEGRAM_ALLOWED_CHAT_IDS (csv) to authorize commands
    - API_SECRET to call protected endpoints
    """

    def __init__(self) -> None:
        self._s = get_settings()
        self._token = (self._s.alert_telegram_bot_token or "").strip()
        self._api_base = (getattr(self._s, "telegram_api_base", "") or "").strip() or "http://127.0.0.1:8000"
        self._offset = 0

        allowed_csv = (getattr(self._s, "telegram_allowed_chat_ids", "") or "").strip()
        if not allowed_csv and (self._s.alert_telegram_chat_id or "").strip():
            allowed_csv = str(self._s.alert_telegram_chat_id).strip()
        ids: set[int] = set()
        for part in allowed_csv.split(","):
            p = part.strip()
            if not p:
                continue
            try:
                ids.add(int(p))
            except ValueError:
                continue
        self._allowed = ids

    def _tg_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _send(self, chat_id: int, text: str) -> None:
        if not self._token:
            return
        try:
            httpx.post(
                self._tg_url("sendMessage"),
                json={"chat_id": chat_id, "text": text[:3900]},
                timeout=20.0,
            )
        except Exception as e:
            logger.warning("telegram send failed: %s", e)

    def _api(self, method: str, path: str, *, needs_secret: bool = False) -> dict[str, Any] | list[Any] | None:
        url = self._api_base.rstrip("/") + path
        headers: dict[str, str] = {}
        if needs_secret:
            headers["X-API-Secret"] = str(self._s.api_secret or "")
        try:
            r = httpx.request(method, url, headers=headers, timeout=25.0)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _format_positions(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "Нет открытых позиций."
        lines = []
        for p in rows[:20]:
            sym = p.get("symbol")
            side = p.get("side")
            upnl = p.get("unrealized_pnl_usdt")
            mark = p.get("last_mark_price")
            lines.append(f"{sym} {side} mark={mark} uPnL={upnl}")
        more = "" if len(rows) <= 20 else f"\n…и ещё {len(rows) - 20}"
        return "\n".join(lines) + more

    def _handle(self, msg: TelegramMessage) -> None:
        chat_id = msg.chat_id
        if self._allowed and chat_id not in self._allowed:
            self._send(chat_id, "Доступ запрещён.")
            return

        txt = (msg.text or "").strip()
        if txt.startswith("/start"):
            self._send(
                chat_id,
                "Команды:\n"
                "/status\n"
                "/pnl\n"
                "/positions\n"
                "/stop\n"
                "/start_trading",
            )
            return

        if txt.startswith("/status"):
            st = self._api("GET", "/api/system/status") or {}
            self._send(chat_id, json.dumps(st, ensure_ascii=False)[:3900])
            return

        if txt.startswith("/pnl"):
            eq = self._api("GET", "/api/equity") or {}
            self._send(chat_id, json.dumps(eq, ensure_ascii=False)[:3900])
            return

        if txt.startswith("/positions"):
            pos = self._api("GET", "/api/positions") or []
            if isinstance(pos, list):
                self._send(chat_id, self._format_positions(pos))
            else:
                self._send(chat_id, json.dumps(pos, ensure_ascii=False)[:3900])
            return

        if txt.startswith("/stop"):
            r = self._api("POST", "/api/bot/stop", needs_secret=True) or {}
            self._send(chat_id, json.dumps(r, ensure_ascii=False)[:3900])
            return

        if txt.startswith("/start_trading"):
            r = self._api("POST", "/api/bot/start", needs_secret=True) or {}
            self._send(chat_id, json.dumps(r, ensure_ascii=False)[:3900])
            return

        self._send(chat_id, "Не понял. /start для списка команд.")

    def run_forever(self) -> None:
        if not self._token:
            raise RuntimeError("ALERT_TELEGRAM_BOT_TOKEN is empty")
        logger.info("telegram bot started allowed=%s api_base=%s", sorted(self._allowed), self._api_base)
        while True:
            try:
                payload = {"timeout": 25, "offset": self._offset}
                r = httpx.get(self._tg_url("getUpdates"), params=payload, timeout=35.0)
                r.raise_for_status()
                data = r.json()
                for upd in data.get("result") or []:
                    self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    text = msg.get("text")
                    if chat_id is None or not text:
                        continue
                    self._handle(TelegramMessage(chat_id=int(chat_id), text=str(text)))
            except Exception as e:
                logger.warning("telegram poll error: %s", e)
                time.sleep(2.0)


def main() -> None:
    TelegramBot().run_forever()


if __name__ == "__main__":
    main()

