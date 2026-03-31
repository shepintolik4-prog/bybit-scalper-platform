"""
Prometheus /metrics для Grafana. Отдельно от DogStatsD (Datadog).
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# Торговля
trades_opened = Counter(
    "scalper_trades_opened_total",
    "Открытые сделки",
    ["mode", "side"],
)
trades_closed = Counter(
    "scalper_trades_closed_total",
    "Закрытые сделки",
    ["mode", "reason"],
)
trade_pnl_usdt = Histogram(
    "scalper_trade_pnl_usdt",
    "PnL закрытой сделки USDT",
    buckets=(-500, -100, -50, -10, -1, 0, 1, 10, 50, 100, 500, 2000),
)
signal_rejects = Counter(
    "scalper_signal_rejects_total",
    "Отказы входа",
    ["reason"],
)

equity_gauge = Gauge("scalper_equity_usdt", "Текущая эквити (последний снимок)", ["mode"])

retrain_runs = Counter("scalper_retrain_runs_total", "Запуски переобучения", ["symbol"])
self_improve_runs = Counter("scalper_self_improve_runs_total", "Итерации self-improve")

tick_duration = Histogram(
    "scalper_bot_tick_seconds",
    "Длительность _tick бота",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

scan_symbols_gauge = Gauge("scalper_scan_symbols_count", "Символов в последнем цикле скана")
last_trade_open_unix = Gauge("scalper_last_trade_open_unixtime", "Unix time последнего открытия позиции")
seconds_since_last_open = Gauge(
    "scalper_seconds_since_last_trade_open",
    "Секунды с последнего открытия (для алерта «нет сделок»)",
)

bot_ticks_total = Counter("scalper_bot_ticks_total", "Тиков основного цикла бота")
scan_fetch_seconds = Histogram(
    "scalper_scan_fetch_seconds",
    "Время параллельной загрузки OHLCV (один цикл)",
    buckets=(0.5, 1.0, 2.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0),
)
portfolio_drawdown_pct = Gauge("scalper_portfolio_drawdown_pct", "Просадка от пика equity, %")
strategy_winrate_gauge = Gauge("scalper_strategy_winrate", "Скользящий winrate по стратегии", ["strategy_id"])
strategy_trades_gauge = Gauge("scalper_strategy_closed_trades", "Закрытых сделок по стратегии", ["strategy_id"])
strategy_signal_total = Counter(
    "scalper_strategy_router_selections_total",
    "Выбор маршрутизатором (до fallback)",
    ["strategy_id"],
)

# Сигналы / отказы по фичам (PromQL reject_rate ≈ rate(scalper_signal_rejects_total[5m]) / rate(scalper_bot_ticks_total[5m]))
signals_candidate_total = Counter(
    "scalper_signals_candidate_total",
    "Кандидатов, добавленных в список после фильтров (до выбора best)",
)
signals_source_total = Counter(
    "scalper_signals_source_total",
    "Источник сигнала у кандидата",
    ["source"],
)
signals_empty_features_total = Counter(
    "scalper_signals_empty_features_total",
    "Случаи empty_features (strict+recovered ниже порога), до rule-fallback",
)
signals_pipeline_fallback_used_total = Counter(
    "scalper_signals_pipeline_fallback_used_total",
    "Символов, где сработал rule-fallback после слабого feature-frame",
)

# Execution / сверка с биржей (PromQL: rate(...[5m]) для fail/partial rate)
execution_orders_submitted_total = Counter(
    "scalper_execution_orders_submitted_total",
    "Попытки выставления live-ордера (market entry)",
)
execution_orders_failed_total = Counter(
    "scalper_execution_orders_failed_total",
    "Неуспешное подтверждение или отказ ордера",
    ["reason"],
)
execution_orders_filled_total = Counter(
    "scalper_execution_orders_filled_total",
    "Ордера в статусе filled (трекер)",
)
execution_order_partial_total = Counter(
    "scalper_execution_order_partial_total",
    "Частично исполненные ордера (трекер)",
)
execution_sync_mismatch_total = Counter(
    "scalper_execution_sync_mismatch_total",
    "События сверки: фантом, размер, цена, усыновление сироты",
    ["kind"],
)

race_condition_detected_total = Counter(
    "scalper_race_condition_detected_total",
    "Защита от гонок: таймаут лока, предотвращённый двойной вход",
    ["kind"],
)
invalid_state_transition_total = Counter(
    "scalper_invalid_state_transition_total",
    "Попытка запрещённого перехода state machine",
    ["from_state", "to_state"],
)
kill_switch_triggered_total = Counter(
    "scalper_kill_switch_triggered_total",
    "Активации kill switch",
    ["reason"],
)
consistency_errors_total = Counter(
    "scalper_consistency_errors_total",
    "Несоответствия DB vs exchange / margin / orders",
    ["check"],
)
system_health_gauge = Gauge(
    "scalper_system_health",
    "0=stopped 1=warning 2=ok (агрегат)",
)
circuit_breaker_open_gauge = Gauge(
    "scalper_circuit_breaker_open",
    "1 если circuit breaker открыт (новые входы запрещены)",
)


def refresh_strategy_gauges() -> None:
    try:
        from app.services.strategy_performance import load_stats

        data = load_stats()
        for sid, st in (data.get("strategies") or {}).items():
            n = int(st.get("wins", 0)) + int(st.get("losses", 0))
            wr = float(st.get("winrate", 0.0))
            strategy_trades_gauge.labels(strategy_id=sid).set(n)
            strategy_winrate_gauge.labels(strategy_id=sid).set(wr)
    except Exception:
        pass


def metrics_response():
    from starlette.responses import Response

    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
