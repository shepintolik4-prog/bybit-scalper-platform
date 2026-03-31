# Bybit Scalper Platform — аудит и справочник системы

Документ для разработчика/кванта: найденные проблемы, внесённые правки, остаточные риски и полное описание архитектуры и потока данных.

**Плагины (интеграция, без live-данных в этом документе):**

- **Sentry** — `app/monitoring/sentry_setup.py`, `init_sentry()` в lifespan; исключения в цикле бота → `sentry_sdk.capture_exception()`.
- **Prometheus** — `GET /metrics`, `app/monitoring/prometheus_metrics.py`, middleware метрик HTTP.
- **Hugging Face** — sentiment в тике (`app/services/sentiment_hf.py`), при необходимости документации по API моделей смотреть официальные гайды HF.
- **Firecrawl** — новости/контекст (`app/services/news_firecrawl.py`), при сбоях проверять ключи и лимиты провайдера.

**Live execution (фондовый слой):** `docs/EXECUTION_LAYER.md` — `exchange_sync`, `order_tracker`, метрики `scalper_execution_*`, миграции БД.

---

## 0. Полный аудит (обзор модулей)

| Область | Файлы / модули | Замечания |
|--------|-----------------|-----------|
| **Вход / lifecycle** | `main.py` | Lifespan: Sentry, БД, дефолт `BotSettings`, APScheduler retrain, `bot_engine.ensure_worker()`. `/api/health/ready` проверяет SQLAlchemy `engine` (не путать с `bot_engine`). |
| **БД** | `database.py` | SQLite: `check_same_thread=False` для фонового бота; `pool_pre_ping`. Риск блокировок при конкуренции API+бот. |
| **Auth** | `api/deps.py` | `X-API-Secret` == `API_SECRET` из env; сравнение строк (не constant-time). |
| **Бот** | `services/bot_engine.py` | См. P1–P3; поток-демон, один цикл на процесс. |
| **Биржа** | `bybit_exchange.py` | Ключи: БД (Fernet) или env; публичные свечи через `create_public_exchange()`. |
| **Сканер** | `market_scanner.py` | Кэш OHLCV, `asyncio.to_thread` + семафор; ошибки по символу → пустой ряд в результатах. |
| **Стратегии** | `strategies/*`, `strategy_selector.py` | Роутинг по режиму; fallback `ml_hybrid`. |
| **ML** | `ml/predictor.py`, `backtest.py`, `retrain_scheduler.py` | XGB: `y=1` = up в бэктесте → `proba[1]` ≈ p_up; автоretrain по символу с max closed trades. |
| **Риск / фонд** | `risk.py`, `fund_risk.py`, `dynamic_risk.py` | Размер позиции, стопы, лимиты портфеля. |
| **JSON state** | `strategy_performance.py`, `trading_control_store.py` | Lock при записи; `summary_for_api()` читает файл без lock — редкая гонка чтения. |
| **Watchdog** | `watchdog.py` | HTTP health; `WATCHDOG_RESTART_CMD` через `shell=True` — только доверенный конфиг. |
| **Frontend** | `frontend/src/*` | Vite proxy `/api` → 8000; секрет в `localStorage` + заголовок axios. |
| **Инфра** | `docker-compose*.yml`, `deploy/` | См. `PRODUCTION_ORCHESTRATION.md`; не выставлять API в интернет без секрета и TLS. |

---

## 1. Список проблем (аудит)

### Критичные / высокие (торговля и учёт)

| ID | Проблема | Статус |
|----|-----------|--------|
| P1 | **Смешение paper и live в `Position`:** `open_n`, список открытых позиций, корреляция/экспозиция и `_manage_open` работали по **всем** строкам `Position`, а не по текущему режиму. В результате paper-бот мог «съедать» лимиты live-позиций, наоборот, и при закрытии метки Prometheus/journal могли не совпадать с фактическим режимом позиции. | **Исправлено** — фильтр `Position.mode == pos_mode`, дубликаты входа по `symbol` с учётом `mode`, закрытие: `virtual_balance` и метрики по `pos.mode`, привязка `TradeRecord` по `symbol + status + mode`. |
| P2 | **Закрытие `TradeRecord`:** выбор открытой сделки только по `symbol` без `mode` — риск привязать PnL к чужой записи при редких коллизиях. | **Исправлено** — добавлен фильтр `TradeRecord.mode == pos.mode`. |
| P3 | **Live execution vs симуляция сопровождения:** после `create_order` позиция в БД сопровождается по **публичным OHLCV** (как paper). Реальный SL/TP на бирже может сработать иначе по времени/цене; рассинхрон БД ↔ биржа — источник ошибок учёта и «фантомных» состояний. | **Открыто** — см. раздел «Критические риски». |
| P4 | **Незащищённые мутации API:** `POST /api/bot/start|stop`, `PATCH /api/settings`, все `POST /api/ml/*`, `POST /api/portfolio/allocate` были доступны **без** `X-API-Secret` — посторонний мог остановить бота, менять риск-параметры, грузить CPU обучением/бэктестом, дергать тяжёлую аллокацию. | **Исправлено** — везде добавлен `Depends(verify_api_secret)` (кроме `GET /api/bot/status`). |
| P5 | **`routes_risk._equity_usdt`:** вызов несуществующего `get_settings()` (мертвая строка) → **NameError** при первом запросе `GET /api/risk/fund`. | **Исправлено** — удалена лишняя строка. |

