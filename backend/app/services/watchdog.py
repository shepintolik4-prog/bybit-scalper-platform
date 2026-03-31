"""
Внешний watchdog: health API, возраст последней сделки, длительность тика.
Перезапуск — только через WATCHDOG_RESTART_CMD (subprocess), без изменения кода.
Запуск: из каталога backend: `python -m app.services.watchdog`
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time

import httpx

from app.config import get_settings
from app.services.alerts import send_alert

logger = logging.getLogger("scalper.watchdog")


def _restart(cmd: str) -> None:
    logger.warning("executing restart cmd: %s", cmd)
    subprocess.Popen(cmd, shell=True)  # noqa: S602


def run_loop() -> None:
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = s.watchdog_api_base.rstrip("/")
    interval = max(15, int(s.watchdog_interval_sec))
    ready_url = f"{base}/api/health/ready"
    wd_url = f"{base}/api/health/watchdog"
    to = float(s.watchdog_ready_timeout_sec)
    no_trade = float(s.watchdog_no_trade_sec)
    max_tick = float(s.watchdog_max_tick_sec)
    restart_cmd = (s.watchdog_restart_cmd or "").strip()

    while True:
        try:
            r = httpx.get(ready_url, timeout=to)
            if r.status_code != 200:
                send_alert("Watchdog", f"ready HTTP {r.status_code}", level="error")
                if restart_cmd:
                    _restart(restart_cmd)
        except Exception as e:
            send_alert("Watchdog", f"ready fail: {e}", level="error")
            if restart_cmd:
                _restart(restart_cmd)
            time.sleep(interval)
            continue

        try:
            wr = httpx.get(wd_url, timeout=to)
            data = wr.json() if wr.status_code == 200 else {}
        except Exception as e:
            send_alert("Watchdog", f"watchdog json fail: {e}", level="warning")
            time.sleep(interval)
            continue

        tick = float(data.get("last_tick_seconds") or 0)
        if max_tick > 0 and tick > max_tick:
            send_alert("Watchdog", f"slow tick {tick}s > {max_tick}s", level="warning")

        age = data.get("seconds_since_last_trade_open")
        if no_trade > 0 and age is not None and float(age) > no_trade:
            send_alert(
                "Watchdog",
                f"no new trade for {float(age):.0f}s (threshold {no_trade:.0f}s)",
                level="info",
            )

        if data.get("trading_paused"):
            logger.info("trading paused: %s", data.get("trading_pause_reason"))

        time.sleep(interval)


def main() -> None:
    s = get_settings()
    if not s.watchdog_enabled:
        logger.error("WATCHDOG_ENABLED=false — выставьте true в .env или запускайте с env")
        sys.exit(1)
    run_loop()


if __name__ == "__main__":
    main()
