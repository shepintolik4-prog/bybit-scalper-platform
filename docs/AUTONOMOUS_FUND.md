# Автономный режим (quant-style): скан, выбор сделки, UI, мониторинг

## Скан рынка

- Модуль `app/services/market_scanner.py`: список USDT linear perpetual, фильтр по `SCANNER_MIN_QUOTE_VOLUME_USDT`, параллельная загрузка OHLCV (`asyncio` + `to_thread`), TTL-кэш свечей `OHLCV_CACHE_TTL_SEC`.
- Список символов задаётся `SCAN_ALL_USDT_PERPETUAL` + `resolve_scan_symbols()` (см. `.env.example`).

## Выбор лучшей сделки

- Для каждого кандидата считается `composite_score ≈ |combined_edge| × confidence_по_стороне × liquidity_score × risk_ATR_в_коридоре`.
- Ранжирование топ-10 и «лучший символ цикла» отдаются в `GET /api/scan/snapshot`.
- При `FUND_PORTFOLIO_TILT_ENABLED` используется ERC-tilt с опорой на `selection_score` (см. `fund_risk.py`).

## Самообучение

- Закрытия пишутся в `TRADE_OUTCOMES_PATH` (JSONL) для последующего анализа / дообучения.
- `AUTO_RETRAIN_ENABLED`: при `SCAN_ALL_USDT_PERPETUAL=true` символ для переобучения выбирается по числу закрытых сделок в БД (иначе — первый из `DEFAULT_SYMBOLS`).

## UI

- Дашборд запрашивает `/api/scan/snapshot`: просканированные символы, топ-10, выбранный кандидат, последние отклонения.
- В позициях/сделках поле `trade_explanation_ru` и `explanation` — блок «Почему сделка».

## Метрики и алерты (Prometheus / Grafana)

- Экспорт: `GET http://localhost:8000/metrics` (путь из `PROMETHEUS_METRICS_PATH`).
- Полезные серии:
  - `scalper_scan_symbols_count`
  - `scalper_seconds_since_last_trade_open` (до первого входа может быть `-1`)
  - `scalper_last_trade_open_unixtime`
  - `scalper_signal_rejects_total`
  - `scalper_bot_tick_seconds`

Пример правил: `monitoring/prometheus_alerts.example.yml` — скопируйте в свой Prometheus и настройте `alertmanager`.

Grafana: datasource Prometheus → URL вашего Prometheus; импортируйте дашборд вручную по метрикам выше.

## Sentry

- Ошибки цикла бота уходят в Sentry при настроенном `SENTRY_DSN_BACKEND`.
- Авто-«исправление кода» и перезапуск процесса изнутри приложения **не делаются** (небезопасно). Используйте внешний supervisor (systemd, NSSM, Docker restart) + Sentry.

## Self-heal (реалистично)

- Внешний watchdog: опрос `GET /api/health/ready` и рестарт процесса (см. `tools/health_watchdog.ps1` как шаблон).
- Firecrawl / Hugging Face: влияние на edge через `autonomy_context` / `NEWS_*`, `HF_*` в `.env.example`.

## Плагины

- **Sentry**: `SENTRY_DSN_*`
- **Prometheus**: встроенный `/metrics`; для сбора — sidecar или отдельный Prometheus.
- **Firecrawl / HF**: см. раздел автономии в корневом README проекта.