### Средние

| ID | Проблема | Рекомендация |
|----|-----------|--------------|
| M1 | **Поток бота в `threading.Thread` + отдельный `SessionLocal` на тик** — конкуренция с API на одной SQLite БД возможны блокировки/timeout при высокой нагрузке. | Production: PostgreSQL (уже предусмотрено в Docker-стеке); для SQLite — короткие транзакции, как сейчас. |
| M2 | **`asyncio` внутри синхронного сканера** — если где-то вызывается `asyncio.run` из воркера, возможны edge-case с event loop; держать границы sync/async явными. | Аудит вызовов `asyncio.run` в `market_scanner` и дочерних модулях. |
| M3 | **API `/api/positions` без фильтра** смешивал режимы в одном списке (для UI это приемлемо, но неочевидно). | Добавлен опциональный query `?mode=paper|live`. |
| M4 | **Публичные read-only эндпоинты:** `GET /api/settings`, `GET /api/trades`, `GET /api/equity`, `GET /api/risk/fund`, `GET /api/scan/snapshot`, `GET /api/autonomy/status` раскрывают режим, баланс paper, историю, снимок сканера. | Для production: сеть/VPN, либо расширить защиту тем же секретом или JWT. |
| M5 | **`GET /api/trading/control`** без секрета — видно pause/reason. | Низкий риск; при желании закрыть секретом. |

### ML / данные

| ID | Проблема | Комментарий |
|----|-----------|-------------|
| ML1 | Калибровка и порядок классов XGBoost (`predict_proba`) — при смене обучающего пайплайна нужно проверять соответствие «класс 0/1» стороне сделки. | Регрессионные тесты на фикстурах после retrain. |
| ML2 | LSTM как эвристика поверх ряда цен — дрейф режима рынка снижает стационарность; meta-filter и regime multipliers частично компенсируют, но не гарантируют edge. | Walk-forward / мониторинг распределения фичей. |

---

## 2. Внесённые исправления (код)

- **`backend/app/services/bot_engine.py`**
  - `pos_mode = "paper" if paper else "live"` для подсчёта открытых позиций, списка `open_positions`, корреляции и фондовых проверок.
  - `_open_paper` / `_open_live`: проверка дубликата по `(symbol, mode)`.
  - `_manage_open`: только позиции текущего режима; начисление на `virtual_balance` и метрики по `pos.mode`.
  - Закрытие сделки: `TradeRecord` с фильтром `mode == pos.mode`.
- **`backend/app/api/routes_data.py`**
  - `GET /api/positions?mode=paper|live` — опциональная фильтрация.
- **`backend/app/api/routes_bot.py`**
  - `POST /start`, `/stop` — только с `X-API-Secret`.
- **`backend/app/api/routes_settings.py`**
  - `PATCH /api/settings` — только с секретом (`GET` пока без секрета для дашборда).
- **`backend/app/api/routes_ml.py`**
  - Все POST — только с секретом.
- **`backend/app/api/routes_portfolio.py`**
  - `POST /allocate` — только с секретом.
- **`backend/app/api/routes_risk.py`**
  - Удалён баг с `get_settings()` в `_equity_usdt`.

**Эксплуатация после P4:** в UI ввести **API Secret** (значение `API_SECRET` из `.env`, по умолчанию в dev — `dev-secret`) и сохранить, иначе кнопки «Старт/Стоп», сохранение настроек, ML и портфель вернут **401**.

---

## 3. Улучшения (рекомендации, не обязательно сделаны)

