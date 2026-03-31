/**
 * Группировка и краткие подсказки для bot_runtime (.env без секретов).
 * Ключи — как в pydantic Settings (snake_case).
 */

export const RUNTIME_KEY_HINTS = {
  default_symbols:
    "Базовый список пар, если не включён полный скан USDT perpetual. Влияет на то, какие символы гарантированно попадают в цикл.",
  scan_all_usdt_perpetual:
    "true — тянуть все linear USDT perpetual с биржи (нагрузка на API). false — только default_symbols.",
  scan_all_symbols_refresh_sec: "Как часто обновлять кэш списка всех perpetual при полном скане.",
  scan_all_max_symbols:
    "Лимит пар при полном скане (0 = без лимита). Снижает риск 10006 и ускоряет тик.",
  scanner_min_quote_volume_usdt:
    "Минимальный суточный объём (USDT) для участия в скане. Выше — меньше мусорных альтов.",
  scanner_max_parallel: "Параллельные запросы OHLCV в одном батче сканера.",
  scanner_liquidity_ref_quote_vol:
    "Опорный объём для нормализации liquidity_score в скоринге кандидатов.",
  scanner_min_close_usdt: "Отсев символов с подозрительно низкой ценой закрытия.",
  scanner_min_last_bar_volume: "Отсев по объёму последней свечи (анти-спайк/анти-пыль).",
  ohlcv_cache_ttl_sec: "Сколько секунд держать кэш свечей в памяти между тиками.",
  selection_use_composite:
    "true — выбор лучшего кандидата по composite (edge×conf×ликвидность×ATR-риск).",
  scan_interval_sec: "Пауза между полными циклами бота (сек). Меньше — чаще сигналы, выше нагрузка на API.",
  multi_strategy_enabled: "Включить несколько стратегий и маршрутизатор вместо одной логики.",
  strategy_router_mode:
    "regime — выбор стратегии по режиму рынка; ml_only — упор на ML-маршрут (см. код движка).",
  strategy_ml_conf_floor: "Нижний порог уверенности модели для маршрута, где он применим.",
  signal_min_edge: "Минимальный |combined_edge| для допуска к входу после остальных фильтров.",
  min_model_confidence: "Минимальная вероятность успеха по стороне сделки (XGBoost и т.п.).",
  min_risk_reward: "Минимальное соотношение потенциала к риску (RR) для постановки SL/TP.",
  use_atr_filter:
    "Жёсткий коридор ATR%: символы вне atr_pct_min/max отбрасываются полностью.",
  atr_pct_min: "Нижняя граница ATR% (волатильность слишком низкая — часто нет движения).",
  atr_pct_max: "Верхняя граница ATR% (слишком жёсткая волатильность — риск проскальзывания/ликвидаций).",
  atr_soft_opt_min_pct: "Мягкий «комфортный» пол ATR для скоринга (не жёсткий отсев).",
  atr_soft_opt_max_pct: "Верхняя граница мягкого коридора ATR для скоринга.",
  atr_soft_score_floor: "Минимальный вклад atr_score в скоринг при мягком режиме.",
  use_macd_momentum_filter: "Фильтр по MACD/импульсу: отсекает входы против локального импульса.",
  max_margin_pct_equity: "Потолок использования маржи относительно эквити (защита от перегруза).",
  daily_loss_limit_pct: "Дневной лимит убытка (% от эквити): при достижении — стоп новых входов.",
  min_equity_usdt: "Минимальный эквити для торговли; ниже — консервативный режим/стоп.",
  aggressive_mode: "Ослабляет часть фильтров и поднимает чувствительность (больше сигналов, выше риск).",
  fallback_rule_enabled:
    "Разрешить rule-based fallback, если фич/строк в кадре мало (см. feature_frame_min_rows).",
  feature_frame_min_rows: "Минимум строк OHLCV/фич для «полноценного» ML-пути.",
  regime_adx_trend: "Порог ADX, выше которого рынок считается трендовым для режима.",
  regime_atr_high_pct: "Уровень ATR%, выше которого режим считается «жарким».",
  regime_vol_cluster_high: "Порог vol cluster для классификации режима.",
  exec_spread_bps: "Полу-модель спреда в paper/backtest (базисные пункты).",
  exec_slippage_bps: "Модель проскальзывания при исполнении в симуляции.",
  exec_latency_bars: "Задержка входа в барах (симуляция реального лага).",
  exec_fee_roundtrip_pct: "Комиссия туда-обратно в % для оценки PnL в paper/backtest.",
  exchange_sync_enabled: "Сверка открытых позиций с биржей в live.",
  exchange_sync_interval_sec: "Как часто делать сверку позиций с Bybit.",
  live_order_confirm_timeout_sec: "Таймаут ожидания подтверждения ордера в live.",
  dynamic_risk_dd_compress: "Насколько сжимать риск при росте просадки (динамический риск).",
  dynamic_risk_floor_mult: "Нижний множитель размера позиции при плохой эквити-кривой.",
  corr_max_pair: "Максимальная корреляция между кандидатами — анти-кластер однотипных позиций.",
  max_total_exposure_ratio: "Потолок суммарной экспозиции (грубо: сколько «объёма» можно набрать).",
  meta_min_p_trade: "Минимальный p_trade мета-фильтра (вероятность «торговать вообще»).",
  meta_enabled: "Включить мета-фильтр поверх сигнала стратегии.",
  kill_switch_enabled: "Включить автоматический kill-switch по правилам ниже.",
  kill_switch_drawdown_pct: "Просадка (%), при которой kill-switch глушит новые входы.",
  max_trades_per_minute: "Лимит сделок в минуту (анти-спам и защита API).",
  circuit_breaker_window_sec: "Окно для подсчёта ошибок биржи перед circuit breaker.",
  circuit_breaker_failure_threshold: "Сколько ошибок подряд в окне открывает circuit breaker.",
  circuit_breaker_cooldown_sec: "Пауза после срабатывания circuit breaker.",
  fund_risk_enabled: "Включить лимиты фонда: доля на имя, кластеры альтов и т.д.",
  fund_max_single_name_gross_pct: "Максимальная доля брутто-экспозиции на один тикер.",
  fund_major_symbols: "Список «мейджоров» для кластерных/фондовых лимитов.",
  news_enabled: "Подтягивать новости/заголовки в контекст решения (если настроены источники).",
  hf_sentiment_enabled: "Включить HF-модель сентимента для смещения edge.",
  paper_initial_balance: "Стартовый баланс paper-счёта в движке (если не переопределён БД).",
  aggressive_scalping_mode:
    "Отдельный высокочастотный пресет: короткий ТФ, выше плечо и лимиты — только если осознанно.",
  scalping_scan_timeframe: "Таймфрейм свечей в агрессивном скальпинг-режиме.",
  scalping_max_open_positions: "Максимум одновременных позиций в скальпинг-пресете.",
  scalping_risk_per_trade_pct: "Риск на сделку в скальпинг-пресете (%).",
  scalping_default_leverage: "Базовое плечо в скальпинг-пресете.",
  consistency_checks_enabled: "Периодическая сверка целостности БД ↔ фактические позиции.",
  consistency_check_interval_ticks: "Раз в сколько тиков запускать сверку.",
};

