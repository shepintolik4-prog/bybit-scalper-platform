"""
Детальный CSV-журнал сделок для FULL_AGGRESSIVE_MAX_FLOW (дополнение к JSONL trade_outcomes).
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import get_settings


def _csv_path() -> Path:
    return Path(get_settings().full_aggressive_trade_csv_path)


def _ensure_header(path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()


def log_trade_closed_csv(
    *,
    opened_at: datetime,
    closed_at: datetime,
    symbol: str,
    side: str,
    strategy_id: str,
    entry_reason: str,
    entry_price: float,
    exit_price: float,
    pnl_usdt: float,
    pnl_pct: float,
    rsi: float | None,
    atr_pct: float | None,
    ema20: float | None,
    mode: str,
    trade_id: int,
) -> None:
    s = get_settings()
    if not s.full_aggressive_max_flow:
        return
    path = _csv_path()
    fields = [
        "trade_id",
        "opened_at",
        "closed_at",
        "duration_sec",
        "symbol",
        "side",
        "strategy_id",
        "entry_reason",
        "entry_price",
        "exit_price",
        "pnl_usdt",
        "pnl_pct",
        "rsi",
        "atr_pct",
        "ema20",
        "mode",
    ]
    _ensure_header(path, fields)
    dur = (closed_at - opened_at).total_seconds()
    row = {
        "trade_id": trade_id,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "duration_sec": round(dur, 3),
        "symbol": symbol,
        "side": side,
        "strategy_id": strategy_id,
        "entry_reason": entry_reason,
        "entry_price": round(entry_price, 10),
        "exit_price": round(exit_price, 10),
        "pnl_usdt": round(pnl_usdt, 6),
        "pnl_pct": round(pnl_pct, 6),
        "rsi": "" if rsi is None else round(rsi, 4),
        "atr_pct": "" if atr_pct is None else round(atr_pct, 8),
        "ema20": "" if ema20 is None else round(ema20, 8),
        "mode": mode,
    }
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writerow(row)
        try:
            f.flush()
            os.fsync(f.fileno())
        except OSError:
            pass


def log_trade_json_sidecar(payload: dict[str, Any]) -> None:
    """Доп. JSONL рядом с CSV (расширенный контекст)."""
    s = get_settings()
    if not s.full_aggressive_max_flow:
        return
    path = _csv_path().with_suffix(".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, default=str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