1. **Live:** периодически синхронизировать позиции с Bybit (`fetch_positions`) и закрывать/обновлять БД по факту биржи.
2. **Идемпотентность ордеров:** client order id / проверка частичного исполнения перед записью `Position`.
3. **Тесты:** unit-тесты на разделение paper/live в одной БД (фикстуры с двумя открытыми позициями на разные режимы).
4. **Sentry:** breadcrumbs на открытие/закрытие с `symbol`, `mode`, `strategy_id`.
5. **Prometheus:** алерты по `scalper_seconds_since_last_trade_open` только если это продуктово осмысленно (скальпер может долго не входить).

---

## 4. Критические риски (деньги, риск, ML, исполнение)

### Потеря денег / ошибки исполнения

- Включение **live** без подтверждения фразы и ключей — прямые рыночные ордера с плечом.
- **Расхождение** цены исполнения, SL/TP на бирже и логики в БД по свечам 1m.
- **Пропуск** реального ликвидации или ручного закрытия на бирже — БД может показывать открытую позицию.
- Ошибки сети после частично успешного `create_order` — двойный вход без идемпотентности.

### Risk management

- Лимиты фонда (`fund_risk`), корреляции, drawdown и smart pause — хороший слой, но зависят от корректности **equity** (live: `fetch_balance`) и от **неперемешанных** позиций по режиму (после фикса P1).
- `max_open_positions` и exposure считаются от отфильтрованного портфеля режима — обязательно держать `paper_mode` в настройках согласованным с ожиданиями оператора.

### ML

- Переобучение на истории, утечка будущего в фичах при неверном walk-forward.
- Низкая робастность при смене волатильности/ликвидности альтов.
- Meta-filter и LSTM могут **усилить** уверенность на шуме — мониторить `signal_rejects` и фактический PnL по стратегиям (`strategy_performance`).

### Execution

- Проскальзывание моделируется (`execution_model.apply_execution_price`), реальность может отличаться.
- Ключи в БД (Fernet) — компрометация `SECRET_KEY` или дампа БД равна компрометации ключей.

---

## 5. Полное описание системы

### 5.1 Архитектура backend

- **Точка входа:** `backend/app/main.py` — FastAPI, CORS, `MetricsMiddleware`, lifespan: логирование, Sentry, `create_all`, дефолтный `BotSettings`, APScheduler (`retrain_scheduler`), `bot_engine.ensure_worker()`, shutdown останавливает планировщик и поток бота.
- **БД:** SQLAlchemy; локально часто SQLite (`database.py`, `check_same_thread=False` для фонового потока). Модели: `BotSettings`, `Position`, `TradeRecord`, `EquityPoint`, `BybitApiCredentials`, и др.
- **API-роуты:** `routes_settings`, `routes_bot`, `routes_data`, `routes_ml`, `routes_scan`, `routes_strategies`, `routes_trading_control`, `routes_risk`, `routes_portfolio`, `routes_keys`, `routes_health`, `routes_autonomy`, и т.д.
- **`bot_engine`:** singleton `engine`; фоновый цикл `_loop`: пока `bot_enabled`, вызывает `_tick`, затем `run_self_improve`, sleep `scan_interval_sec`.

### 5.2 Как работает `bot_engine`

- Читает `BotSettings` (paper/live, риск, плечи, пороги ML, флаги сканера).
- Для live подтягивает equity с биржи; для paper — `virtual_balance` + залог по открытым paper-позициям.
- Резолвит список символов (`resolve_scan_symbols`, опционально полный USDT perpetual).
- По каждому символу: OHLCV → фичи → режим рынка → выбор стратегии (`strategy_selector`) → rule или ML hybrid (XGBoost + LSTM + meta + sentiment/news context).
- Фильтры: edge, confidence, MACD, meta, корреляция с **уже открытыми позициями этого режима**, fund limits, exposure, drawdown, trading control pause.
- Лучший кандидат → расчёт SL/TP/трейла (`risk.default_stops`, `compute_position_size`) → `_open_paper` или `_open_live`.
- `_manage_open` обновляет трейлинг и закрывает по SL/TP/trail, обновляет `TradeRecord`, метрики, `trade_journal`, `strategy_performance`.

### 5.3 Scanner

- **`market_scanner`:** список символов из настроек, тикеры с публичного exchange, фильтр ликвидности, кэш OHLCV с TTL и мягкой уборкой, параллельная загрузка (asyncio) где применимо.
- Снимок для UI/API: `scan_state.set_snapshot` — используется `GET /api/scan/snapshot`.

### 5.4 Стратегии

