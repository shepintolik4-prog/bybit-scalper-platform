"""
Сбор текстов новостей через Firecrawl API (scrape).
Без ключа — возвращает пустой контекст.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def fetch_news_markdown(url: str, *, timeout: float = 45.0) -> str | None:
    s = get_settings()
    if not s.firecrawl_api_key or not url.strip():
        return None
    try:
        r = httpx.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Authorization": f"Bearer {s.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={"url": url.strip(), "formats": ["markdown"]},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            return None
        md = (data.get("data") or {}).get("markdown") or ""
        return str(md)[:8000] if md else None
    except Exception as e:
        logger.info("firecrawl_scrape_failed url=%s err=%s", url[:80], type(e).__name__)
        return None


def fetch_multi_urls(urls: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in urls:
        if not u.strip():
            continue
        t = fetch_news_markdown(u.strip())
        if t:
            out[u] = t
    return out


def build_context_from_env() -> dict[str, Any]:
    """NEWS_URLS=url1,url2 — скрейп и склейка для сентимента."""
    s = get_settings()
    urls = [x.strip() for x in s.news_urls.split(",") if x.strip()]
    if not urls:
        return {"texts": [], "titles": [], "by_url": {}}
    by_url = fetch_multi_urls(urls)
    texts = list(by_url.values())
    return {"texts": texts, "titles": list(by_url.keys()), "by_url": by_url}
