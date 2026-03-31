import asyncio
import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import sentry_sdk
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.logging_config import get_logger
from app.ml.features import (
    build_feature_frame,
    build_feature_frame_recovered,
    diagnose_feature_frame_failure,
    feature_frame_fallback_minimum,
    feature_vector_last,
)
from app.ml.lstm_model import LSTMScorer
from app.ml.meta_filter import get_meta_filter
from app.ml.predictor import explanation_to_json, get_predictor
from app.ml.regime import MarketRegime, RegimeSnapshot, classify_regime_row, regime_multipliers, should_trade_regime
from app.models.orm import BotSettings, EquityPoint, Position, TradeRecord
from app.models.trade_state import PositionLifecycleState
from app.monitoring.prometheus_metrics import (
    bot_ticks_total,
    equity_gauge,
    execution_orders_failed_total,
    execution_orders_submitted_total,
    last_trade_open_unix,
    portfolio_drawdown_pct,
    scan_fetch_seconds,
    scan_symbols_gauge,
    seconds_since_last_open,
    signal_rejects,
    signals_candidate_total,
    signals_empty_features_total,
    signals_pipeline_fallback_used_total,
    signals_source_total,
    strategy_signal_total,
    system_health_gauge,
    tick_duration,
    trade_pnl_usdt,
    trades_closed,
    trades_opened,
)
from app.observability.langfuse_client import trace_trade_decision
from app.services import bybit_exchange, market_scanner
from app.services.exchange_sync import (
    attempt_reduce_only_market_close_with_retries,
    fetch_position_marks_for_symbols,
    sync_positions_with_db,
)
from app.services.order_tracker import track_order_status, verify_position_opened_after_order
from app.services.adaptive_state import get_edge_bias
from app.services.autonomy_context import adjust_edge, build_tick_context
from app.services.correlation_service import passes_correlation_gate, total_exposure_ratio
from app.services.decision_audit import build_decision_chain
from app.services.dynamic_risk import combined_risk_multiplier
from app.services.fund_risk import check_fund_limits, rank_candidates_for_multi_execution, vol_scale_from_equity_history
from app.services.execution_model import apply_execution_price
from app.services.risk import (
    atr_invalid_dead,
    compute_position_size,
    default_stops,
    macd_filter_allows_entry,
    passes_volatility_regime,
    update_trail,
)
from app.services.scan_state import append_reject, set_snapshot
from app.services.consistency_checks import run_consistency_checks
from app.services.event_bus import E_ORDER_CREATED, E_ORDER_FILLED, E_POSITION_CLOSED, E_SIGNAL_GENERATED, emit
from app.services.kill_switch import (
    evaluate_kill_switch,
    kill_switch_active,
    recent_equity_atr_spike_ratio,
    record_exchange_failure,
)
from app.services.profit_engine import (
    apply_adaptive_learning,
    apply_profit_scaling,
    compute_performance_metrics,
    diversification_adjust_candidates,
    effective_min_edge,
    effective_min_model_confidence,
)
from app.services.risk_guards import global_guards
from app.services.self_improve import run_self_improve
from app.services.state_manager import (
    can_start_live_entry,
    symbol_operation_lock,
    transition_position_lifecycle,
    transition_trade_record,
)
from app.services.strategy_performance import is_strategy_disabled, record_trade_closed, summary_for_api
from app.services.strategy_selector import StrategySelection, explain_selection, select_strategy_for_regime
from app.services.trade_detail_log import log_trade_closed_csv, log_trade_json_sidecar
from app.services.trade_journal import append_trade_outcome
from app.services.trading_control_store import (
    equity_return_short_long_ratio,
    evaluate_smart_pause,
    load_control,
    maybe_clear_smart_pause,
)
from app.strategies import RULE_STRATEGY_REGISTRY
from app.strategies.fallback_strategy import signal_fallback_from_ohlcv_only
from app.strategies.max_flow_strategies import force_volatility_entry_signal, pick_max_flow_signal

logger = get_logger("bot.engine")


def _side_is_long(side: str | None) -> bool:
    s = (side or "").lower().strip()
    return s in ("buy", "long")


class BotEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.predictor = get_predictor()
        self.meta = get_meta_filter()
        self.lstm = LSTMScorer()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._peak_equity: float | None = None
        self._loss_day: date | None = None
        self._day_start_equity: float | None = None
        self._last_open_ts: float | None = None
        self._bot_started_ts: float = time.time()
        self._last_tick_seconds: float = 0.0
        self._strategy_panel_snapshot: dict[str, Any] = {}
        self._last_exchange_sync_ts: float = 0.0
        self._tick_counter: int = 0
        self._universe_syms_cache: list[str] = []
        self._universe_syms_cache_ts: float = 0.0
        self._scan_panel_overrides: dict[str, Any] | None = None

    def ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def start(self) -> None:
        self.ensure_worker()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _expire_stale_pending_live_trades(self, db: Session, max_sec: float = 900.0) -> None:
        """Pending без подтверждённой позиции не должен блокировать входы бесконечно."""
        cutoff = datetime.utcnow() - timedelta(seconds=max_sec)
        rows = (
            db.query(TradeRecord)
            .filter(
                TradeRecord.mode == "live",
                TradeRecord.status == "pending",
                TradeRecord.opened_at < cutoff,
            )
            .all()
        )
        if not rows:
            return
        for tr in rows:
            tr.status = "failed"
            tr.order_status = "expired"
        db.commit()

    def _loop(self) -> None:
        while not self._stop.is_set():
            db = SessionLocal()
            sleep_sec = float(self.settings.scan_interval_sec)
            try:
                st = db.query(BotSettings).filter_by(id=1).first()
                if not st:
                    sleep_sec = 2.0
                elif st.bot_enabled:
                    self._tick(db, st)
                    run_self_improve(db)
                    apply_adaptive_learning(db)
                else:
                    # Иначе при «Стоп» в UI закрытие по TP/SL никогда не выполняется
                    self._manage_open_positions_only(db, st)
                    sleep_sec = max(2.0, float(self.settings.manage_positions_poll_sec))
            except Exception:
                import traceback

                traceback.print_exc()
                try:
                    record_exchange_failure()
                except Exception:
                    pass
                try:
                    import sentry_sdk

                    sentry_sdk.capture_exception()
                except Exception:
                    pass
            finally:
                db.close()
            time.sleep(sleep_sec)

    def _manage_open_positions_only(self, db: Session, st: BotSettings) -> None:
        """TP/SL/трейлинг и принудительные выходы без скана новых входов (bot_enabled=false)."""
        paper = st.paper_mode
        logger.info(
            "tick_skip_bot_stopped bot_enabled=0 paper=%s — только управление открытыми и universe для UI; "
            "OHLCV/сигналы не считаются (включите бота в панели + X-API-Secret)",
            paper,
        )
        if paper:
            locked = sum(float(p.size_usdt) for p in db.query(Position).filter(Position.mode == "paper").all())
            equity = float(st.virtual_balance) + locked
        else:
            equity = self._fetch_live_equity()
        self._manage_open(db, st, paper, equity)
        self._snapshot_equity(db, st, equity, paper)
        # Дашборд: список символов после фильтра объёма (без OHLCV), иначе «0 символов» при остановленном боте
        syms = self._universe_symbols_for_dashboard(cache_ttl_sec=60.0)
        self._publish_scan_snapshot(
            syms,
            [],
            None,
            self._strategy_panel_snapshot,
            panel_overrides={
                "tick_skip": {
                    "reason": "bot_stopped",
                    "detail_ru": "Бот остановлен (Стоп в настройках): полный тик с свечами не выполняется. "
                    "Ниже — список пар после фильтра ликвидности (как при старте скана).",
                }
            },
        )

    def _update_daily_loss_tracker(self, equity: float) -> float | None:
        today = datetime.utcnow().date()
        if self._loss_day != today:
            self._loss_day = today
            self._day_start_equity = equity
        if self._day_start_equity is None or self._day_start_equity <= 0:
            return None
        return (equity - self._day_start_equity) / self._day_start_equity * 100.0

    def _log_reject(self, symbol: str, reason: str, **extra: Any) -> None:
        payload = {"symbol": symbol, "reason": reason, **extra}
        logger.info("signal_reject %s", json.dumps(payload, ensure_ascii=False, default=str))
        plain: dict[str, Any] = {}
        for k, v in extra.items():
            if isinstance(v, (str, int, float, bool, type(None))):
                plain[k] = v
            elif isinstance(v, list):
                plain[f"{k}_csv"] = ",".join(str(x) for x in v)[:800]
            elif isinstance(v, dict):
                plain[f"{k}_json"] = json.dumps(v, ensure_ascii=False, default=str)[:800]
        try:
            append_reject(symbol, reason, **plain)
        except Exception:
            pass
        try:
            signal_rejects.labels(reason=reason).inc()
        except Exception:
            pass

    def _universe_symbols_for_dashboard(self, *, cache_ttl_sec: float | None = None) -> list[str]:
        """Список символов как перед OHLCV (fetch_all + tickers + filter). Лёгкий запрос для UI."""
        now = time.time()
        if (
            cache_ttl_sec is not None
            and self._universe_syms_cache
            and (now - self._universe_syms_cache_ts) < float(cache_ttl_sec)
        ):
            return list(self._universe_syms_cache)
        s = self.settings
        try:
            symbols_raw = market_scanner.fetch_all_symbols()
            try:
                tickers = market_scanner.fetch_tickers_map()
            except Exception as e:
                logger.warning("fetch_tickers_map (universe): %s", e)
                tickers = {}
            fa = bool(s.full_aggressive_max_flow)
            out = market_scanner.filter_symbols(
                symbols_raw,
                tickers,
                min_quote_volume_usdt=0.0 if fa else float(s.scanner_min_quote_volume_usdt),
            )
        except Exception as e:
            logger.warning("universe_symbols_for_dashboard: %s", e)
            out = []
        self._universe_syms_cache = list(out)
        self._universe_syms_cache_ts = now
        return list(out)

    def _publish_scan_snapshot(
        self,
        symbols: list[str],
        candidates: list[tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]],
        best: tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any] | None,
        strategy_panel: dict[str, Any] | None = None,
        panel_overrides: dict[str, Any] | None = None,
    ) -> None:
        def _sel(x: tuple[float, str, dict[str, Any], dict[str, float], float, Any, Any]) -> float:
            v = x[2].get("selection_score")
            try:
                if v is None:
                    raise TypeError("selection_score is None")
                return float(v)
            except Exception:
                return float(abs(x[0]))

        top_signals: list[dict[str, Any]] = []
        for c in sorted(candidates, key=_sel, reverse=True)[:10]:
            sym = c[1]
            expl = c[2]
            top_signals.append(
                {
                    "symbol": sym,
                    "combined_edge": expl.get("combined_edge"),
                    "regime": expl.get("regime"),
                    "confidence": expl.get("confidence_for_side") or expl.get("confidence"),
                    "liquidity_score": expl.get("liquidity_score"),
                    "risk_score_atr_band": expl.get("risk_score_atr_band"),
                    "composite_score": expl.get("composite_score"),
                    "selection_score": expl.get("selection_score"),
                    "strategy_id": expl.get("strategy_id"),
                }
            )
        sel_c = None
        if best and best[2].get("composite_score") is not None:
            try:
                sel_c = float(best[2]["composite_score"])
            except (TypeError, ValueError):
                sel_c = None
        panel = dict(strategy_panel or {})
        if panel_overrides:
            panel.update(panel_overrides)
        set_snapshot(
            scanned_symbols=list(symbols),
            top_signals=top_signals,
            selected_symbol=best[1] if best else None,
            selected_composite=sel_c,
            strategy_panel=panel,
        )
        try:
            scan_symbols_gauge.set(len(symbols))
            if self._last_open_ts is not None:
                seconds_since_last_open.set(time.time() - self._last_open_ts)
            else:
                seconds_since_last_open.set(-1.0)
        except Exception:
            pass

    def _tick(self, db: Session, st: BotSettings) -> None:
        t0 = time.perf_counter()
        try:
            self._tick_body(db, st)
        finally:
            self._last_tick_seconds = time.perf_counter() - t0
            tick_duration.observe(self._last_tick_seconds)
            try:
                bot_ticks_total.inc()
            except Exception:
                pass

    def _tick_body(self, db: Session, st: BotSettings) -> None:
        symbols: list[str] = []
        candidates: list[
            tuple[float, str, dict[str, Any], dict[str, float], float, MarketRegime, RegimeSnapshot]
        ] = []
        best_out: list[
            tuple[float, str, dict[str, Any], dict[str, float], float, MarketRegime, RegimeSnapshot] | None
        ] = [None]
        try:
            self._tick_body_inner(db, st, symbols, candidates, best_out)
        except Exception:
            logger.exception("tick_body_inner failed; running position maintenance (TP/SL)")
            paper = st.paper_mode
            try:
                if paper:
                    locked = sum(
                        float(p.size_usdt) for p in db.query(Position).filter(Position.mode == "paper").all()
                    )
                    eq = float(st.virtual_balance) + locked
                else:
                    eq = self._fetch_live_equity()
                self._manage_open(db, st, paper, eq)
                self._snapshot_equity(db, st, eq, paper)
            except Exception:
                logger.exception("manage_open after tick failure")
            raise
        finally:
            self._publish_scan_snapshot(
                symbols,
                candidates,
                best_out[0],
                self._strategy_panel_snapshot,
                panel_overrides=self._scan_panel_overrides,
            )
            self._scan_panel_overrides = None

    def _tick_body_inner(
        self,
        db: Session,
        st: BotSettings,
        symbols: list[str],
        candidates: list[
            tuple[float, str, dict[str, Any], dict[str, float], float, MarketRegime, RegimeSnapshot]
        ],
        best_out: list[
            tuple[float, str, dict[str, Any], dict[str, float], float, MarketRegime, RegimeSnapshot] | None
        ],
    ) -> None:
        s = self.settings
        paper = st.paper_mode
        if not paper:
            self._expire_stale_pending_live_trades(db)
        if paper:
            locked = sum(float(p.size_usdt) for p in db.query(Position).filter(Position.mode == "paper").all())
            equity = float(st.virtual_balance) + locked
        else:
            equity = self._fetch_live_equity()
        if equity < s.min_equity_usdt:
            self._manage_open(db, st, paper, equity)
            self._snapshot_equity(db, st, equity, paper)
            symbols[:] = self._universe_symbols_for_dashboard()
            self._scan_panel_overrides = {
                "tick_skip": {
                    "reason": "min_equity",
                    "detail_ru": f"Эквити ниже MIN_EQUITY_USDT ({s.min_equity_usdt}); свечи для сигналов не загружались.",
                }
            }
            return

        peak_db = db.query(func.max(EquityPoint.equity)).scalar()
        self._peak_equity = max(equity, self._peak_equity or 0, float(peak_db or 0))
        dd = (self._peak_equity - equity) / self._peak_equity * 100 if self._peak_equity else 0
        try:
            portfolio_drawdown_pct.set(dd)
        except Exception:
            pass
        vol_ratio_eq = equity_return_short_long_ratio(db)
        maybe_clear_smart_pause(drawdown_pct=dd, equity_vol_ratio=vol_ratio_eq)
        evaluate_smart_pause(drawdown_pct=dd, equity_vol_ratio=vol_ratio_eq)
        try:
            atr_spike = recent_equity_atr_spike_ratio(db, st)
            evaluate_kill_switch(
                db=db,
                st=st,
                paper=paper,
                drawdown_pct=dd,
                equity=equity,
                atr_spike_ratio=atr_spike,
            )
        except Exception as e:
            logger.warning("kill_switch evaluate: %s", e)

        self._tick_counter += 1
        if s.consistency_checks_enabled and self._tick_counter % max(1, int(s.consistency_check_interval_ticks)) == 0:
            try:
                run_consistency_checks(db, st)
            except Exception as e:
                logger.warning("consistency_checks: %s", e)

        # 0 или отрицательное значение в env = лимит max drawdown выключен глобально.
        dd_limit = float(st.max_drawdown_pct)
        if float(s.max_drawdown_pct) <= 0:
            dd_limit = 0.0
        if dd_limit > 0 and dd > dd_limit:
            self._log_reject("ALL", "max_portfolio_drawdown", dd_pct=round(dd, 3))
            self._manage_open(db, st, paper, equity)
            self._snapshot_equity(db, st, equity, paper)
            symbols[:] = self._universe_symbols_for_dashboard()
            self._scan_panel_overrides = {
                "tick_skip": {
                    "reason": "max_drawdown",
                    "detail_ru": f"Просадка портфеля выше лимита ({round(dd, 2)}% > {dd_limit}%); OHLCV не запускался.",
                }
            }
            return

        daily_ret = self._update_daily_loss_tracker(equity)
        # 0 или отрицательное значение = дневной лимит убытка выключен.
        daily_loss_limit_pct = float(s.daily_loss_limit_pct)
        if daily_loss_limit_pct > 0 and daily_ret is not None and daily_ret <= -daily_loss_limit_pct:
            self._log_reject("ALL", "daily_loss_limit", daily_ret_pct=round(daily_ret, 3))
            self._manage_open(db, st, paper, equity)
            self._snapshot_equity(db, st, equity, paper)
            symbols[:] = self._universe_symbols_for_dashboard()
            self._scan_panel_overrides = {
                "tick_skip": {
                    "reason": "daily_loss_limit",
                    "detail_ru": f"Дневной лимит убытка ({round(daily_ret, 2)}%); OHLCV не запускался.",
                }
            }
            return

        pos_mode = "paper" if paper else "live"
        open_n = db.query(Position).filter(Position.mode == pos_mode).count()
        fa = bool(s.full_aggressive_max_flow)
        open_cap = (
            int(s.full_aggressive_max_positions)
            if fa
            else (int(s.scalping_max_open_positions) if s.aggressive_scalping_mode else int(st.max_open_positions))
        )
        if open_n >= open_cap:
            self._manage_open(db, st, paper, equity)
            self._snapshot_equity(db, st, equity, paper)
            symbols[:] = self._universe_symbols_for_dashboard()
            self._scan_panel_overrides = {
                "tick_skip": {
                    "reason": "open_cap",
                    "open_n": open_n,
                    "open_cap": open_cap,
                    "detail_ru": f"Достигнут лимит открытых позиций ({open_n}/{open_cap}) — список пар показан без загрузки свечей.",
                }
            }
            return

        quiet_fb = (
            paper
            and float(s.paper_quiet_fallback_sec) > 0
            and (time.time() - (self._last_open_ts or self._bot_started_ts))
            >= float(s.paper_quiet_fallback_sec)
        )
        if quiet_fb:
            self._scan_panel_overrides = {
                "quiet_fallback_active": True,
                "detail_ru": "Paper: долго без новой сделки — на этот тик ослаблены edge/conf/min_ohlcv/meta.",
            }
            logger.warning(
                "paper_quiet_fallback_engaged sec_since_last_open=%s",
                round(time.time() - (self._last_open_ts or self._bot_started_ts), 1),
            )
        else:
            self._scan_panel_overrides = None

        symbols_raw = market_scanner.fetch_all_symbols()
        try:
            tickers = market_scanner.fetch_tickers_map()
        except Exception as e:
            logger.warning("fetch_tickers_map failed: %s", e)
            tickers = {}
        symbols[:] = market_scanner.filter_symbols(
            symbols_raw,
            tickers,
            min_quote_volume_usdt=0.0 if fa else float(s.scanner_min_quote_volume_usdt),
        )
        logger.info(
            "scan_symbols_filtered raw=%s after_volume_filter=%s scan_all_usdt_perp=%s max_symbols=%s",
            len(symbols_raw),
            len(symbols),
            s.scan_all_usdt_perpetual,
            s.scan_all_max_symbols,
        )
        if fa:
            scan_tf = str(s.full_aggressive_scan_timeframe)
            scan_lim = int(s.full_aggressive_ohlcv_limit)
        elif s.aggressive_scalping_mode:
            scan_tf = str(s.scalping_scan_timeframe)
            scan_lim = int(s.scalping_ohlcv_limit)
        else:
            scan_tf = "5m"
            scan_lim = 280
        t_fetch = time.perf_counter()
        try:
            ohlcv_by_sym = asyncio.run(
                market_scanner.scan_symbols_parallel(
                    symbols,
                    timeframe=scan_tf,
                    limit=scan_lim,
                    max_concurrency=int(s.scanner_max_parallel),
                )
            )
        except RuntimeError:
            ohlcv_by_sym = {sym: market_scanner.fetch_ohlcv_cached(sym, scan_tf, scan_lim) for sym in symbols}
        try:
            scan_fetch_seconds.observe(time.perf_counter() - t_fetch)
        except Exception:
            pass

        risk_m = combined_risk_multiplier(equity, self._peak_equity or equity, s.paper_initial_balance)
        tick_ctx = build_tick_context()
        edge_bias = get_edge_bias()
        open_positions = db.query(Position).filter(Position.mode == pos_mode).all()
        open_syms = [p.symbol for p in open_positions]
        pos_tuples = [(float(p.size_usdt), int(p.leverage)) for p in open_positions]
        open_triples = [(p.symbol, float(p.size_usdt), int(p.leverage)) for p in open_positions]

        eff_edge_mult = (
            0.22
            if fa
            else (0.38 if s.aggressive_scalping_mode else (0.72 if s.aggressive_mode else 1.0))
        )
        eff_liquidity_ref = float(s.scanner_liquidity_ref_quote_vol) * (
            0.15 if fa else (0.28 if s.aggressive_scalping_mode else (0.45 if s.aggressive_mode else 1.0))
        )
        meta_floor_delta = 0.22 if s.aggressive_scalping_mode else (0.14 if s.aggressive_mode else 0.0)
        min_ohlcv_bars = (
            int(s.full_aggressive_min_ohlcv_bars)
            if fa
            else (
                int(s.scalping_min_ohlcv_bars)
                if s.aggressive_scalping_mode
                else (72 if s.aggressive_mode else 100)
            )
        )
        min_feat_rows = (
            max(int(s.full_aggressive_feature_min_rows), 12)
            if fa
            else max(22, int(s.feature_frame_min_rows))
        )
        if quiet_fb and not fa:
            min_ohlcv_bars = max(24, int(min_ohlcv_bars * 0.65))
            min_feat_rows = max(18, int(min_feat_rows * 0.82))

        nonempty_ohlcv = sum(1 for v in ohlcv_by_sym.values() if v)
        meets_min_pre = sum(1 for v in ohlcv_by_sym.values() if v and len(v) >= min_ohlcv_bars)
        logger.info(
            "scan_cycle_ohlcv symbols=%s ohlcv_nonempty=%s ohlcv_meets_min_bars=%s min_bars=%s tf=%s quiet_fb=%s",
            len(symbols),
            nonempty_ohlcv,
            meets_min_pre,
            min_ohlcv_bars,
            scan_tf,
            quiet_fb,
        )
        if nonempty_ohlcv > 0:
            logger.info(
                "TRADING_ENGINE_ACTIVE paper=%s bot_enabled=1 symbols=%s ohlcv_nonempty=%s",
                paper,
                len(symbols),
                nonempty_ohlcv,
            )

        force_trade_armed = False
        if s.aggressive_scalping_mode and float(s.scalping_force_trade_after_minutes) > 0:
            ref_ts = self._last_open_ts if self._last_open_ts is not None else self._bot_started_ts
            if time.time() - float(ref_ts) >= float(s.scalping_force_trade_after_minutes) * 60.0:
                force_trade_armed = True
        ref_ts_fa = self._last_open_ts if self._last_open_ts is not None else self._bot_started_ts
        force_trade_armed_fa = bool(
            fa and s.full_aggressive_force_trade and (time.time() - float(ref_ts_fa) >= float(s.full_aggressive_force_trade_sec))
        )
        weak_bucket: list[dict[str, Any]] = []
        weak_bucket_fa: list[dict[str, Any]] = []

        router_counts: dict[str, int] = {}
        for sym in symbols:
            raw = ohlcv_by_sym.get(sym, [])
            if not raw:
                self._log_reject(sym, "ohlcv_empty", n=0)
                continue
            if len(raw) < min_ohlcv_bars:
                self._log_reject(sym, "insufficient_bars", n=len(raw), need=min_ohlcv_bars)
                continue
            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
            _last_c = float(pd.to_numeric(df["close"].iloc[-1], errors="coerce") or 0.0)
            _last_v = float(pd.to_numeric(df["volume"].iloc[-1], errors="coerce") or 0.0)
            if (
                not fa
                and float(s.scanner_min_close_usdt) > 0
                and _last_c > 0
                and _last_c < float(s.scanner_min_close_usdt)
            ):
                self._log_reject(
                    sym,
                    "skip_junk_instrument",
                    reason_kind="low_close",
                    close=round(_last_c, 10),
                    threshold=float(s.scanner_min_close_usdt),
                )
                continue
            if not fa and float(s.scanner_min_last_bar_volume) > 0 and _last_v < float(s.scanner_min_last_bar_volume):
                self._log_reject(
                    sym,
                    "skip_junk_instrument",
                    reason_kind="low_last_volume",
                    volume=round(_last_v, 6),
                    threshold=float(s.scanner_min_last_bar_volume),
                )
                continue
            forced_fallback = False
            fb_sig_cached = None
            strict_df = build_feature_frame(df)
            recovered_df = build_feature_frame_recovered(df)
            if len(strict_df) >= min_feat_rows:
                feat_df = strict_df
            elif len(recovered_df) >= min_feat_rows:
                feat_df = recovered_df
            else:
                diag = diagnose_feature_frame_failure(df)
                feat_df = None
                if s.fallback_rule_enabled:
                    fb_sig_cached = signal_fallback_from_ohlcv_only(df)
                    if fb_sig_cached is not None:
                        forced_fallback = True
                        if len(recovered_df) >= 1:
                            feat_df = recovered_df
                        elif len(strict_df) >= 1:
                            feat_df = strict_df
                        else:
                            feat_df = feature_frame_fallback_minimum(df)
                if feat_df is None or len(feat_df) < 1:
                    self._log_reject(
                        sym,
                        "empty_features",
                        ohlcv_len=diag.get("ohlcv_len"),
                        nan_pct=diag.get("nan_pct_close"),
                        columns=list(diag.get("columns") or []),
                        stage=str(diag.get("stage")),
                        feat_rows_strict=diag.get("feat_rows_strict"),
                        feat_rows_recovered=diag.get("feat_rows_recovered"),
                        error=diag.get("error"),
                    )
                    try:
                        signals_empty_features_total.inc()
                    except Exception:
                        pass
                    continue
                if forced_fallback:
                    try:
                        signals_pipeline_fallback_used_total.inc()
                    except Exception:
                        pass
                    logger.info(
                        "feature_pipeline_recovered %s",
                        json.dumps(
                            {
                                "symbol": sym,
                                "ohlcv_len": diag.get("ohlcv_len"),
                                "nan_pct": diag.get("nan_pct_close"),
                                "stage": str(diag.get("stage")),
                                "feat_rows_strict": diag.get("feat_rows_strict"),
                                "feat_rows_recovered": diag.get("feat_rows_recovered"),
                                "by": "fallback_technical",
                            },
                            ensure_ascii=False,
                            default=str,
                        ),
                    )

            row_s = feat_df.iloc[-1]
            snap = classify_regime_row(row_s)
            reg = snap.regime
            if (
                not fa
                and not should_trade_regime(reg)
                and not (s.aggressive_mode and forced_fallback)
                and not s.aggressive_scalping_mode
            ):
                self._log_reject(sym, "regime_flat_skip", regime=reg.value)
                continue

            feats = feature_vector_last(feat_df)
            atr_pct = float(feats["atr_pct"])
            atr14_val = float(feats.get("atr14") or 0.0)
            last_close_atr = float(pd.to_numeric(df["close"].iloc[-1], errors="coerce") or 0.0)
            atr_score_sel = market_scanner.compute_atr_soft_score(
                atr_pct,
                opt_lo=float(s.atr_soft_opt_min_pct),
                opt_hi=float(s.atr_soft_opt_max_pct),
                floor=float(s.atr_soft_score_floor),
            )
            if fa:
                if atr_invalid_dead(atr_pct):
                    self._log_reject(
                        sym,
                        "atr_regime",
                        atr_pct=round(atr_pct, 12),
                        decision="reject_dead_atr",
                        mode="max_flow",
                    )
                    continue
            elif s.aggressive_scalping_mode:
                if atr_invalid_dead(atr_pct):
                    self._log_reject(
                        sym,
                        "atr_regime",
                        atr_pct=round(atr_pct, 12),
                        atr=round(atr14_val, 12),
                        close=round(last_close_atr, 10),
                        decision="reject_dead_atr",
                        mode="scalping",
                    )
                    continue
            elif s.use_atr_filter and not passes_volatility_regime(atr_pct) and not (s.aggressive_mode and forced_fallback):
                self._log_reject(
                    sym,
                    "atr_regime",
                    atr_pct=round(atr_pct, 10),
                    atr=round(atr14_val, 10),
                    close=round(last_close_atr, 10),
                    atr_score=round(atr_score_sel, 6),
                    decision="reject",
                    use_atr_filter=True,
                )
                continue
            _atr_decision = "score_soft" if not s.use_atr_filter else "pass_hard_filter"
            logger.debug(
                "atr_decision %s",
                json.dumps(
                    {
                        "symbol": sym,
                        "atr_pct": atr_pct,
                        "atr": atr14_val,
                        "close": last_close_atr,
                        "atr_score": round(atr_score_sel, 6),
                        "decision": _atr_decision,
                        "use_atr_filter": s.use_atr_filter,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )

            if fa:
                mf = pick_max_flow_signal(df, feats)
                if mf is None or is_strategy_disabled(mf.strategy_id):
                    if force_trade_armed_fa:
                        weak_bucket_fa.append(
                            {
                                "sym": sym,
                                "df": df,
                                "feats": feats,
                                "reg": reg,
                                "snap": snap,
                                "tick_ctx": tick_ctx,
                                "tickers": tickers,
                                "edge_bias": edge_bias,
                                "atr_score_sel": atr_score_sel,
                            }
                        )
                    continue
                sid = mf.strategy_id
                router_counts[sid] = router_counts.get(sid, 0) + 1
                try:
                    strategy_signal_total.labels(strategy_id=sid).inc()
                except Exception:
                    pass
                combined_adj = float(mf.edge)
                conf = min(0.95, max(float(mf.confidence), float(s.full_aggressive_min_conf)))
                tp_scale = float(mf.tp_scale)
                rule_extra = dict(mf.details or {})
                need_mf = float(s.full_aggressive_need_edge)
                if abs(combined_adj) < need_mf:
                    if force_trade_armed_fa:
                        weak_bucket_fa.append(
                            {
                                "sym": sym,
                                "df": df,
                                "feats": feats,
                                "reg": reg,
                                "snap": snap,
                                "tick_ctx": tick_ctx,
                                "tickers": tickers,
                                "edge_bias": edge_bias,
                                "atr_score_sel": atr_score_sel,
                            }
                        )
                    self._log_reject(
                        sym,
                        "low_edge_max_flow",
                        combined=round(combined_adj, 5),
                        need=need_mf,
                        strategy=sid,
                    )
                    continue
                sel = StrategySelection(
                    strategy_id=sid,
                    reason_ru=f"MAX FLOW: {rule_extra.get('entry_reason', sid)}",
                    regime=reg.value,
                    meta={"max_flow": True, "entry_reason": rule_extra.get("entry_reason")},
                )
                p_down, p_up = self.predictor.predict_proba_row(feats)
                expl = self.predictor.explain(feats, p_down, p_up)
                expl["strategy_id"] = sid
                expl["strategy_selection"] = explain_selection(sel)
                expl["strategy_tp_scale"] = tp_scale
                expl["rule_strategy_details"] = rule_extra
                expl["combined_edge_raw"] = round(abs(combined_adj), 5)
                expl["combined_edge"] = round(combined_adj, 5)
                expl["edge_bias_applied"] = round(edge_bias, 5)
                expl["entry_reason"] = rule_extra.get("entry_reason")
                expl["max_flow_mode"] = True
                expl["tick_context"] = {
                    "news_enabled": tick_ctx.get("news_enabled"),
                    "sentiment": tick_ctx.get("sentiment"),
                    "news_titles": (tick_ctx.get("news") or {}).get("titles", [])[:5],
                }
                expl["atr_pct"] = round(atr_pct, 6)
                expl["regime"] = reg.value
                expl["adx"] = round(snap.adx, 4)
                expl["vol_cluster_ratio"] = round(snap.vol_cluster_ratio, 4)
                last_mid = float(df["close"].iloc[-1])
                qv = market_scanner.quote_volume_usdt(tickers, sym)
                liq = market_scanner.liquidity_score_from_quote_vol(qv, ref=float(eff_liquidity_ref))
                composite = abs(float(combined_adj)) * float(conf) * liq * float(atr_score_sel)
                selection_score = composite if s.selection_use_composite else abs(float(combined_adj))
                expl["confidence_for_side"] = round(conf, 4)
                expl["liquidity_score"] = round(liq, 4)
                expl["atr_score"] = round(float(atr_score_sel), 4)
                expl["risk_score_atr_band"] = round(float(atr_score_sel), 4)
                expl["composite_score"] = round(composite, 6)
                expl["selection_score"] = round(selection_score, 6)
                expl["quote_volume_24h_proxy"] = round(qv, 2)
                expl["feature_pipeline"] = "full_aggressive_max_flow"
                expl["filters_relaxed"] = {
                    "meta_skipped": True,
                    "correlation_skipped": True,
                    "macd_skipped": True,
                    "fund_may_skip": bool(s.full_aggressive_skip_fund_limits),
                }
                try:
                    signals_candidate_total.inc()
                    signals_source_total.labels(source=sid).inc()
                except Exception:
                    pass
                candidates.append((combined_adj, sym, expl, feats, last_mid, reg, snap))
                continue

            p_down, p_up = self.predictor.predict_proba_row(feats)
            ml_hint = max(float(p_down), float(p_up))

            rule_sig = None
            forced_scalp = False
            scalp_fn = RULE_STRATEGY_REGISTRY.get("aggressive_scalp")
            if s.aggressive_scalping_mode and scalp_fn is not None:
                scalp_sig = scalp_fn(df, feats, snap, reg)
                if scalp_sig is not None:
                    forced_scalp = True
                    sid = "aggressive_scalp"
                    sel = StrategySelection(
                        strategy_id=sid,
                        reason_ru="Агрессивный скальпинг: RSI/EMA/микротренд.",
                        regime=reg.value,
                        meta={"scalping": True, "entry": (scalp_sig.details or {}).get("entry_reasons")},
                    )
                    rule_sig = scalp_sig
            if not forced_scalp and forced_fallback:
                sid = "fallback_technical"
                sel = StrategySelection(
                    strategy_id=sid,
                    reason_ru="Резерв: мало валидных строк фич (strict/recovered) — технические правила.",
                    regime=reg.value,
                    meta={"pipeline": "fallback_technical", "forced": True},
                )
                rule_sig = fb_sig_cached
            elif not forced_scalp:
                sel = select_strategy_for_regime(reg, snap, atr_pct, ml_confidence_hint=ml_hint)
                sid = sel.strategy_id
                if is_strategy_disabled(sid):
                    blocked = sid
                    sel = StrategySelection(
                        strategy_id="ml_hybrid",
                        reason_ru=f"Стратегия {blocked} отключена статистикой — fallback ML.",
                        regime=reg.value,
                        meta={"blocked": blocked},
                    )
                    sid = "ml_hybrid"

            router_counts[sid] = router_counts.get(sid, 0) + 1
            try:
                strategy_signal_total.labels(strategy_id=sid).inc()
            except Exception:
                pass

            if not forced_fallback and not forced_scalp:
                rule_sig = None
                if sid != "ml_hybrid":
                    fn = RULE_STRATEGY_REGISTRY.get(sid)
                    rule_sig = fn(df, feats, snap, reg) if fn else None
                    if rule_sig is None:
                        att = sid
                        sel = StrategySelection(
                            strategy_id="ml_hybrid",
                            reason_ru=f"Rule {att} без сигнала — fallback ML.",
                            regime=reg.value,
                            meta={"attempted_router": att},
                        )
                        sid = "ml_hybrid"

            sentiment_f = tick_ctx.get("sentiment")
            lstm_info: dict[str, Any] = {}
            combined = 0.0
            tp_scale = 1.0
            skip_macd = False
            rule_extra: dict[str, Any] = {}

            if sid == "ml_hybrid":
                closes = df["close"].astype(float).tolist()
                vols = df["volume"].astype(float).tolist()
                lstm_info = self.lstm.score(closes, vols)
                xgb_edge = self.predictor.edge_score(p_down, p_up)
                combined = xgb_edge
                if lstm_info.get("lstm_p_up") is not None:
                    lstm_edge = float(lstm_info["lstm_p_up"]) - 0.5
                    combined = xgb_edge * 0.65 + lstm_edge * 0.7
                combined_adj = adjust_edge(combined, sentiment_f, s)
                side = "buy" if combined_adj > 0 else "sell"
                conf = p_up if side == "buy" else p_down
            else:
                assert rule_sig is not None
                combined_adj = float(rule_sig.edge)
                conf = float(rule_sig.confidence)
                side = rule_sig.side
                tp_scale = float(rule_sig.tp_scale)
                skip_macd = bool(rule_sig.skip_macd)
                rule_extra = dict(rule_sig.details)
                combined = abs(combined_adj)

            macd_hist_prev = float(feat_df["macd_hist"].iloc[-2]) if len(feat_df) >= 2 else None
            macd_hist_prev2 = float(feat_df["macd_hist"].iloc[-3]) if len(feat_df) >= 3 else None

            rm = regime_multipliers(reg)
            base_need_edge = s.signal_min_edge * float(rm["edge"]) * eff_edge_mult + edge_bias
            if s.aggressive_scalping_mode:
                base_need_edge *= float(s.scalping_signal_min_edge_mult)
            need_edge = effective_min_edge(db, s, base_need_edge, dd, sid)
            if quiet_fb and not fa:
                need_edge = max(0.0, float(need_edge) * 0.5)
            if abs(combined_adj) < need_edge:
                if s.aggressive_scalping_mode and force_trade_armed:
                    if abs(combined_adj) >= need_edge * float(s.scalping_force_trade_edge_mult) and conf >= float(
                        s.scalping_force_trade_conf_floor
                    ):
                        weak_bucket.append(
                            {
                                "sym": sym,
                                "combined_adj": combined_adj,
                                "feats": feats,
                                "reg": reg,
                                "snap": snap,
                                "last_mid": float(df["close"].iloc[-1]),
                                "conf": conf,
                                "sid": sid,
                                "p_down": p_down,
                                "p_up": p_up,
                                "atr_pct": atr_pct,
                                "atr_score_sel": atr_score_sel,
                                "tp_scale": tp_scale,
                                "rule_extra": dict(rule_extra),
                                "lstm_info": dict(lstm_info),
                                "sel": sel,
                                "tick_ctx": tick_ctx,
                                "sentiment_f": sentiment_f,
                                "edge_bias": edge_bias,
                                "side": side,
                            }
                        )
                self._log_reject(
                    sym,
                    "low_edge",
                    combined=round(combined_adj, 5),
                    need=need_edge,
                    regime=reg.value,
                    strategy=sid,
                )
                continue

            min_conf = (
                float(s.scalping_min_conf_floor)
                if s.aggressive_scalping_mode
                else effective_min_model_confidence(s)
            )
            if quiet_fb and not s.aggressive_scalping_mode and not fa:
                min_conf = max(0.28, float(min_conf) - 0.12)
            if conf < min_conf:
                self._log_reject(sym, "low_confidence", conf=round(conf, 4), need=round(min_conf, 4), strategy=sid)
                continue
            if not skip_macd and not macd_filter_allows_entry(
                side, float(feats["macd_hist"]), macd_hist_prev, macd_hist_prev2
            ):
                self._log_reject(sym, "macd_mismatch", side=side, strategy=sid)
                continue

            meta_p_trade_val: float | None = None
            if s.meta_enabled and not (s.aggressive_scalping_mode and s.scalping_skip_meta) and not (
                quiet_fb and not fa
            ):
                p_meta0, p_meta1 = self.meta.predict_trade_proba(feats, reg, combined_adj)
                p_trade = p_meta1 if len([p_meta0, p_meta1]) == 2 else 0.5
                meta_p_trade_val = float(p_trade)
                meta_thr = max(0.28, float(s.meta_min_p_trade) - meta_floor_delta)
                if forced_fallback and s.aggressive_mode:
                    meta_thr = max(0.22, meta_thr - 0.1)
                if p_trade < meta_thr:
                    self._log_reject(sym, "meta_filter", p_trade=round(p_trade, 4), regime=reg.value, need=round(meta_thr, 4))
                    continue

            ok_c, cmsg = True, ""
            if not (s.aggressive_scalping_mode and s.scalping_skip_correlation):
                ok_c, cmsg = passes_correlation_gate(sym, open_syms, symbols)
            if not ok_c:
                self._log_reject(sym, "correlation", detail=cmsg)
                continue

            expl = self.predictor.explain(feats, p_down, p_up)
            expl.update(lstm_info)
            expl["strategy_id"] = sid
            expl["strategy_selection"] = explain_selection(sel)
            expl["strategy_tp_scale"] = tp_scale
            if rule_extra:
                expl["rule_strategy_details"] = rule_extra
            expl["combined_edge_raw"] = round(combined, 5)
            expl["combined_edge"] = round(combined_adj, 5)
            expl["edge_bias_applied"] = round(edge_bias, 5)
            expl["tick_context"] = {
                "news_enabled": tick_ctx.get("news_enabled"),
                "sentiment": sentiment_f,
                "news_titles": (tick_ctx.get("news") or {}).get("titles", [])[:5],
            }
            if meta_p_trade_val is not None:
                expl["meta_p_trade"] = round(meta_p_trade_val, 4)
            expl["atr_pct"] = round(atr_pct, 6)
            expl["regime"] = reg.value
            expl["adx"] = round(snap.adx, 4)
            expl["vol_cluster_ratio"] = round(snap.vol_cluster_ratio, 4)
            last_mid = float(df["close"].iloc[-1])
            qv = market_scanner.quote_volume_usdt(tickers, sym)
            liq = market_scanner.liquidity_score_from_quote_vol(
                qv,
                ref=float(eff_liquidity_ref),
            )
            composite = abs(float(combined_adj)) * float(conf) * liq * float(atr_score_sel)
            selection_score = composite if s.selection_use_composite else abs(float(combined_adj))
            expl["confidence_for_side"] = round(conf, 4)
            expl["liquidity_score"] = round(liq, 4)
            expl["atr_score"] = round(float(atr_score_sel), 4)
            expl["risk_score_atr_band"] = round(float(atr_score_sel), 4)
            expl["composite_score"] = round(composite, 6)
            expl["selection_score"] = round(selection_score, 6)
            expl["quote_volume_24h_proxy"] = round(qv, 2)
            expl["feature_pipeline"] = (
                "aggressive_scalp"
                if forced_scalp
                else ("fallback_forced" if forced_fallback else "strict_or_recovered")
            )
            if s.aggressive_scalping_mode:
                expl["aggressive_scalping"] = True
                expl["scalp_edge_threshold"] = round(float(need_edge), 6)
                expl["scalp_min_conf_used"] = round(float(min_conf), 4)
                expl["filters_relaxed"] = {
                    "meta_skipped": bool(s.scalping_skip_meta and s.meta_enabled),
                    "correlation_skipped": bool(s.scalping_skip_correlation),
                    "macd_relaxed": bool(s.scalping_relax_macd),
                    "atr_only_dead_check": True,
                    "scan_timeframe": scan_tf,
                }
            try:
                signals_candidate_total.inc()
                signals_source_total.labels(source=str(expl.get("strategy_id") or "unknown")).inc()
            except Exception:
                pass
            candidates.append((combined_adj, sym, expl, feats, last_mid, reg, snap))
            if s.aggressive_scalping_mode:
                logger.info(
                    "signal_accepted %s",
                    json.dumps(
                        {
                            "symbol": sym,
                            "strategy_id": sid,
                            "side": side,
                            "combined_edge": round(combined_adj, 5),
                            "confidence": round(conf, 4),
                            "need_edge": round(need_edge, 5),
                            "min_conf_used": round(min_conf, 4),
                            "selection_score": round(float(expl.get("selection_score", 0)), 6),
                            "entry_detail": rule_extra if sid == "aggressive_scalp" else None,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )

        if (
            s.aggressive_scalping_mode
            and not candidates
            and weak_bucket
            and force_trade_armed
        ):
            w = max(
                weak_bucket,
                key=lambda z: abs(float(z["combined_adj"])) * float(z["conf"]) * float(z["atr_score_sel"]),
            )
            sym_f = str(w["sym"])
            combined_f = float(w["combined_adj"])
            feats_f = w["feats"]
            reg_f = w["reg"]
            snap_f = w["snap"]
            last_mid_f = float(w["last_mid"])
            conf_f = float(w["conf"])
            sid_f = str(w["sid"])
            p_down_f = w["p_down"]
            p_up_f = w["p_up"]
            atr_pct_f = float(w["atr_pct"])
            atr_score_f = float(w["atr_score_sel"])
            tp_scale_f = float(w["tp_scale"])
            rule_extra_f = w["rule_extra"]
            lstm_f = w["lstm_info"]
            sel_f = w["sel"]
            sentiment_ff = w["sentiment_f"]
            edge_bias_f = float(w["edge_bias"])
            tick_ctx_f = w.get("tick_ctx") or tick_ctx
            expl_f = self.predictor.explain(feats_f, p_down_f, p_up_f)
            expl_f.update(lstm_f)
            expl_f["strategy_id"] = sid_f
            expl_f["strategy_selection"] = explain_selection(sel_f)
            expl_f["strategy_tp_scale"] = tp_scale_f
            if rule_extra_f:
                expl_f["rule_strategy_details"] = rule_extra_f
            expl_f["combined_edge_raw"] = round(abs(combined_f), 5)
            expl_f["combined_edge"] = round(combined_f, 5)
            expl_f["edge_bias_applied"] = round(edge_bias_f, 5)
            expl_f["tick_context"] = {
                "news_enabled": tick_ctx_f.get("news_enabled"),
                "sentiment": sentiment_ff,
                "news_titles": (tick_ctx_f.get("news") or {}).get("titles", [])[:5],
            }
            expl_f["force_trade"] = True
            expl_f["weakened_conditions"] = ["edge_threshold", "force_after_idle"]
            expl_f["atr_pct"] = round(atr_pct_f, 6)
            expl_f["regime"] = reg_f.value
            expl_f["adx"] = round(snap_f.adx, 4)
            expl_f["vol_cluster_ratio"] = round(snap_f.vol_cluster_ratio, 4)
            qv_f = market_scanner.quote_volume_usdt(tickers, sym_f)
            liq_f = market_scanner.liquidity_score_from_quote_vol(
                qv_f,
                ref=float(eff_liquidity_ref),
            )
            composite_f = abs(combined_f) * conf_f * liq_f * atr_score_f
            expl_f["confidence_for_side"] = round(conf_f, 4)
            expl_f["liquidity_score"] = round(liq_f, 4)
            expl_f["atr_score"] = round(atr_score_f, 4)
            expl_f["risk_score_atr_band"] = round(atr_score_f, 4)
            expl_f["composite_score"] = round(composite_f, 6)
            expl_f["selection_score"] = round(composite_f, 6) if s.selection_use_composite else round(abs(combined_f), 6)
            expl_f["quote_volume_24h_proxy"] = round(qv_f, 2)
            expl_f["feature_pipeline"] = "force_trade_promoted"
            expl_f["aggressive_scalping"] = True
            candidates.append((combined_f, sym_f, expl_f, feats_f, last_mid_f, reg_f, snap_f))
            logger.info(
                "force_trade_promoted %s",
                json.dumps(
                    {"symbol": sym_f, "combined_edge": round(combined_f, 5), "conf": round(conf_f, 4)},
                    ensure_ascii=False,
                ),
            )

        if (
            fa
            and not candidates
            and weak_bucket_fa
            and force_trade_armed_fa
            and s.full_aggressive_force_trade
        ):
            w = max(weak_bucket_fa, key=lambda z: float(z["feats"].get("atr_pct", 0)))
            sym_f = str(w["sym"])
            df_f = w["df"]
            feats_f = w["feats"]
            reg_f = w["reg"]
            snap_f = w["snap"]
            mf = force_volatility_entry_signal(df_f, feats_f)
            if not is_strategy_disabled(mf.strategy_id):
                combined_f = float(mf.edge)
                conf_f = float(mf.confidence)
                tp_scale_f = float(mf.tp_scale)
                rule_extra_f = dict(mf.details or {})
                last_mid_f = float(df_f["close"].iloc[-1])
                p_down_f, p_up_f = self.predictor.predict_proba_row(feats_f)
                sel_f = StrategySelection(
                    strategy_id=mf.strategy_id,
                    reason_ru="MAX FLOW: принудительный вход (нет сигналов, таймаут простоя).",
                    regime=reg_f.value,
                    meta={"max_flow_force": True},
                )
                expl_f = self.predictor.explain(feats_f, p_down_f, p_up_f)
                expl_f["strategy_id"] = mf.strategy_id
                expl_f["strategy_selection"] = explain_selection(sel_f)
                expl_f["strategy_tp_scale"] = tp_scale_f
                expl_f["rule_strategy_details"] = rule_extra_f
                expl_f["combined_edge_raw"] = round(abs(combined_f), 5)
                expl_f["combined_edge"] = round(combined_f, 5)
                expl_f["edge_bias_applied"] = round(float(w["edge_bias"]), 5)
                expl_f["force_trade"] = True
                expl_f["max_flow_mode"] = True
                tick_ctx_f = w.get("tick_ctx") or tick_ctx
                expl_f["tick_context"] = {
                    "news_enabled": tick_ctx_f.get("news_enabled"),
                    "sentiment": tick_ctx_f.get("sentiment"),
                    "news_titles": (tick_ctx_f.get("news") or {}).get("titles", [])[:5],
                }
                atr_pct_f = float(feats_f.get("atr_pct", 0))
                atr_score_f = float(w["atr_score_sel"])
                expl_f["atr_pct"] = round(atr_pct_f, 6)
                expl_f["regime"] = reg_f.value
                expl_f["adx"] = round(snap_f.adx, 4)
                expl_f["vol_cluster_ratio"] = round(snap_f.vol_cluster_ratio, 4)
                qv_f = market_scanner.quote_volume_usdt(tickers, sym_f)
                liq_f = market_scanner.liquidity_score_from_quote_vol(
                    qv_f,
                    ref=float(eff_liquidity_ref),
                )
                composite_f = abs(combined_f) * conf_f * liq_f * atr_score_f
                expl_f["confidence_for_side"] = round(conf_f, 4)
                expl_f["liquidity_score"] = round(liq_f, 4)
                expl_f["atr_score"] = round(atr_score_f, 4)
                expl_f["risk_score_atr_band"] = round(atr_score_f, 4)
                expl_f["composite_score"] = round(composite_f, 6)
                expl_f["selection_score"] = round(composite_f, 6) if s.selection_use_composite else round(
                    abs(combined_f), 6
                )
                expl_f["quote_volume_24h_proxy"] = round(qv_f, 2)
                expl_f["feature_pipeline"] = "full_aggressive_force_trade"
                candidates.append((combined_f, sym_f, expl_f, feats_f, last_mid_f, reg_f, snap_f))
                logger.info(
                    "max_flow_force_trade %s",
                    json.dumps({"symbol": sym_f, "strategy_id": mf.strategy_id}, ensure_ascii=False),
                )

        if (
            candidates
            and s.profit_diversification_enabled
            and not fa
            and not (s.aggressive_scalping_mode and s.scalping_skip_profit_diversification)
        ):
            candidates = diversification_adjust_candidates(candidates, open_syms, symbols, s)

        mode_api = "paper" if paper else "live"
        try:
            perf_met = compute_performance_metrics(
                db, mode=mode_api, hours=int(s.performance_metrics_window_hours)
            )
        except Exception:
            perf_met = {}

        self._strategy_panel_snapshot = {
            "router_counts": dict(sorted(router_counts.items(), key=lambda x: -x[1])),
            "trading_control": load_control(),
            "drawdown_pct": round(dd, 4),
            "performance": summary_for_api(),
            "performance_metrics": perf_met,
            "kill_switch_active": kill_switch_active(),
            "aggressive_scalping_mode": bool(s.aggressive_scalping_mode),
            "scalping_scan_timeframe": str(s.scalping_scan_timeframe) if s.aggressive_scalping_mode else None,
            "full_aggressive_max_flow": bool(fa),
            "full_aggressive_scan_timeframe": str(s.full_aggressive_scan_timeframe) if fa else None,
        }

        ranked: list[
            tuple[float, str, dict[str, Any], dict[str, float], float, MarketRegime, RegimeSnapshot]
        ] = []
        if candidates:
            ranked = rank_candidates_for_multi_execution(candidates, equity, symbols)
            if ranked and s.fund_portfolio_tilt_enabled:
                ranked[0][2]["candidate_selection"] = "portfolio_tilt_erc"

        best: tuple[float, str, dict[str, Any], dict[str, float], float, MarketRegime, RegimeSnapshot] | None = (
            ranked[0] if ranked else None
        )
        best_out[0] = best
        max_n = max(1, int(s.max_candidates_per_tick))
        to_try = ranked[:max_n]
        multi_executed = 0
        multi_skipped: list[str] = []

        if best:
            n_cand = len(candidates)
            bx = best[2]
            feats_b = best[3]
            bx["trade_explanation_ru"] = {
                "почему_эта_пара": (
                    f"Топ кандидат из {n_cand} (мульти-вход: до {max_n} символов за тик по score). "
                    "Скоринг: |edge_adj| × вероятность × ликвидность × риск_ATR."
                ),
                "composite_score": bx.get("composite_score"),
                "combined_edge": bx.get("combined_edge"),
                "confidence_по_стороне": bx.get("confidence_for_side"),
                "режим_рынка": bx.get("regime"),
                "ликвидность_score": bx.get("liquidity_score"),
                "риск_ATR_в_коридоре": bx.get("risk_score_atr_band"),
                "индикаторы_xgb_top": bx.get("top_features"),
                "macd_hist": round(float(feats_b.get("macd_hist", 0)), 6),
                "новости_и_sentiment": bx.get("tick_context"),
                "стратегия": bx.get("strategy_id"),
                "выбор_стратегии": bx.get("strategy_selection"),
            }

        ctrl_open = load_control()
        if ctrl_open.get("paused"):
            if best:
                self._log_reject(
                    best[1], "trading_paused", reason=str(ctrl_open.get("reason", ""))[:220]
                )
            logger.info(
                "tick_multi_exec %s",
                json.dumps(
                    {
                        "paused": True,
                        "raw_candidates": len(candidates),
                        "ranked_unique": len(ranked),
                        "try_cap": max_n,
                        "executed": 0,
                    },
                    ensure_ascii=False,
                ),
            )
        elif to_try and kill_switch_active():
            logger.info(
                "tick_multi_exec %s",
                json.dumps(
                    {
                        "kill_switch": True,
                        "raw_candidates": len(candidates),
                        "ranked_unique": len(ranked),
                        "try_cap": max_n,
                        "executed": 0,
                    },
                    ensure_ascii=False,
                ),
            )
        elif to_try:
            attempted_in_loop = 0
            for cand in to_try:
                open_positions = db.query(Position).filter(Position.mode == pos_mode).all()
                open_n = len(open_positions)
                if open_n >= open_cap:
                    logger.info(
                        "tick_multi_exec skip reason=max_positions_reached open_n=%s cap=%s",
                        open_n,
                        open_cap,
                    )
                    break

                attempted_in_loop += 1
                combined, sym, expl, feats, last_mid, reg, _snap = cand
                open_syms_cur = {p.symbol for p in open_positions}
                if sym in open_syms_cur:
                    logger.info(
                        "tick_multi_exec skip symbol=%s reason=already_open",
                        sym,
                    )
                    multi_skipped.append(f"{sym}:already_open")
                    continue

                try:
                    db.refresh(st)
                except Exception:
                    pass

                if paper:
                    locked = sum(float(p.size_usdt) for p in open_positions)
                    iter_equity = float(st.virtual_balance) + locked
                else:
                    iter_equity = self._fetch_live_equity()

                try:
                    emit(
                        E_SIGNAL_GENERATED,
                        {
                            "symbol": sym,
                            "regime": expl.get("regime"),
                            "strategy_id": expl.get("strategy_id"),
                            "combined_edge": expl.get("combined_edge"),
                        },
                    )
                except Exception:
                    pass

                side = "buy" if combined > 0 else "sell"
                atr = feats["atr14"]
                tp_sc = float(expl.get("strategy_tp_scale") or 1.0)
                rd = default_stops(side, last_mid, atr, regime=reg, tp_scale=tp_sc)
                if fa:
                    lev = min(
                        max(int(st.leverage), int(s.full_aggressive_min_leverage)),
                        int(s.full_aggressive_max_leverage),
                    )
                    risk_pct_open = float(s.full_aggressive_risk_pct)
                elif s.aggressive_scalping_mode:
                    lev = min(
                        max(int(st.leverage), int(s.scalping_default_leverage)),
                        int(s.scalping_max_leverage_cap),
                    )
                    risk_pct_open = float(s.scalping_risk_per_trade_pct)
                else:
                    lev = min(int(st.leverage), int(s.max_leverage))
                    risk_pct_open = float(st.risk_per_trade_pct)
                sl = rd.stop_price
                tp = rd.take_profit_price
                rm = regime_multipliers(reg)
                size = compute_position_size(
                    iter_equity,
                    last_mid,
                    sl,
                    risk_pct_open,
                    lev,
                    risk_mult=risk_m,
                    regime_size_mult=float(rm["size"]),
                )
                vscale = vol_scale_from_equity_history(db)
                if vscale < 1.0:
                    expl["fund_vol_scale"] = round(vscale, 4)
                size *= vscale
                strat_sz = expl.get("strategy_id") or "ml_hybrid"
                conf_sz = float(expl.get("confidence_for_side") or expl.get("confidence") or 0.5)
                atr_pct_sz = float(feats.get("atr_pct") or 0.01)
                size = apply_profit_scaling(size, conf_sz, atr_pct_sz, dd, strat_sz, iter_equity)
                expl["profit_engine_size_usdt"] = round(size, 4)

                open_triples = [(p.symbol, float(p.size_usdt), int(p.leverage)) for p in open_positions]
                pos_tuples = [(float(p.size_usdt), int(p.leverage)) for p in open_positions]

                g = global_guards()
                if not g.circuit_allows_new_trades():
                    self._log_reject(sym, "circuit_breaker")
                    logger.info("tick_multi_exec stop symbol=%s reason=circuit_breaker", sym)
                    break
                if not g.allow_new_trade_this_minute():
                    self._log_reject(sym, "max_trades_per_minute")
                    logger.info("tick_multi_exec stop symbol=%s reason=max_trades_per_minute", sym)
                    break

                ok_sym_exp, sym_exp_msg = g.exposure_ok_for_symbol(sym, size, lev, open_triples, iter_equity)
                sym_blocked = (not ok_sym_exp) and not (fa and s.full_aggressive_skip_symbol_exposure_cap)
                if sym_blocked:
                    self._log_reject(sym, "symbol_exposure_cap", detail=sym_exp_msg)
                    multi_skipped.append(f"{sym}:symbol_exposure_cap")
                    continue

                fr_check = check_fund_limits(sym, size, lev, iter_equity, open_triples)
                fund_ok = fr_check.ok or (fa and s.full_aggressive_skip_fund_limits)
                if fund_ok:
                    expl["fund_metrics"] = fr_check.metrics
                if not fund_ok:
                    self._log_reject(sym, "fund_limit", detail=fr_check.reason, **fr_check.metrics)
                    multi_skipped.append(f"{sym}:fund_limit")
                    continue

                exp_ratio = total_exposure_ratio(pos_tuples, iter_equity)
                add_ratio = (size * lev) / max(iter_equity, 1e-9)
                exp_cap_hit = (exp_ratio + add_ratio > s.max_total_exposure_ratio) and not (
                    fa and s.full_aggressive_skip_total_exposure_cap
                )
                if exp_cap_hit:
                    self._log_reject(
                        sym, "exposure_cap", current=round(exp_ratio, 3), add=round(add_ratio, 3)
                    )
                    multi_skipped.append(f"{sym}:exposure_cap")
                    continue

                rm_audit = regime_multipliers(reg)
                base_need_audit = s.signal_min_edge * float(rm_audit["edge"]) + edge_bias
                need_edge_audit = effective_min_edge(
                    db, s, base_need_audit, dd, expl.get("strategy_id") or "ml_hybrid"
                )
                meta_p_expl = expl.get("meta_p_trade")
                _sent = tick_ctx.get("sentiment")
                _sent_f = float(_sent) if isinstance(_sent, (int, float)) else None
                expl["decision_audit"] = build_decision_chain(
                    symbol=sym,
                    side=side,
                    regime=reg.value,
                    combined_edge=float(expl.get("combined_edge", combined)),
                    need_edge=need_edge_audit,
                    meta_ok=True if s.meta_enabled else None,
                    meta_p=float(meta_p_expl) if meta_p_expl is not None else None,
                    sentiment=_sent_f,
                    news_titles=list((tick_ctx.get("news") or {}).get("titles") or [])[:8],
                    fund_ok=True,
                    adaptive_bias=edge_bias,
                )
                fr = apply_execution_price(last_mid, side)
                entry = fr.fill_price
                expl["execution"] = {
                    "mid": last_mid,
                    "fill": entry,
                    "spread_half": round(fr.spread_half, 8),
                    "slippage_bps": fr.slippage_bps_applied,
                    "latency_bars": fr.latency_bars,
                }
                expl["dynamic_risk_mult"] = round(risk_m, 4)
                expl["regime_size_mult"] = round(float(rm["size"]), 4)
                expl["decision"] = {
                    "side": side,
                    "combined_edge": round(combined, 5),
                    "probability_success": round(
                        float(expl.get("p_up", 0.5) if side == "buy" else expl.get("p_down", 0.5)),
                        4,
                    ),
                    "confidence": expl.get("confidence_for_side") or expl.get("confidence"),
                }
                opened_ok = (
                    self._open_paper(db, st, sym, side, entry, size, lev, sl, tp, rd, expl)
                    if paper
                    else self._open_live(db, st, sym, side, entry, size, lev, sl, tp, rd, expl)
                )
                if opened_ok:
                    multi_executed += 1
                else:
                    multi_skipped.append(f"{sym}:open_failed_or_race")
                    logger.info(
                        "tick_multi_exec skip symbol=%s reason=open_not_confirmed (duplicate balance or live fail)",
                        sym,
                    )

            logger.info(
                "tick_multi_exec %s",
                json.dumps(
                    {
                        "raw_candidates": len(candidates),
                        "ranked_unique": len(ranked),
                        "try_cap": max_n,
                        "attempted_in_loop": attempted_in_loop,
                        "executed": multi_executed,
                        "skipped": multi_skipped[:25],
                    },
                    ensure_ascii=False,
                ),
            )

        self._strategy_panel_snapshot["multi_exec"] = {
            "max_per_tick": max_n,
            "ranked": len(ranked),
            "last_tick_executed": multi_executed,
            "last_tick_skipped_sample": multi_skipped[:12],
        }

        try:
            if kill_switch_active():
                system_health_gauge.set(0.0)
            elif dd > st.max_drawdown_pct * 0.85:
                system_health_gauge.set(1.0)
            else:
                system_health_gauge.set(2.0)
        except Exception:
            pass

        self._manage_open(db, st, paper, equity)
        self._snapshot_equity(db, st, equity, paper)

    def _open_paper(
        self,
        db: Session,
        st: BotSettings,
        symbol: str,
        side: str,
        price: float,
        size_usdt: float,
        lev: int,
        sl: float,
        tp: float,
        rd: Any,
        expl: dict[str, Any],
    ) -> bool:
        try:
            with symbol_operation_lock(symbol, "paper"):
                if db.query(Position).filter(Position.symbol == symbol, Position.mode == "paper").first():
                    return False
                if not can_start_live_entry(db, symbol, "paper"):
                    return False
                margin = size_usdt
                if float(st.virtual_balance) < margin:
                    self._log_reject(
                        symbol, "insufficient_paper_balance", free=round(float(st.virtual_balance), 2)
                    )
                    return False
                fee = max(margin * (self.settings.exec_fee_roundtrip_pct / 200.0), 0.01)
                st.virtual_balance -= margin + fee
                pos = Position(
                    symbol=symbol,
                    side=side,
                    entry_price=price,
                    size_usdt=size_usdt,
                    leverage=lev,
                    stop_loss=sl,
                    take_profit=tp,
                    highest_price=price if side == "buy" else None,
                    lowest_price=price if side == "sell" else None,
                    trail_price=None,
                    explanation_json=explanation_to_json(expl),
                    mode="paper",
                    lifecycle_state=PositionLifecycleState.NEW.value,
                )
                db.add(pos)
                tr = TradeRecord(
                    symbol=symbol,
                    side=side,
                    entry_price=price,
                    size_usdt=size_usdt,
                    stop_loss=sl,
                    take_profit=tp,
                    explanation_json=explanation_to_json(expl),
                    mode="paper",
                    status="open",
                    order_status="filled",
                    data_source="db",
                    lifecycle_state=PositionLifecycleState.NEW.value,
                )
                db.add(tr)
                db.flush()
                transition_trade_record(tr, PositionLifecycleState.PENDING, db=db)
                transition_trade_record(tr, PositionLifecycleState.FILLED, db=db)
                transition_position_lifecycle(pos, PositionLifecycleState.FILLED, db=db)
                db.commit()
                global_guards().record_open_attempt()
                try:
                    emit(
                        E_ORDER_FILLED,
                        {"symbol": symbol, "mode": "paper", "side": side, "trade_id": tr.id},
                    )
                except Exception:
                    pass
        except TimeoutError:
            self._log_reject(symbol, "symbol_lock_timeout", mode="paper")
            return False

        self._last_open_ts = time.time()
        logger.info("position_open %s", json.dumps({"symbol": symbol, "side": side, "mode": "paper"}, ensure_ascii=False))
        try:
            trades_opened.labels(mode="paper", side=side).inc()
            trace_trade_decision(symbol=symbol, side=side, explanation=expl, paper=True)
            last_trade_open_unix.set(self._last_open_ts)
        except Exception:
            pass
        return True

    def _open_live(
        self,
        db: Session,
        st: BotSettings,
        symbol: str,
        side: str,
        price: float,
        size_usdt: float,
        lev: int,
        sl: float,
        tp: float,
        rd: Any,
        expl: dict[str, Any],
    ) -> bool:
        if not self.settings.confirm_real_trading:
            return False
        try:
            with symbol_operation_lock(symbol, "live"):
                if db.query(Position).filter(Position.symbol == symbol, Position.mode == "live").first():
                    return False
                if not can_start_live_entry(db, symbol, "live"):
                    return False

                client_order_id = uuid.uuid4().hex[:32]
                tr = TradeRecord(
                    symbol=symbol,
                    side=side,
                    entry_price=price,
                    size_usdt=size_usdt,
                    stop_loss=sl,
                    take_profit=tp,
                    explanation_json=explanation_to_json(expl),
                    mode="live",
                    status="pending",
                    order_status="pending",
                    client_order_id=client_order_id,
                    data_source="db",
                    lifecycle_state=PositionLifecycleState.NEW.value,
                )
                db.add(tr)
                db.flush()
                transition_trade_record(tr, PositionLifecycleState.PENDING, db=db)
                db.commit()
                db.refresh(tr)

                try:
                    execution_orders_submitted_total.inc()
                except Exception:
                    pass

                order_resp: dict[str, Any] | None = None
                try:
                    ex = bybit_exchange.create_exchange()
                    ex.load_markets()
                    amt = (size_usdt * lev) / price
                    amt = float(ex.amount_to_precision(symbol, amt))
                    ex.set_leverage(lev, symbol)
                    params: dict[str, Any] = {
                        "stopLoss": sl,
                        "takeProfit": tp,
                        "orderLinkId": client_order_id,
                    }
                    if side == "buy":
                        order_resp = ex.create_order(symbol, "market", "buy", amt, None, params)
                    else:
                        order_resp = ex.create_order(symbol, "market", "sell", amt, None, params)
                except Exception as e:
                    expl["live_error"] = str(e)
                    try:
                        sentry_sdk.capture_exception(e)
                    except Exception:
                        pass
                    tr.status = "failed"
                    tr.order_status = "failed"
                    transition_trade_record(tr, PositionLifecycleState.FAILED, db=db)
                    tr.explanation_json = explanation_to_json(expl)
                    db.commit()
                    try:
                        execution_orders_failed_total.labels(reason="create_exception").inc()
                    except Exception:
                        pass
                    global_guards().record_execution_failure()
                    return False

                ex_oid = str((order_resp or {}).get("id") or "")
                tr.exchange_order_id = ex_oid or tr.exchange_order_id
                db.commit()
                try:
                    emit(
                        E_ORDER_CREATED,
                        {
                            "symbol": symbol,
                            "exchange_order_id": ex_oid,
                            "client_order_id": client_order_id,
                            "trade_id": tr.id,
                        },
                    )
                except Exception:
                    pass

                track_order_status(
                    db,
                    symbol=symbol,
                    exchange_order_id=ex_oid or None,
                    client_order_id=client_order_id,
                    trade_hint=tr,
                )
                db.commit()
                db.refresh(tr)

                ok_pos, np = verify_position_opened_after_order(
                    symbol,
                    side=side,
                    max_wait_sec=self.settings.live_order_confirm_timeout_sec,
                    sleep_sec=self.settings.live_order_confirm_poll_sec,
                )
                if not ok_pos or np is None:
                    expl["live_error"] = (expl.get("live_error") or "") + "; failsafe: no confirmed exchange position"
                    tr.status = "failed"
                    tr.order_status = "unknown"
                    transition_trade_record(tr, PositionLifecycleState.FAILED, db=db)
                    tr.explanation_json = explanation_to_json(expl)
                    db.commit()
                    try:
                        execution_orders_failed_total.labels(reason="no_position_confirm").inc()
                    except Exception:
                        pass
                    global_guards().record_execution_failure()
                    logger.error("live_open_failsafe symbol=%s client=%s", symbol, client_order_id)
                    return False

                entry_px = float(np.entry_price or price)
                margin = float(np.initial_margin_usdt or (np.notional_usdt / max(np.leverage, lev, 1)))
                expl["live_fill"] = {
                    "entry_from_exchange": entry_px,
                    "margin_usdt": round(margin, 6),
                    "contracts": np.contracts,
                    "client_order_id": client_order_id,
                    "exchange_order_id": ex_oid,
                }
                pos = Position(
                    symbol=symbol,
                    side=side,
                    entry_price=entry_px,
                    size_usdt=margin,
                    leverage=int(np.leverage or lev),
                    stop_loss=sl,
                    take_profit=tp,
                    highest_price=np.mark_price if side == "buy" else None,
                    lowest_price=np.mark_price if side == "sell" else None,
                    trail_price=None,
                    explanation_json=explanation_to_json(expl),
                    mode="live",
                    exchange_order_id=ex_oid or None,
                    client_order_id=client_order_id,
                    data_source="exchange",
                    contracts_qty=np.contracts,
                    last_mark_price=np.mark_price,
                    last_exchange_sync_at=datetime.utcnow(),
                    lifecycle_state=PositionLifecycleState.NEW.value,
                )
                db.add(pos)
                db.flush()
                transition_position_lifecycle(pos, PositionLifecycleState.FILLED, db=db)
                tr.status = "open"
                tr.order_status = "filled"
                tr.entry_price = entry_px
                tr.size_usdt = margin
                tr.data_source = "exchange"
                transition_trade_record(tr, PositionLifecycleState.FILLED, db=db)
                tr.explanation_json = explanation_to_json(expl)
                db.commit()
                global_guards().record_open_attempt()
                try:
                    emit(
                        E_ORDER_FILLED,
                        {
                            "symbol": symbol,
                            "mode": "live",
                            "side": side,
                            "trade_id": tr.id,
                            "exchange_order_id": ex_oid,
                        },
                    )
                except Exception:
                    pass
        except TimeoutError:
            self._log_reject(symbol, "symbol_lock_timeout", mode="live")
            return False

        self._last_open_ts = time.time()
        try:
            trades_opened.labels(mode="live", side=side).inc()
            trace_trade_decision(symbol=symbol, side=side, explanation=expl, paper=False)
            last_trade_open_unix.set(self._last_open_ts)
        except Exception:
            pass
        return True

    @staticmethod
    def _position_age_sec(opened_at: datetime | None) -> float:
        if opened_at is None:
            return 0.0
        now = datetime.utcnow()
        oa = opened_at
        if getattr(oa, "tzinfo", None) is not None:
            oa = oa.replace(tzinfo=None)
        return max(0.0, (now - oa).total_seconds())

    @staticmethod
    def _unrealized_pnl_pct_leveraged(pos: Position, mark: float) -> float:
        ep = float(pos.entry_price)
        if ep <= 0 or mark <= 0:
            return 0.0
        direction = 1.0 if _side_is_long(pos.side) else -1.0
        return (mark - ep) / ep * direction * float(int(pos.leverage)) * 100.0

    def _close_open_position(
        self,
        db: Session,
        st: BotSettings,
        pos: Position,
        exit_price: float,
        reason: str,
        *,
        skip_live_exchange: bool = False,
    ) -> bool:
        """
        Закрыть позицию в БД (paper/live). Live: сначала reduceOnly на бирже, затем CLOSING.
        Раньше CLOSING выставлялся до reduce — при ошибке API позиция залипала в closing.
        Возвращает True, если позиция удалена и сделка закрыта; False — live reduce не удался (сессия закоммичена).
        """
        if pos.mode == "live" and not skip_live_exchange:
            ok = attempt_reduce_only_market_close_with_retries(
                pos,
                attempts=max(1, int(self.settings.live_close_reduce_retries)),
                delay_sec=float(self.settings.live_close_reduce_retry_delay_sec),
            )
            if not ok:
                logger.warning(
                    "position_live_reduce_failed_deferring_close %s",
                    json.dumps(
                        {
                            "symbol": pos.symbol,
                            "id": pos.id,
                            "mode": pos.mode,
                            "close_reason": reason,
                        },
                        ensure_ascii=False,
                    ),
                )
                db.commit()
                return False
        elif pos.mode == "live" and skip_live_exchange:
            logger.warning(
                "position_close_skip_live_exchange %s",
                json.dumps(
                    {"symbol": pos.symbol, "id": pos.id, "close_reason": reason},
                    ensure_ascii=False,
                ),
            )

        transition_position_lifecycle(pos, PositionLifecycleState.CLOSING, db=db)
        db.flush()

        long_side = _side_is_long(pos.side)
        frx = apply_execution_price(exit_price, "sell" if long_side else "buy")
        exit_fill = frx.fill_price
        pnl_pct = (
            (exit_fill - pos.entry_price) / pos.entry_price * (1 if long_side else -1) * pos.leverage
        ) * 100
        pnl_usdt = pos.size_usdt * (pnl_pct / 100.0)
        fee_exit = max(pos.size_usdt * (self.settings.exec_fee_roundtrip_pct / 200.0), 0.0)

        try:
            expl = json.loads(pos.explanation_json) if pos.explanation_json else {}
        except json.JSONDecodeError:
            logger.warning("position id=%s explanation_json is not valid JSON, using empty dict", pos.id)
            expl = {}
        if not isinstance(expl, dict):
            expl = {}

        expl["close"] = {
            "reason": reason,
            "exit_mid": exit_price,
            "exit_fill": exit_fill,
            "pnl_pct": round(pnl_pct, 4),
            "exit_slippage_bps": frx.slippage_bps_applied,
        }

        if pos.mode == "paper":
            margin = pos.size_usdt
            st.virtual_balance += margin + pnl_usdt - fee_exit

        tr = (
            db.query(TradeRecord)
            .filter(
                TradeRecord.symbol == pos.symbol,
                TradeRecord.status == "open",
                TradeRecord.mode == pos.mode,
            )
            .order_by(TradeRecord.id.desc())
            .first()
        )
        if tr:
            transition_trade_record(tr, PositionLifecycleState.CLOSING, db=db)
            tr.exit_price = exit_fill
            net_pnl = pnl_usdt - fee_exit
            tr.pnl_usdt = net_pnl
            tr.pnl_pct = pnl_pct
            tr.closed_at = datetime.utcnow()
            tr.status = "closed"
            transition_trade_record(tr, PositionLifecycleState.CLOSED, db=db)
            tr.explanation_json = explanation_to_json(expl)
            mode_l = pos.mode
            try:
                emit(
                    E_POSITION_CLOSED,
                    {
                        "symbol": pos.symbol,
                        "mode": mode_l,
                        "pnl_usdt": float(net_pnl),
                        "reason": reason,
                        "trade_id": tr.id,
                    },
                )
            except Exception:
                pass
            try:
                trades_closed.labels(mode=mode_l, reason=reason).inc()
                trade_pnl_usdt.observe(float(net_pnl))
            except Exception:
                pass
            try:
                dur_sec = (tr.closed_at - tr.opened_at).total_seconds() if tr.closed_at and tr.opened_at else None
                entry_reason = str(
                    expl.get("entry_reason")
                    or (expl.get("rule_strategy_details") or {}).get("entry_reason")
                    or ""
                )
                append_trade_outcome(
                    {
                        "symbol": pos.symbol,
                        "side": pos.side,
                        "reason": reason,
                        "pnl_usdt": round(float(net_pnl), 6),
                        "ok": float(net_pnl) >= 0,
                        "mode": mode_l,
                        "regime": expl.get("regime"),
                        "combined_edge_at_open": expl.get("combined_edge"),
                        "strategy_id": expl.get("strategy_id") or "unknown",
                        "entry_reason": entry_reason,
                        "duration_sec": dur_sec,
                        "rsi14": expl.get("features", {}).get("rsi14")
                        if isinstance(expl.get("features"), dict)
                        else None,
                        "atr_pct": expl.get("atr_pct"),
                    }
                )
                record_trade_closed(
                    str(expl.get("strategy_id") or "unknown"),
                    float(net_pnl),
                    float(net_pnl) >= 0,
                )
                feat_open = expl.get("features") if isinstance(expl.get("features"), dict) else {}
                rd = expl.get("rule_strategy_details") or {}
                rsi_log = feat_open.get("rsi14")
                if rsi_log is None:
                    rsi_log = rd.get("rsi")
                ema_log = rd.get("ema20")
                log_trade_closed_csv(
                    opened_at=tr.opened_at,
                    closed_at=tr.closed_at or datetime.utcnow(),
                    symbol=pos.symbol,
                    side=pos.side,
                    strategy_id=str(expl.get("strategy_id") or "unknown"),
                    entry_reason=entry_reason,
                    entry_price=float(pos.entry_price),
                    exit_price=float(exit_fill),
                    pnl_usdt=float(net_pnl),
                    pnl_pct=float(pnl_pct),
                    rsi=float(rsi_log) if rsi_log is not None else None,
                    atr_pct=float(expl["atr_pct"]) if expl.get("atr_pct") is not None else None,
                    ema20=float(ema_log) if ema_log is not None else None,
                    mode=mode_l,
                    trade_id=int(tr.id),
                )
                log_trade_json_sidecar(
                    {
                        "trade_id": tr.id,
                        "symbol": pos.symbol,
                        "strategy_id": expl.get("strategy_id"),
                        "entry_reason": entry_reason,
                        "pnl_usdt": round(float(net_pnl), 6),
                        "pnl_pct": round(float(pnl_pct), 6),
                        "duration_sec": dur_sec,
                        "close_reason": reason,
                        "market_context": {
                            "rsi": rsi_log,
                            "atr_pct": expl.get("atr_pct"),
                            "ema20": ema_log,
                        },
                    }
                )
            except Exception:
                pass

        db.delete(pos)
        db.commit()
        return True

    def force_close_position_by_symbol(
        self,
        db: Session,
        symbol: str,
        *,
        mode: str | None = None,
        confirm_db_without_exchange: bool = False,
    ) -> dict[str, Any]:
        """
        Принудительное закрытие по символу (paper/live). Блокировка symbol+mode как у force paper.
        Live: при confirm_db_without_exchange=True не вызывается reduceOnly (только БД — риск рассинхрона).
        """
        st = db.query(BotSettings).filter_by(id=1).first()
        if not st:
            return {"ok": False, "error": "no_bot_settings"}
        sym = symbol.strip()
        q = db.query(Position).filter(Position.symbol == sym)
        if mode in ("paper", "live"):
            q = q.filter(Position.mode == mode)
        rows = q.all()
        if not rows:
            return {"ok": False, "error": "position_not_found"}
        if len(rows) > 1:
            return {
                "ok": False,
                "error": "ambiguous_symbol_pass_mode",
                "modes": [r.mode for r in rows],
            }
        pos0 = rows[0]
        m = pos0.mode
        if confirm_db_without_exchange and m != "live":
            return {"ok": False, "error": "confirm_db_without_exchange_live_only"}
        out_px: float
        try:
            with symbol_operation_lock(pos0.symbol, m):
                pos2 = db.query(Position).filter(Position.symbol == sym, Position.mode == m).first()
                if not pos2:
                    return {"ok": True, "already_closed": True}
                st2 = db.query(BotSettings).filter_by(id=1).first()
                if not st2:
                    return {"ok": False, "error": "no_bot_settings"}
                try:
                    cands = bybit_exchange.fetch_mark_price_candidates(
                        pos2.symbol,
                        prefer_ticker=bool(self.settings.paper_mark_prefer_ticker),
                    )
                except Exception as e:
                    logger.warning("force_close_by_symbol fetch_mark_price_candidates %s: %s", pos2.symbol, e)
                    cands = []
                if cands:
                    prices = [p for p, _ in cands]
                    out_px = float(min(prices)) if not _side_is_long(pos2.side) else float(max(prices))
                elif pos2.last_mark_price is not None and float(pos2.last_mark_price) > 0:
                    out_px = float(pos2.last_mark_price)
                else:
                    out_px = float(pos2.entry_price)
                skip_ex = bool(m == "live" and confirm_db_without_exchange)
                try:
                    closed = self._close_open_position(
                        db,
                        st2,
                        pos2,
                        out_px,
                        "manual_force_close_symbol_api",
                        skip_live_exchange=skip_ex,
                    )
                except Exception as e:
                    logger.exception("force_close_by_symbol symbol=%s mode=%s", sym, m)
                    db.rollback()
                    return {"ok": False, "error": f"close_failed: {e!s}"}
                if not closed:
                    return {
                        "ok": False,
                        "error": "live_reduce_failed",
                        "symbol": sym,
                        "mode": m,
                        "hint": "Повторите или передайте confirm_db_without_exchange=true (только если позиции нет на бирже).",
                    }
        except TimeoutError:
            return {"ok": False, "error": "symbol_lock_timeout", "symbol": sym, "mode": m}
        return {"ok": True, "exit_price": out_px, "symbol": sym, "mode": m}

    def reconcile_stuck_positions_on_startup(self, db: Session) -> dict[str, Any]:
        """Позиции в lifecycle closing (после сбоя) — добить закрытие или вернуть в filled."""
        st = db.query(BotSettings).filter_by(id=1).first()
        out: dict[str, Any] = {
            "ok": True,
            "closing": 0,
            "closed_ok": 0,
            "reverted_filled": 0,
            "errors": [],
        }
        if not st:
            out["ok"] = False
            out["error"] = "no_bot_settings"
            return out
        rows = (
            db.query(Position)
            .filter(Position.lifecycle_state == PositionLifecycleState.CLOSING.value)
            .all()
        )
        out["closing"] = len(rows)
        for pos in list(rows):
            try:
                with symbol_operation_lock(pos.symbol, pos.mode):
                    p = db.query(Position).filter(Position.id == pos.id).first()
                    if not p or p.lifecycle_state != PositionLifecycleState.CLOSING.value:
                        continue
                    st2 = db.query(BotSettings).filter_by(id=1).first()
                    if not st2:
                        continue
                    try:
                        cands = bybit_exchange.fetch_mark_price_candidates(
                            p.symbol,
                            prefer_ticker=bool(self.settings.paper_mark_prefer_ticker),
                        )
                    except Exception as e:
                        logger.warning("startup_reconcile fetch_mark %s: %s", p.symbol, e)
                        cands = []
                    if cands:
                        prices = [x for x, _ in cands]
                        out_px = float(min(prices)) if not _side_is_long(p.side) else float(max(prices))
                    elif p.last_mark_price is not None and float(p.last_mark_price) > 0:
                        out_px = float(p.last_mark_price)
                    else:
                        out_px = float(p.entry_price)

                    if p.mode == "live":
                        ok = attempt_reduce_only_market_close_with_retries(
                            p,
                            attempts=max(1, int(self.settings.live_close_reduce_retries)),
                            delay_sec=float(self.settings.live_close_reduce_retry_delay_sec),
                        )
                        if not ok:
                            transition_position_lifecycle(p, PositionLifecycleState.FILLED, db=db)
                            db.commit()
                            out["reverted_filled"] += 1
                            logger.warning(
                                "startup_stuck_closing_reverted_to_filled %s",
                                json.dumps(
                                    {"symbol": p.symbol, "id": p.id, "mode": p.mode},
                                    ensure_ascii=False,
                                ),
                            )
                            continue

                    closed = self._close_open_position(
                        db, st2, p, out_px, "startup_reconcile_closing"
                    )
                    if closed:
                        out["closed_ok"] += 1
                    else:
                        transition_position_lifecycle(p, PositionLifecycleState.FILLED, db=db)
                        db.commit()
                        out["reverted_filled"] += 1
                        logger.warning(
                            "startup_stuck_closing_reverted_after_close_fail %s",
                            json.dumps(
                                {"symbol": p.symbol, "id": p.id, "mode": p.mode},
                                ensure_ascii=False,
                            ),
                        )
            except TimeoutError:
                out["errors"].append(
                    {"id": pos.id, "symbol": pos.symbol, "error": "lock_timeout"}
                )
            except Exception as e:
                logger.exception("startup_reconcile position id=%s", pos.id)
                out["errors"].append({"id": pos.id, "symbol": pos.symbol, "error": str(e)})
        return out

    def reconcile_long_running_paper_on_startup(self, db: Session) -> dict[str, Any]:
        """Paper-позиции старше 2× MAX_POSITION_LIFETIME_SEC — закрыть при старте (анти-залипание)."""
        st = db.query(BotSettings).filter_by(id=1).first()
        summary: dict[str, Any] = {"checked": 0, "closed": 0, "errors": []}
        if not st:
            return summary
        thr = float(self.settings.max_position_lifetime_sec) * 2.0
        rows = db.query(Position).filter(Position.mode == "paper").all()
        for pos in rows:
            summary["checked"] += 1
            if self._position_age_sec(pos.opened_at) <= thr:
                continue
            try:
                with symbol_operation_lock(pos.symbol, "paper"):
                    p2 = db.query(Position).filter(Position.id == pos.id, Position.mode == "paper").first()
                    if not p2:
                        continue
                    st2 = db.query(BotSettings).filter_by(id=1).first()
                    if not st2:
                        continue
                    try:
                        cands = bybit_exchange.fetch_mark_price_candidates(
                            p2.symbol,
                            prefer_ticker=bool(self.settings.paper_mark_prefer_ticker),
                        )
                    except Exception:
                        cands = []
                    if cands:
                        prices = [p for p, _ in cands]
                        out_px = float(min(prices)) if not _side_is_long(p2.side) else float(max(prices))
                    elif p2.last_mark_price is not None and float(p2.last_mark_price) > 0:
                        out_px = float(p2.last_mark_price)
                    else:
                        out_px = float(p2.entry_price)
                    closed = self._close_open_position(
                        db, st2, p2, out_px, "startup_reconcile_stale_age_paper"
                    )
                    if closed:
                        summary["closed"] += 1
            except TimeoutError:
                summary["errors"].append({"id": pos.id, "error": "lock_timeout"})
            except Exception as e:
                logger.exception("reconcile_long_paper id=%s", pos.id)
                summary["errors"].append({"id": pos.id, "error": str(e)})
        return summary

    def force_close_paper_position(self, db: Session, position_id: int) -> dict[str, Any]:
        """Ручное закрытие paper-позиции (разблокирует кап при залипании)."""
        st = db.query(BotSettings).filter_by(id=1).first()
        if not st:
            return {"ok": False, "error": "no_bot_settings"}
        pos = db.query(Position).filter(Position.id == position_id).first()
        if not pos:
            return {"ok": False, "error": "position_not_found"}
        if pos.mode != "paper":
            return {"ok": False, "error": "paper_only"}
        out_px: float
        sym: str
        with symbol_operation_lock(pos.symbol, "paper"):
            pos2 = db.query(Position).filter(Position.id == position_id, Position.mode == "paper").first()
            if not pos2:
                return {"ok": True, "already_closed": True}
            st2 = db.query(BotSettings).filter_by(id=1).first()
            if not st2:
                return {"ok": False, "error": "no_bot_settings"}
            sym = pos2.symbol
            try:
                cands = bybit_exchange.fetch_mark_price_candidates(
                    pos2.symbol,
                    prefer_ticker=bool(self.settings.paper_mark_prefer_ticker),
                )
            except Exception as e:
                logger.warning("force_close fetch_mark_price_candidates %s: %s", pos2.symbol, e)
                cands = []
            if cands:
                prices = [p for p, _ in cands]
                out_px = float(min(prices)) if not _side_is_long(pos2.side) else float(max(prices))
            elif pos2.last_mark_price is not None and float(pos2.last_mark_price) > 0:
                out_px = float(pos2.last_mark_price)
            else:
                out_px = float(pos2.entry_price)
            try:
                closed = self._close_open_position(db, st2, pos2, out_px, "manual_force_close_api")
            except Exception as e:
                logger.exception("force_close_paper id=%s", position_id)
                db.rollback()
                return {"ok": False, "error": f"close_failed: {e!s}"}
            if not closed:
                return {"ok": False, "error": "close_deferred_unexpected_for_paper"}
        return {"ok": True, "exit_price": out_px, "symbol": sym}

    def _manage_open(self, db: Session, st: BotSettings, paper: bool, equity: float) -> None:
        pos_mode = "paper" if paper else "live"
        s = self.settings
        marks: dict[str, float] = {}
        open_orders_by_sym: dict[str, list[Any]] = {}
        tickers_map: dict[str, dict[str, Any]] = {}
        if paper and bool(s.paper_mark_prefer_ticker):
            # Быстрый путь для paper: один вызов fetch_tickers() вместо N вызовов mark/ohlcv на позицию.
            # Это делает обновление Mark/PnL в UI стабильным и не упирается в REST-лимиты.
            try:
                tickers_map = market_scanner.fetch_tickers_map()
            except Exception as e:
                logger.debug("fetch_tickers_map (paper manage_open) failed: %s", e)
                tickers_map = {}
        if not paper:
            if s.exchange_sync_enabled:
                nowt = time.time()
                if nowt - self._last_exchange_sync_ts >= s.exchange_sync_interval_sec:
                    try:
                        sync_positions_with_db(db, st)
                        self._last_exchange_sync_ts = nowt
                    except Exception as e:
                        try:
                            sentry_sdk.capture_exception(e)
                        except Exception:
                            pass
            live_syms = [p.symbol for p in db.query(Position).filter(Position.mode == "live").all()]
            if live_syms:
                marks = fetch_position_marks_for_symbols(live_syms)
                try:
                    ex = bybit_exchange.create_exchange()
                    ex.load_markets()
                    for sym in live_syms:
                        try:
                            open_orders_by_sym[sym] = ex.fetch_open_orders(sym, limit=40)
                        except Exception:
                            open_orders_by_sym[sym] = []
                except Exception as e:
                    try:
                        sentry_sdk.capture_exception(e)
                    except Exception:
                        pass

        for pos in db.query(Position).filter(Position.mode == pos_mode).all():
            mark_stale = False
            cur: float | None = None
            mark_lo: float | None = None
            mark_hi: float | None = None
            if paper:
                t = tickers_map.get(pos.symbol) or {}
                last = t.get("last") or t.get("mark") or t.get("close")
                try:
                    if last is not None and float(last) > 0:
                        cur = float(last)
                        mark_stale = False
                        mark_lo = mark_hi = cur
                    else:
                        raise ValueError("no_ticker_last")
                except Exception:
                    # Фолбэк: старый путь через mark candidates (может быть медленно/лимитно).
                    try:
                        cands = bybit_exchange.fetch_mark_price_candidates(
                            pos.symbol,
                            prefer_ticker=bool(s.paper_mark_prefer_ticker),
                        )
                        if not cands:
                            cur = None
                            mark_stale = True
                        else:
                            best_age = min(a for _, a in cands)
                            mark_stale = best_age > float(s.mark_price_max_stale_sec)
                            prices = [p for p, _ in cands]
                            mark_lo = min(prices)
                            mark_hi = max(prices)
                            cur = min(cands, key=lambda x: x[1])[0]
                    except Exception as e:
                        logger.debug("paper fetch_mark_price_candidates %s: %s", pos.symbol, e)
                        cur = None
                        mark_stale = True
            else:
                raw = marks.get(pos.symbol)
                if raw is not None and float(raw) > 0:
                    cur = float(raw)
                    mark_stale = False
                    mark_lo = mark_hi = cur
                elif pos.last_mark_price is not None and float(pos.last_mark_price) > 0:
                    cur = float(pos.last_mark_price)
                    mark_stale = False
                    mark_lo = mark_hi = cur
                else:
                    try:
                        cands = bybit_exchange.fetch_mark_price_candidates(
                            pos.symbol,
                            prefer_ticker=bool(s.paper_mark_prefer_ticker),
                        )
                        if not cands:
                            cur = None
                            mark_stale = True
                        else:
                            best_age = min(a for _, a in cands)
                            mark_stale = best_age > float(s.mark_price_max_stale_sec)
                            prices = [p for p, _ in cands]
                            mark_lo = min(prices)
                            mark_hi = max(prices)
                            cur = min(cands, key=lambda x: x[1])[0]
                    except Exception:
                        cur = None
                        mark_stale = True
                oo = open_orders_by_sym.get(pos.symbol) or []
                if oo:
                    logger.debug("live %s open_orders=%s", pos.symbol, len(oo))

            age_sec = self._position_age_sec(pos.opened_at)
            exit_price: float | None = None
            reason = ""

            if cur is None or cur <= 0:
                logger.info(
                    "position_mark_unavailable %s",
                    json.dumps({"symbol": pos.symbol, "mode": pos.mode, "age_sec": round(age_sec, 1)}),
                )
                if age_sec >= float(s.max_position_lifetime_sec):
                    fb = float(pos.last_mark_price or pos.entry_price or 0.0)
                    if fb > 0:
                        exit_price = fb
                        reason = "forced_close_timeout"
                        logger.info(
                            "forced_close_timeout %s",
                            json.dumps(
                                {
                                    "symbol": pos.symbol,
                                    "mode": pos.mode,
                                    "age_sec": round(age_sec, 1),
                                    "exit_fallback": "last_mark_or_entry",
                                    "price": round(fb, 8),
                                },
                                ensure_ascii=False,
                            ),
                        )
                if exit_price is None:
                    db.commit()
                    continue
            else:
                if not mark_stale:
                    pos.last_mark_price = float(cur)
                elif mark_stale:
                    logger.info(
                        "position_mark_stale_skip %s",
                        json.dumps(
                            {
                                "symbol": pos.symbol,
                                "mode": pos.mode,
                                "mark": round(float(cur), 8),
                                "max_stale_sec": float(s.mark_price_max_stale_sec),
                            },
                            ensure_ascii=False,
                        ),
                    )

                if age_sec >= float(s.max_position_lifetime_sec):
                    exit_price = float(cur)
                    reason = "forced_close_timeout"
                    logger.info(
                        "forced_close_timeout %s",
                        json.dumps(
                            {
                                "symbol": pos.symbol,
                                "mode": pos.mode,
                                "age_sec": round(age_sec, 1),
                                "mark": round(float(cur), 8),
                            },
                            ensure_ascii=False,
                        ),
                    )

                ep = float(pos.entry_price)
                if mark_lo is None or mark_hi is None:
                    mark_lo = mark_hi = float(cur)
                if (
                    exit_price is None
                    and not mark_stale
                    and ep > 0
                    and float(s.bad_mark_price_max_rel_deviation) > 0
                ):
                    rel_dev = max(abs(float(mark_lo) - ep) / ep, abs(float(mark_hi) - ep) / ep)
                    if rel_dev > float(s.bad_mark_price_max_rel_deviation):
                        exit_price = float(cur)
                        reason = "bad_mark_deviation"
                        logger.warning(
                            "bad_price_detected %s",
                            json.dumps(
                                {
                                    "symbol": pos.symbol,
                                    "mode": pos.mode,
                                    "entry": round(ep, 8),
                                    "mark": round(float(cur), 8),
                                    "rel_deviation": round(rel_dev, 4),
                                    "threshold": float(s.bad_mark_price_max_rel_deviation),
                                },
                                ensure_ascii=False,
                            ),
                        )

                if exit_price is None and not mark_stale:
                    u_pnl = self._unrealized_pnl_pct_leveraged(pos, float(cur))
                    hi_thr = float(s.position_force_close_pnl_pct_high)
                    lo_thr = float(s.position_force_close_pnl_pct_low)
                    if u_pnl > hi_thr or u_pnl < lo_thr:
                        exit_price = float(cur)
                        reason = "forced_close_abnormal_pnl"
                        logger.info(
                            "forced_close_abnormal_pnl %s",
                            json.dumps(
                                {
                                    "symbol": pos.symbol,
                                    "mode": pos.mode,
                                    "unrealized_pnl_pct": round(u_pnl, 4),
                                    "mark": round(float(cur), 8),
                                    "limits": [lo_thr, hi_thr],
                                },
                                ensure_ascii=False,
                            ),
                        )

                # TP/SL/трейлинг при любом валидном cur (даже если котировка «stale»):
                # иначе 1m-свеча > MARK_PRICE_MAX_STALE_SEC залипает позицию навсегда.
                if exit_price is None:
                    atr = abs(pos.entry_price - pos.stop_loss) / 1.5
                    rd = default_stops(
                        "buy" if _side_is_long(pos.side) else "sell",
                        pos.entry_price,
                        atr,
                        regime=None,
                    )
                    trail_side = "buy" if _side_is_long(pos.side) else "sell"
                    hi, lo, trail = update_trail(
                        trail_side,
                        pos.entry_price,
                        pos.highest_price,
                        pos.lowest_price,
                        float(cur),
                        pos.trail_price,
                        rd.trail_trigger_pct,
                        rd.trail_offset_pct,
                    )
                    if not mark_stale:
                        pos.highest_price = hi
                        pos.lowest_price = lo
                        pos.trail_price = trail
                    else:
                        # Не двигаем трейл по устаревшему mark; TP/SL по уровням всё равно проверяем.
                        pass
                    if _side_is_long(pos.side):
                        if float(mark_lo) <= pos.stop_loss:
                            exit_price, reason = float(mark_lo), "stop_loss"
                        elif float(mark_hi) >= pos.take_profit:
                            exit_price, reason = float(mark_hi), "take_profit"
                        elif trail and not mark_stale and float(cur) <= trail:
                            exit_price, reason = float(cur), "trailing_stop"
                    else:
                        if float(mark_hi) >= pos.stop_loss:
                            exit_price, reason = float(mark_hi), "stop_loss"
                        elif float(mark_lo) <= pos.take_profit:
                            exit_price, reason = float(mark_lo), "take_profit"
                        elif trail and not mark_stale and float(cur) >= trail:
                            exit_price, reason = float(cur), "trailing_stop"

            if exit_price is None:
                logger.info(
                    "position_no_exit_this_tick %s",
                    json.dumps(
                        {
                            "symbol": pos.symbol,
                            "mode": pos.mode,
                            "id": pos.id,
                            "lifecycle": getattr(pos, "lifecycle_state", None),
                            "cur": None if cur is None else round(float(cur), 8),
                            "mark_lo": None if mark_lo is None else round(float(mark_lo), 8),
                            "mark_hi": None if mark_hi is None else round(float(mark_hi), 8),
                            "mark_stale": mark_stale,
                            "tp": round(float(pos.take_profit), 8),
                            "sl": round(float(pos.stop_loss), 8),
                            "trail_price": float(pos.trail_price) if pos.trail_price is not None else None,
                            "age_sec": round(age_sec, 1),
                        },
                        ensure_ascii=False,
                    ),
                )
                db.commit()
                continue

            try:
                closed = self._close_open_position(db, st, pos, float(exit_price), reason)
                if not closed:
                    logger.info(
                        "position_close_deferred_live_reduce %s",
                        json.dumps(
                            {
                                "symbol": pos.symbol,
                                "id": pos.id,
                                "mode": pos.mode,
                                "signal_reason": reason,
                                "exit_price": float(exit_price),
                            },
                            ensure_ascii=False,
                        ),
                    )
            except Exception:
                logger.exception(
                    "position_close_failed %s",
                    json.dumps(
                        {"symbol": pos.symbol, "id": pos.id, "mode": pos.mode},
                        ensure_ascii=False,
                    ),
                )
                db.rollback()

    def _snapshot_equity(self, db: Session, st: BotSettings, equity: float, paper: bool) -> None:
        if paper:
            locked = sum(float(p.size_usdt) for p in db.query(Position).filter(Position.mode == "paper").all())
            nav = float(st.virtual_balance) + locked
            bal = float(st.virtual_balance)
        else:
            nav = equity
            bal = equity
        db.add(
            EquityPoint(
                equity=nav,
                balance=bal,
                mode="paper" if paper else "live",
            )
        )
        db.commit()
        try:
            equity_gauge.labels(mode="paper" if paper else "live").set(nav)
        except Exception:
            pass

    def _fetch_live_equity(self) -> float:
        try:
            ex = bybit_exchange.create_exchange()
            bal = ex.fetch_balance()
            return float(bal["USDT"]["total"] or 0)
        except Exception:
            return 0.0


engine = BotEngine()
