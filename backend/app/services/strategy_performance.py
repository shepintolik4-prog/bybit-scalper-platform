"""
Учёт PnL / winrate по стратегиям; авто-отключение слабых (без правки кода — только data-файл).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from app.config import get_settings

_lock = threading.Lock()
DEFAULT_STATS: dict[str, Any] = {
    "updated_at": 0,
    "strategies": {},
    "disabled": [],
    "boosted_strategies": [],
}


def _path() -> Path:
    return Path(get_settings().strategy_stats_path)


def load_stats() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_STATS))
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return json.loads(json.dumps(DEFAULT_STATS))
        data.setdefault("strategies", {})
        data.setdefault("disabled", [])
        data.setdefault("boosted_strategies", [])
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_STATS))


def _save(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.time()
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


def is_strategy_disabled(strategy_id: str) -> bool:
    with _lock:
        return strategy_id in set(load_stats().get("disabled") or [])


def is_strategy_boosted(strategy_id: str) -> bool:
    with _lock:
        return strategy_id in set(load_stats().get("boosted_strategies") or [])


def _apply_full_aggressive_auto_select(data: dict[str, Any]) -> None:
    """После N сделок: отключить слабые стратегии, усилить топ по PnL."""
    s = get_settings()
    if not s.full_aggressive_max_flow:
        return
    strategies = data.get("strategies") or {}
    total_n = sum(int((strategies.get(k) or {}).get("n", 0)) for k in strategies)
    if total_n < int(s.full_aggressive_auto_select_min_trades):
        return
    disabled = set(data.get("disabled") or [])
    scored: list[tuple[str, int, float, float]] = []
    for sid, st in strategies.items():
        n = int(st.get("n", 0))
        wr = float(st.get("winrate", 0))
        pnl = float(st.get("pnl_usdt", 0))
        scored.append((sid, n, wr, pnl))
        if n >= 12 and wr < float(s.full_aggressive_disable_winrate_below) and pnl < 0:
            disabled.add(sid)
    scored.sort(key=lambda x: x[3], reverse=True)
    frac = float(s.full_aggressive_boost_top_fraction)
    top_k = max(1, int(len(scored) * frac)) if scored else 0
    boosted = [x[0] for x in scored[:top_k] if x[3] > 0 and x[1] >= 8]
    data["disabled"] = sorted(disabled)
    data["boosted_strategies"] = boosted


def record_trade_closed(strategy_id: str, pnl_usdt: float, ok: bool) -> None:
    s = get_settings()
    with _lock:
        data = load_stats()
        st = data["strategies"].get(strategy_id) or {"wins": 0, "losses": 0, "pnl_usdt": 0.0, "n": 0}
        st["n"] = int(st.get("n", 0)) + 1
        if ok:
            st["wins"] = int(st.get("wins", 0)) + 1
        else:
            st["losses"] = int(st.get("losses", 0)) + 1
        st["pnl_usdt"] = float(st.get("pnl_usdt", 0.0)) + float(pnl_usdt)
        n = st["wins"] + st["losses"]
        st["winrate"] = round(st["wins"] / n, 4) if n else 0.0
        data["strategies"][strategy_id] = st

        min_n = int(s.strategy_min_trades_for_disable)
        min_wr = float(s.strategy_min_winrate_disable)
        if n >= min_n and st["winrate"] < min_wr and strategy_id not in data["disabled"]:
            data["disabled"].append(strategy_id)
        _apply_full_aggressive_auto_select(data)
        _save(data)

    try:
        from app.monitoring.prometheus_metrics import refresh_strategy_gauges

        refresh_strategy_gauges()
    except Exception:
        pass


def force_enable_strategy(strategy_id: str) -> None:
    with _lock:
        data = load_stats()
        data["disabled"] = [x for x in (data.get("disabled") or []) if x != strategy_id]
        _save(data)


def summary_for_api() -> dict[str, Any]:
    data = load_stats()
    strategies = data.get("strategies") or {}
    rows = []
    for sid, st in sorted(strategies.items()):
        n = int(st.get("wins", 0)) + int(st.get("losses", 0))
        rows.append(
            {
                "strategy_id": sid,
                "trades": n,
                "wins": int(st.get("wins", 0)),
                "losses": int(st.get("losses", 0)),
                "winrate": st.get("winrate", 0.0),
                "pnl_usdt": round(float(st.get("pnl_usdt", 0.0)), 4),
                "disabled": sid in set(data.get("disabled") or []),
            }
        )
    return {
        "strategies": rows,
        "disabled_list": list(data.get("disabled") or []),
        "boosted_strategies": list(data.get("boosted_strategies") or []),
        "updated_at": data.get("updated_at"),
    }