export const RUNTIME_GROUPS = [
  {
    id: "scan",
    title: "Сканер и данные",
    blurb:
      "Какие символы обходятся, как фильтруется ликвидность и как часто бот будит рынок. Прямо влияет на нагрузку Bybit REST и на шум сигналов.",
    match: (k) =>
      k === "default_symbols" ||
      k.startsWith("scan_") ||
      k.startsWith("scanner_") ||
      k === "ohlcv_cache_ttl_sec" ||
      k === "selection_use_composite" ||
      k === "trade_outcomes_path",
  },
  {
    id: "strategies",
    title: "Стратегии и режим",
    blurb:
      "Мульти-стратегии, mean reversion / breakout параметры, пороги режима (ADX, ATR, vol cluster). Определяет, какой сценарий входа активен.",
    match: (k) =>
      k.startsWith("multi_strategy") ||
      k.startsWith("strategy_") ||
      k.startsWith("mean_reversion") ||
      k.startsWith("breakout_") ||
      k.startsWith("regime_") ||
      k === "adaptive_state_path",
  },
  {
    id: "signal",
    title: "Сигнал, ML и фильтры",
    blurb:
      "Пороги edge/уверенности, RR, ATR-коридоры, MACD-фильтр, агрессивный режим и fallback при нехватке данных.",
    match: (k) =>
      k.startsWith("signal_") ||
      k.startsWith("min_model") ||
      k.startsWith("min_risk") ||
      k.startsWith("atr_") ||
      k.startsWith("use_") ||
      k === "aggressive_mode" ||
      k === "fallback_rule_enabled" ||
      k === "feature_frame_min_rows",
  },
  {
    id: "risk",
    title: "Риск, маржа и лимиты",
    blurb:
      "Дневной стоп-лосс по эквити, минимальный эквити, лимит маржи, динамическое сжатие риска при просадке.",
    match: (k) =>
      k.startsWith("max_margin") ||
      k.startsWith("daily_loss") ||
      k.startsWith("min_equity") ||
      k.startsWith("dynamic_risk_") ||
      k.startsWith("risk_") ||
      k === "paper_initial_balance" ||
      k.startsWith("max_drawdown") ||
      k.startsWith("max_open_positions") ||
      k.startsWith("risk_per_trade") ||
      k.startsWith("default_leverage") ||
      k.startsWith("max_leverage"),
  },
  {
    id: "exec",
    title: "Исполнение и live-синхронизация",
    blurb:
      "Параметры симуляции спреда/проскалывания и сверки с биржей в реальном режиме.",
    match: (k) =>
      k.startsWith("exec_") ||
      k.startsWith("exchange_sync") ||
      k.startsWith("live_order"),
  },
  {
    id: "corr_meta",
    title: "Корреляции и мета-фильтр",
    blurb:
      "Ограничение похожих позиций и второй уровень фильтрации «торговать ли сейчас вообще».",
    match: (k) => k.startsWith("corr_") || k.startsWith("max_total_exposure") || k.startsWith("meta_"),
  },
  {
    id: "guards",
    title: "Kill switch, circuit breaker, частота сделок",
    blurb:
      "Аварийная остановка входов при просадке/волатильности и защита от шторма ошибок API.",
    match: (k) =>
      k.startsWith("kill_switch") ||
      k.startsWith("circuit_breaker") ||
      k.startsWith("max_trades_per_minute") ||
      k.startsWith("max_exposure_per_symbol"),
  },
  {
    id: "fund",
    title: "Фонд / портфельный слой",
    blurb:
      "Лимиты концентрации, кластеры альтов, vol targeting и наклон портфеля (если включено).",
    match: (k) =>
      k.startsWith("fund_") ||
      k.startsWith("portfolio_") ||
      k.startsWith("profit_") ||
      k === "performance_metrics_window_hours",
  },
  {
    id: "wf_bt",
    title: "Walk-forward и бэктест",
    blurb: "Размеры окон обучения/теста при офлайн-прогонах и дообучении.",
    match: (k) => k.startsWith("wf_"),
  },
  {
    id: "news_ml",
    title: "Новости, sentiment, внешние API",
    blurb:
      "Опциональные источники альфы и телеметрия. Ключи API из ответа скрыты; здесь только флаги и модели.",
    match: (k) =>
      k.startsWith("news_") ||
      k.startsWith("hf_sentiment") ||
      k.startsWith("langfuse_") ||
      k.startsWith("prometheus_") ||
      k.startsWith("browserbase_") ||
      k.startsWith("firecrawl") ||
      k.startsWith("auto_retrain") ||
      k.startsWith("self_improve"),
  },
  {
    id: "watchdog",
    title: "Watchdog, логи, метрики, Sentry",
    blurb:
      "Внешний процесс мониторинга, уровень логов, DogStatsD и окружение Sentry (DSN в ответе не передаётся).",
    match: (k) =>
      k.startsWith("watchdog_") ||
      k.startsWith("log_level") ||
      k.startsWith("metrics_") ||
      k.startsWith("dd_") ||
      k.startsWith("sentry_") ||
      k.startsWith("alert_log") ||
      k === "confirm_real_trading" ||
      k === "bybit_testnet",
  },
  {
    id: "consistency",
    title: "Сверка БД",
    blurb: "Периодическая проверка согласованности записей с фактическим состоянием.",
    match: (k) => k.startsWith("consistency_"),
  },
  {
    id: "scalping",
    title: "Агрессивный скальпинг-пресет",
    blurb:
      "Отдельный режим с коротким ТФ и ослабленными фильтрами. Включать только осознанно.",
    match: (k) => k.startsWith("scalping_") || k === "aggressive_scalping_mode",
  },
];

export function bucketRuntimeKeys(allKeys) {
  const keys = [...allKeys].sort();
  const used = new Set();
  const out = [];
  for (const g of RUNTIME_GROUPS) {
    const list = keys.filter((k) => g.match(k) && !used.has(k));
    list.forEach((k) => used.add(k));
    if (list.length) out.push({ id: g.id, title: g.title, blurb: g.blurb, keys: list });
  }
  const rest = keys.filter((k) => !used.has(k));
  if (rest.length) {
    out.push({
      id: "other",
      title: "Остальные параметры",
      blurb:
        "Поля из Settings без отдельной группы. Подробности — в backend/app/config.py и README; после правок .env нужен перезапуск воркера.",
      keys: rest,
    });
  }
  return out;
}

export function formatRuntimeValue(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(6)));
  if (typeof v === "string" && v.length > 120) return `${v.slice(0, 117)}…`;
  return String(v);
}
