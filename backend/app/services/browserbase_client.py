"""
Browserbase: удалённый браузер для сценариев (логин, скрейп с JS).
Заглушка + опциональный Playwright connect при заданных переменных.
"""
from __future__ import annotations

import logging
import os

from app.config import get_settings

logger = logging.getLogger(__name__)


def fetch_rendered_text(url: str, *, timeout_ms: int = 30000) -> str | None:
    """
    Если задан BROWSERBASE_API_KEY и BROWSERBASE_WS_ENDPOINT — подключение через Playwright.
    Иначе None (используйте Firecrawl для статического scrape).
    """
    s = get_settings()
    if not s.browserbase_enabled or not s.browserbase_api_key:
        return None
    ws = (s.browserbase_ws_endpoint or os.environ.get("BROWSERBASE_WS_ENDPOINT") or "").strip()
    if not ws:
        logger.debug("browserbase: no ws endpoint")
        return None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws)
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms)
            txt = page.inner_text("body")
            browser.close()
            return (txt or "")[:12000]
    except ImportError:
        logger.warning("browserbase: install playwright `pip install playwright` and browser")
        return None
    except Exception as e:
        logger.info("browserbase_fetch_failed: %s", type(e).__name__)
        return None
