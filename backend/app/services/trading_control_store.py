"""
Пауза торговли (smart pause / ручная) без миграций БД — JSON + потокобезопасность.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.services.alerts import send_alert

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_lock = threading.Lock()


def equity_return_short_long_ratio(db: "Session") -> float | None:
    """Короткая vol / длинная vol по ряду equity (для smart pause)."""
    from sqlalchemy import desc

    from app.models.orm import EquityPoint

    rows = db.query(EquityPoint.equity).order_by(desc(EquityPoint.ts)).limit(40).all()
    if len(rows) < 20:
        return None
    eq = [float(r[0]) for r in reversed(rows)]
    rets = [(eq[i] - eq[i - 1]) / max(eq[i - 1], 1e-9) for i in range(1, len(eq))]
    if len(rets) < 12:
        return None
    short = rets[-5:]
    long = rets[:-5]
    import statistics

    s_vol = statistics.pstdev(short)
    l_vol = statistics.pstdev(long)
    if l_vol <= 1e-12:
        return None
    return float(s_vol / l_vol)


def _path() -> Path:
    return Path(get_settings().trading_control_path)


def load_control() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {"paused": False, "reason": "", "since_ts": None, "source": ""}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {"paused": False, "reason": "", "since_ts": None, "source": ""}
        d.setdefault("paused", False)
        d.setdefault("reason", "")
        d.setdefault("since_ts", None)
        d.setdefault("source", "")
        return d
    except Exception:
        return {"paused": False, "reason": "", "since_ts": None, "source": ""}


def save_control(payload: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


def set_pause(reason: str, source: str = "manual") -> None:
    with _lock:
        save_control(
            {
                "paused": True,
                "reason": reason,
                "since_ts": time.time(),
                "source": source,
            }
        )


def clear_pause() -> None:
    with _lock:
        save_control({"paused": False, "reason": "", "since_ts": None, "source": ""})


def evaluate_smart_pause(
    *,
    drawdown_pct: float,
    equity_vol_ratio: float | None,
) -> bool:
    """
    Возвращает True, если выставили паузу в этом вызове.
    """
    s = get_settings()
    dd_lim = float(s.smart_pause_drawdown_pct)
    vol_mult = float(s.smart_pause_equity_vol_mult)
    triggered = False
    reason = ""
    if dd_lim > 0 and drawdown_pct >= dd_lim:
        triggered = True
        reason = f"smart_pause_drawdown dd={drawdown_pct:.2f}%>={dd_lim}%"
    if not triggered and vol_mult > 0 and equity_vol_ratio is not None and equity_vol_ratio >= vol_mult:
        triggered = True
        reason = f"smart_pause_equity_vol ratio={equity_vol_ratio:.2f}>={vol_mult}"
    if triggered:
        cur = load_control()
        if not cur.get("paused"):
            set_pause(reason, source="smart")
            send_alert("Smart pause", reason, level="warning")
        return True
    return False


def maybe_clear_smart_pause(
    *,
    drawdown_pct: float,
    equity_vol_ratio: float | None,
) -> None:
    s = get_settings()
    max_sec = float(s.smart_pause_auto_clear_sec)
    if max_sec <= 0:
        return
    cur = load_control()
    if not cur.get("paused") or cur.get("source") != "smart":
        return
    since = cur.get("since_ts")
    if since is None or (time.time() - float(since)) < max_sec:
        return
    dd_lim = float(s.smart_pause_drawdown_pct)
    vol_mult = float(s.smart_pause_equity_vol_mult)
    ok_dd = dd_lim <= 0 or drawdown_pct < dd_lim * 0.75
    ok_vol = vol_mult <= 0 or equity_vol_ratio is None or equity_vol_ratio < vol_mult * 0.7
    if ok_dd and ok_vol:
        clear_pause()
