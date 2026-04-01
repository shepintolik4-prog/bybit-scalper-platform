from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=(),
    )

    model_dir: str = "models"
    database_url: str = "postgresql+psycopg2://scalper:scalper@localhost:5433/scalper"
    bybit_api_key: str = ""
    bybit_api_secret: str = ""
    bybit_testnet: bool = Field(default=False, validation_alias="BYBIT_TESTNET")
    confirm_real_trading: bool = False
    api_secret: str = "dev-secret"
    debug: bool = Field(default=False, validation_alias="DEBUG")
    # Мастер-ключ для Fernet (хранение Bybit API в БД). Мин. 16 символов.
    secret_key: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    sentry_dsn_backend: str | None = None
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.0
    log_level: str = "INFO"

    # Datadog / DogStatsD (метрики через UDP; Agent слушает :8125)
    metrics_enabled: bool = False
    dd_agent_host: str = ""
    dd_dogstatsd_port: int = 8125
    metrics_prefix: str = "bybit_scalper"
    metrics_default_tags: str = ""

    default_symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT"
    # True: сканировать все linear USDT perpetual с биржи (см. resolve_scan_symbols в коде)
    scan_all_usdt_perpetual: bool = Field(
        default=True,
        validation_alias=AliasChoices("SCAN_ALL_USDT_PERPETUAL", "SCAN_ALL_ENABLED"),
    )
    scan_all_symbols_refresh_sec: int = 3600
    # 0 = без лимита; иначе только первые N символов (по алфавиту) — снижает нагрузку на REST
    scan_all_max_symbols: int = Field(default=30, validation_alias="SCAN_ALL_MAX_SYMBOLS")
    # Если Bybit не отдал свечи — синтетические бары (только paper-разработка; в логах mock_ohlcv)
    mock_ohlcv_on_empty: bool = Field(default=False, validation_alias="MOCK_OHLCV_ON_EMPTY")
    # Paper: нет новых сделок дольше N сек — ослабить пороги edge/conf/meta на тик
    paper_quiet_fallback_sec: float = Field(default=600.0, validation_alias="PAPER_QUIET_FALLBACK_SEC")
    # Сканер: фильтр по 24h quote volume (USDT), 0 = не фильтровать
    scanner_min_quote_volume_usdt: float = 0.0
    scanner_max_parallel: int = 12
    scanner_liquidity_ref_quote_vol: float = 5_000_000.0
    # Отсев «мусорных» котировок (0 = выкл.): последняя цена / объём последней свечи
    scanner_min_close_usdt: float = 0.0
    scanner_min_last_bar_volume: float = 0.0
    ohlcv_cache_ttl_sec: float = 45.0
    # Выбор сделки: True = max(selection_score), selection_score ≈ |edge|×conf×liq×risk_atr
    selection_use_composite: bool = True
    trade_outcomes_path: str = "data/trade_outcomes.jsonl"

    # --- Signal engine weights (edge); defaults are conservative ---
    signal_w_orderbook: float = 0.28
    signal_w_volume: float = 0.18
    signal_w_breakout: float = 0.24
    signal_w_funding: float = 0.10
    signal_w_rsi: float = 0.20
    trend_ema_period: int = 200
    funding_rate_scale: float = 0.001

    # Signal engine integration into selection_score
    signal_engine_bonus_k: float = 0.35  # composite *= (1 + k * |signal_score|)

    # Orderbook fetch (public REST). Keep small to avoid rate-limit.
    orderbook_symbols_per_tick: int = 6
    orderbook_depth_levels: int = 10
    orderbook_fetch_limit: int = 50
    orderbook_cache_ttl_sec: float = 1.2

    # Мульти-стратегии + маршрутизатор
    multi_strategy_enabled: bool = True
    strategy_router_mode: str = "regime"  # regime | ml_only
    strategy_ml_conf_floor: float = 0.5
    mean_reversion_z_threshold: float = 1.55
    mean_reversion_tp_scale: float = 0.62
    breakout_vol_cluster_min: float = 1.35
    breakout_volume_mult: float = 1.18
    breakout_tp_scale: float = 1.05
    strategy_stats_path: str = "data/strategy_stats.json"
    strategy_min_trades_for_disable: int = 15
    strategy_min_winrate_disable: float = 0.28

    # Smart pause (новые входы; управление открытыми — как обычно)
    trading_control_path: str = "data/trading_control.json"
    smart_pause_drawdown_pct: float = 0.0  # 0 = выкл.; например 22
    smart_pause_equity_vol_mult: float = 0.0  # 0 = выкл.; например 2.2
    smart_pause_auto_clear_sec: float = 0.0  # 0 = не снимать автоматически

    # Алерты
    alert_telegram_bot_token: str = ""
    alert_telegram_chat_id: str = ""
    alert_log_path: str = "data/alerts.log"

    telegram_allowed_chat_ids: str = ""
    telegram_api_base: str = "http://127.0.0.1:8000"

    # Watchdog (отдельный процесс)
    watchdog_enabled: bool = False
    watchdog_api_base: str = "http://127.0.0.1:8000"
    watchdog_interval_sec: int = 60
    watchdog_ready_timeout_sec: float = 12.0
    watchdog_no_trade_sec: float = 14400.0
    watchdog_max_tick_sec: float = 180.0
    watchdog_restart_cmd: str = ""  # shell; пусто = только алерт
    max_open_positions: int = 3
    risk_per_trade_pct: float = 1.0
    max_drawdown_pct: float = 15.0
    default_leverage: int = 5
    max_leverage: int = 10

    paper_initial_balance: float = 10000.0

    # Стратегия / фильтры входа
    signal_min_edge: float = Field(
        default=0.0,
        validation_alias=AliasChoices("SIGNAL_MIN_EDGE", "MIN_EDGE"),
    )
    min_model_confidence: float = Field(
        default=0.5,
        validation_alias=AliasChoices("MIN_MODEL_CONFIDENCE", "MIN_CONFIDENCE"),
    )
    min_risk_reward: float = 1.05
    atr_pct_min: float = 0.0008
    atr_pct_max: float = 0.035
    # USE_ATR_FILTER=true — жёсткий коридор atr_pct_min/max (как раньше atr_regime); false — только мягкий atr_score в скоринге
    use_atr_filter: bool = False
    atr_soft_opt_min_pct: float = 0.005
    atr_soft_opt_max_pct: float = 0.03
    atr_soft_score_floor: float = 0.18
    use_macd_momentum_filter: bool = True
    # Риск
    max_margin_pct_equity: float = 0.22
    daily_loss_limit_pct: float = 5.0
    min_equity_usdt: float = 20.0

    # Агрессивные фильтры + резерв при пустых фичах (env: AGGRESSIVE_MODE, FALLBACK_RULE_ENABLED, FEATURE_FRAME_MIN_ROWS)
    aggressive_mode: bool = False
    fallback_rule_enabled: bool = True
    feature_frame_min_rows: int = 28

    # Regime detection
    regime_adx_trend: float = 22.0
    regime_atr_high_pct: float = 0.028
    regime_vol_cluster_high: float = 1.65
    regime_m_high_edge: float = 1.12
    regime_m_high_sl: float = 1.12
    regime_m_high_size: float = 0.78
    regime_m_trend_edge: float = 0.92
    regime_m_trend_sl: float = 1.0
    regime_m_trend_size: float = 1.05
    regime_m_flat_edge: float = 1.25
    regime_m_flat_sl: float = 0.92
    regime_m_flat_size: float = 0.65
    regime_flat_trade_allow: float = 0.55

    # Исполнение (paper/backtest; live — ориентир)
    exec_spread_bps: float = 2.0
    exec_slippage_bps: float = 3.0
    exec_latency_bars: int = 1
    exec_fee_roundtrip_pct: float = 0.12

    # Защита от залипших позиций и битого mark (paper/live в _manage_open)
    max_position_lifetime_sec: float = Field(
        default=300.0,
        validation_alias=AliasChoices("MAX_POSITION_LIFETIME_SEC", "MAX_POSITION_AGE_SEC"),
    )
    bad_mark_price_max_rel_deviation: float = 0.5
    position_force_close_pnl_pct_high: float = 200.0
    position_force_close_pnl_pct_low: float = -80.0
    mark_price_max_stale_sec: float = 180.0
    paper_mark_prefer_ticker: bool = True

    # Live execution: сверка позиций и подтверждение ордеров
    exchange_sync_enabled: bool = True
    exchange_sync_interval_sec: float = 15.0
    live_order_confirm_timeout_sec: float = 12.0
    live_order_confirm_poll_sec: float = 0.25
    live_close_reduce_retries: int = 3
    live_close_reduce_retry_delay_sec: float = 0.35

    # Динамический риск
    dynamic_risk_dd_compress: float = 0.55
    dynamic_risk_floor_mult: float = 0.35
    dynamic_risk_equity_boost_k: float = 0.6
    dynamic_risk_equity_boost_max: float = 0.12

    # Корреляции / экспозиция
    corr_max_pair: float = 0.82
    max_total_exposure_ratio: float = 2.6

    # Meta-filter
    meta_min_p_trade: float = 0.45
    meta_enabled: bool = True

    # Walk-forward / backtest
    wf_train_bars: int = 400
    wf_test_bars: int = 80
    wf_step_bars: int = 40
    wf_purge_bars: int = 5

    # Портфель (portfolio_manager)
    portfolio_signal_temperature: float = 1.15
    portfolio_periods_per_year: int = 105_120
    portfolio_default_method: str = "risk_parity_erc"

    # Quant-fund / PM слой (концентрация, кластеры, vol overlay, ERC-выбор кандидата)
    fund_risk_enabled: bool = True
    fund_max_single_name_gross_pct: float = 0.52
    fund_max_alt_cluster_share: float = 1.0
    fund_major_symbols: str = "BTC/USDT:USDT,ETH/USDT:USDT"
    fund_vol_targeting_enabled: bool = False
    fund_vol_rel_threshold: float = 0.0012
    fund_vol_size_scale_floor: float = 0.55
    fund_portfolio_tilt_enabled: bool = False
    fund_portfolio_tilt_method: str = "risk_parity_erc"
    fund_tilt_w_max: float = 0.48
    fund_tilt_max_pair_correlation: float = 0.88

    # Автономный стек: новости, sentiment, Langfuse, Prometheus, retrain, self-improve
    news_enabled: bool = False
    news_urls: str = ""
    firecrawl_api_key: str = ""
    hf_token: str = ""
    hf_sentiment_enabled: bool = False
    hf_sentiment_model: str = "distilbert-base-uncased-finetuned-sst-2-english"
    news_sentiment_scale: float = 0.06
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    prometheus_metrics_path: str = "/metrics"
    browserbase_enabled: bool = False
    browserbase_api_key: str = ""
    browserbase_ws_endpoint: str = ""
    auto_retrain_enabled: bool = False
    auto_retrain_interval_hours: float = 24.0
    self_improve_enabled: bool = False
    self_improve_interval_sec: int = 3600
    self_improve_window_hours: int = 24
    self_improve_min_trades: int = 8
    self_improve_winrate_low: float = 0.38
    self_improve_winrate_high: float = 0.58
    self_improve_bias_step: float = 0.004
    self_improve_bias_max: float = 0.025
    adaptive_state_path: str = "data/adaptive_state.json"

    # --- Режим агрессивного скальпинга (AGGRESSIVE_SCALPING_MODE=true): максимум сигналов, высокий риск ---
    aggressive_scalping_mode: bool = False
    scalping_scan_timeframe: str = "1m"
    scalping_ohlcv_limit: int = 420
    scalping_min_ohlcv_bars: int = 48
    scalping_max_open_positions: int = 24
    scalping_risk_per_trade_pct: float = 3.0
    scalping_default_leverage: int = 15
    scalping_max_leverage_cap: int = 20
    scalping_sl_pct: float = 0.007
    scalping_tp_pct: float = 0.014
    scalping_tp_scale: float = 0.85
    scalping_trail_enabled: bool = True
    scalping_trail_trigger_pct: float = 0.005
    scalping_trail_offset_pct: float = 0.0035
    scalping_rsi_period: int = 7
    scalping_rsi_oversold: float = 30.0
    scalping_rsi_overbought: float = 70.0
    scalping_rsi_edge: float = 0.11
    scalping_rsi_confidence: float = 0.58
    scalping_ema_period: int = 20
    scalping_ema_deviation_pct: float = 0.004
    scalping_ema_edge: float = 0.085
    scalping_ema_confidence: float = 0.5
    scalping_micro_edge: float = 0.052
    scalping_micro_confidence: float = 0.4
    scalping_signal_min_edge_mult: float = 0.32
    scalping_min_conf_floor: float = 0.32
    scalping_skip_meta: bool = True
    scalping_relax_macd: bool = True
    scalping_skip_correlation: bool = True
    scalping_skip_profit_diversification: bool = True
    scalping_force_trade_after_minutes: float = 20.0
    scalping_force_trade_edge_mult: float = 0.28
    scalping_force_trade_conf_floor: float = 0.26
    scalping_max_trades_per_minute: int = 36

    # --- MAX FLOW / HFT-style (FULL_AGGRESSIVE_MAX_FLOW): все USDT perp, минимум фильтров, много сделок — ОЧЕНЬ ВЫСОКИЙ РИСК ---
    full_aggressive_max_flow: bool = False
    full_aggressive_auto_enable_bot: bool = True
    full_aggressive_max_positions: int = 40
    full_aggressive_risk_pct: float = 2.0
    full_aggressive_min_leverage: int = 10
    full_aggressive_max_leverage: int = 20
    full_aggressive_sl_pct: float = 0.01
    full_aggressive_tp_pct: float = 0.012
    full_aggressive_tp_scale: float = 1.0
    full_aggressive_trail_enabled: bool = True
    full_aggressive_trail_trigger_pct: float = 0.006
    full_aggressive_trail_offset_pct: float = 0.004
    full_aggressive_scan_timeframe: str = "1m"
    full_aggressive_ohlcv_limit: int = 220
    full_aggressive_min_ohlcv_bars: int = 28
    full_aggressive_feature_min_rows: int = 18
    full_aggressive_force_trade: bool = True
    full_aggressive_force_trade_sec: float = 60.0
    full_aggressive_need_edge: float = 0.015
    full_aggressive_min_conf: float = 0.2
    full_aggressive_max_trades_per_minute: int = 72
    full_aggressive_skip_fund_limits: bool = True
    full_aggressive_skip_total_exposure_cap: bool = True
    full_aggressive_skip_symbol_exposure_cap: bool = True
    full_aggressive_auto_select_min_trades: int = 100
    full_aggressive_disable_winrate_below: float = 0.4
    full_aggressive_boost_top_fraction: float = 0.2
    full_aggressive_boost_edge_mult: float = 0.82
    full_aggressive_ema_dev_pct: float = 0.004
    full_aggressive_momentum_bars: int = 3
    full_aggressive_breakout_lookback: int = 12
    full_aggressive_trade_csv_path: str = "data/trades_detailed.csv"
    full_aggressive_rsi_period: int = 7
    full_aggressive_rsi_oversold: float = 30.0
    full_aggressive_rsi_overbought: float = 70.0

    # Сколько лучших кандидатов пробовать открыть за один тик (по убыванию score; дедуп по символу)
    max_candidates_per_tick: int = 15

    # Интервал основного цикла бота (сек)
    scan_interval_sec: int = Field(default=60, validation_alias="SCAN_INTERVAL_SEC")
    # Когда bot_enabled=false: всё равно опрашивать TP/SL/трейлинг (сек), иначе позиции не закрываются
    manage_positions_poll_sec: float = 8.0

    # Kill switch (глобальная остановка новых входов)
    kill_switch_enabled: bool = False
    kill_switch_drawdown_pct: float = 35.0
    kill_switch_exchange_errors_window_n: int = 8
    kill_switch_exchange_errors_window_sec: float = 120.0
    kill_switch_atr_spike_ratio: float = 2.5
    kill_switch_min_equity_usdt: float = 0.0

    # Risk guards: частота входов, экспозиция на символ, circuit breaker
    max_trades_per_minute: int = 12
    max_exposure_per_symbol_ratio: float = 1.2
    circuit_breaker_window_sec: float = 180.0
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_cooldown_sec: float = 300.0

    # Сверка БД ↔ биржа
    consistency_checks_enabled: bool = True
    consistency_check_interval_ticks: int = 30

    # Profit engine (после защиты капитала)
    profit_dd_edge_knee_pct: float = 8.0
    profit_dd_edge_boost_per_pct: float = 0.012
    profit_dd_edge_max_mult: float = 2.2
    profit_strategy_min_trades_for_tilt: int = 12
    profit_strategy_weak_winrate: float = 0.36
    profit_strategy_strong_winrate: float = 0.55
    profit_strategy_weak_edge_mult: float = 1.18
    profit_strategy_strong_edge_mult: float = 0.92
    profit_confidence_size_gamma: float = 0.85
    profit_atr_size_reference: float = 0.012
    profit_atr_size_floor: float = 0.55
    profit_atr_size_cap: float = 1.35
    profit_dd_size_knee_pct: float = 6.0
    profit_dd_size_floor_mult: float = 0.4
    profit_dd_size_compress_per_pct: float = 0.035
    profit_strategy_strong_size_mult: float = 1.12
    profit_strategy_weak_size_mult: float = 0.72
    profit_max_margin_fraction_of_cap: float = 1.0
    profit_diversification_enabled: bool = True
    profit_diversity_score_bonus: float = 0.08
    profit_adaptive_learning_enabled: bool = True
    profit_adaptive_interval_sec: int = 3600
    profit_adaptive_confidence_step: float = 0.02
    profit_adaptive_confidence_min: float = 0.42
    profit_adaptive_confidence_max: float = 0.72
    performance_metrics_window_hours: int = 720

    @property
    def cors_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def symbol_list(self) -> list[str]:
        return [x.strip() for x in self.default_symbols.split(",") if x.strip()]

    def resolve_scan_symbols(self) -> list[str]:
        """Список символов для одного цикла сканирования бота."""
        if not self.full_aggressive_max_flow and not self.scan_all_usdt_perpetual:
            return self.symbol_list
        from app.services.bybit_exchange import get_usdt_linear_perpetual_symbols

        return get_usdt_linear_perpetual_symbols(
            refresh_sec=self.scan_all_symbols_refresh_sec,
            max_symbols=self.scan_all_max_symbols,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