- Регистр `RULE_STRATEGY_REGISTRY` в `app/strategies/`: `mean_reversion`, `trend_following`, `volatility_breakout`, и т.д.
- **`strategy_selector.select_strategy_for_regime`:** по `MarketRegime` и снапшоту ADX/волатильности выбирает rule-стратегию или форсит `ml_hybrid` (`STRATEGY_ROUTER_MODE`, `MULTI_STRATEGY_ENABLED`).
- Если rule не дал сигнала — fallback на `ml_hybrid`.

### 5.5 ML

- **XGBoost** (`ml/predictor.py`): вероятности вверх/вниз по табличным фичам; explain для JSON в сделку.
- **LSTM** (`ml/lstm_model.py`): скор по ряду close/volume, смешивается с XGB edge.
- **Meta-filter** (`meta_filter.py`): дополнительная вероятность «стоит ли входить».
- **Regime** (`ml/regime.py`): классификация режима, мультипликаторы размера/edge.
- **Обучение / retrain:** APScheduler + эндпоинты ML (см. `routes_ml`).

### 5.6 Торговля

- **Выбор актива:** скан → кандидаты с `selection_score` / portfolio tilt → лучший после всех гейтов.
- **Открытие:** paper — списание маржи с `virtual_balance`, запись `Position`+`TradeRecord`; live — `create_order` через ccxt, при ошибке — `TradeRecord` со статусом `failed`.
- **Сопровождение:** обновление high/low/trail, выход по цене последней свечи vs SL/TP/trail.
- **Закрытие:** PnL%, USDT, комиссии из настроек; для paper возврат маржи + PnL; обновление записи сделки и удаление `Position`.
- **Риск:** `risk_per_trade_pct`, `max_leverage`, режимные множители, `dynamic_risk`, `fund_risk`, корреляции.

### 5.7 UI (frontend)

- Vite + React, `Dashboard.jsx`: настройки, paper/live, старт/стоп бота, секрет API, подтверждение real mode, график equity, таблицы сделок и позиций, снимок скана, сводка стратегий, trading pause/resume, бэктест, обучение модели.
- `api.js` — axios к backend (`VITE_API_BASE`).

### 5.8 Автоматизация и мониторинг

- **Watchdog** (`app/services/watchdog.py`): опрашивает `/api/health/ready` и `/api/health/watchdog` (длительность тика, возраст последнего открытия, pause); опционально `WATCHDOG_RESTART_CMD`.
- **Alerts** (`alerts.py`): Telegram и лог.
- **Prometheus:** `/metrics`, счётчики сделок, отклонений сигналов, длительность тика и др.
- **Langfuse:** трейс решений (`trace_trade_decision`).
- **Deploy:** см. `docs/PRODUCTION_ORCHESTRATION.md`, `deploy/*`.

---

## 6. Поток работы (step-by-step)

1. **Получение данных:** публичный Bybit API — тикеры, OHLCV по списку символов (кэш, фильтр ликвидности); опционально новости/sentiment (Firecrawl/HF).
2. **Анализ рынка:** построение фич, классификация режима, расчёт ATR, ликвидности, композитного скора.
3. **Выбор стратегии:** `strategy_selector` по режиму; для rule — вызов функции стратегии; иначе/фолбэк — `ml_hybrid`.
4. **Принятие решения:** combined edge, confidence, MACD, meta, корреляция, fund/exposure, drawdown, smart pause; выбор лучшего символа.
5. **Открытие сделки:** размер позиции, плечо, SL/TP; paper — БД; live — ордер на биржу + БД при успехе.
6. **Сопровождение:** на каждом тике для открытых позиций **текущего режима** — новая цена, обновление трейла, проверка условий выхода.
7. **Закрытие:** расчёт PnL, комиссий, обновление `TradeRecord`, удаление `Position`, метрики, журнал, `strategy_performance`.
8. **Логирование:** структурные логи, Sentry при исключениях, Prometheus, опционально Langfuse; equity snapshot в `EquityPoint`.

---

## 7. Проверка работы (чек-лист)

- Backend: из `backend` с **venv** (`./.venv/Scripts/python` или `start.ps1`) — `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- API: `GET /api/health/ready`, `GET /api/health/watchdog`, `GET /api/scan/snapshot`, `GET /api/strategies/summary`, `GET /api/positions?mode=paper`.
- Scanner: снимок в UI или `/api/scan/snapshot` после включения `bot_enabled`.
- Торговая логика: сначала только **paper**, сравнение количества позиций с `max_open_positions` при смешанной БД (после фикса — независимо по режимам).
- UI: обновление каждые 15 с, график equity, таблица позиций с полем `mode`.

---

*Версия документа: 2026-03-28. При значимых изменениях кода обновляйте разделы 1–2 и при необходимости поток в §6.*
