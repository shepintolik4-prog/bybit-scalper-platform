"""
Self-improving loop: по закрытым сделкам за окно сдвигаем edge_bias (осторожно).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import TradeRecord
from app.monitoring.prometheus_metrics import self_improve_runs
from app.services.adaptive_state import load_adaptive, save_adaptive

logger = logging.getLogger(__name__)


def run_self_improve(db: Session) -> None:
    s = get_settings()
    if not s.self_improve_enabled:
        return
    state = load_adaptive()
    last_ts = state.get("last_self_improve_ts_unix")
    if last_ts is not None and time.time() - float(last_ts) < s.self_improve_interval_sec:
        return
    window = datetime.utcnow() - timedelta(hours=s.self_improve_window_hours)
    rows = (
        db.query(TradeRecord)
        .filter(TradeRecord.status == "closed", TradeRecord.closed_at.isnot(None), TradeRecord.closed_at >= window)
        .all()
    )
    if len(rows) < s.self_improve_min_trades:
        return
    wins = sum(1 for t in rows if (t.pnl_usdt or 0) > 0)
    wr = wins / len(rows)
    bias = float(state.get("edge_bias", 0.0))
    if wr < s.self_improve_winrate_low:
        bias = min(s.self_improve_bias_max, bias + s.self_improve_bias_step)
        logger.info("self_improve raise edge_bias -> %.4f (winrate=%.2f n=%s)", bias, wr, len(rows))
    elif wr > s.self_improve_winrate_high:
        bias = max(-s.self_improve_bias_max * 0.5, bias - s.self_improve_bias_step * 0.6)
        logger.info("self_improve lower edge_bias -> %.4f (winrate=%.2f n=%s)", bias, wr, len(rows))
    state["edge_bias"] = bias
    state["last_self_improve_ts_unix"] = time.time()
    state["last_winrate"] = wr
    state["last_n"] = len(rows)
    save_adaptive(state)
    self_improve_runs.inc()
