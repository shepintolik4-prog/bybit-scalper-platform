# Bybit Scalper ML — автоматический скальпинг (USDT perpetual)

Полнофункциональный каркас: **Python (FastAPI)**, **PostgreSQL**, **React (Vite)**, **Docker**, **XGBoost + опционально LSTM (PyTorch)**, интеграция **Bybit** через `ccxt`, опционально **Sentry**.

## Возможности

- Сканирование списка USDT perpetual, сигналы по индикаторам (RSI, MACD, ATR, волатильность, объём) + **XGBoost** + лёгкий **LSTM** (если есть вес `lstm_scalper.pt`).
- **Paper / Live**: тестовый виртуальный баланс и реальные ордера (с двойной защитой).
- **Live execution:** сверка позиций с Bybit, `orderLinkId`, failsafe подтверждение входа, reduceOnly при выходе по логике бота — см. **`docs/EXECUTION_LAYER.md`**.
- **Риск**: размер позиции от % риска, лимит просадки, макс. позиций, плечо (до 10x по умолчанию).
- **SL / TP / трейлинг** на основе ATR и логики трейла в `services/risk.py` + `bot_engine.py`.
- **Объяснение сделок**: JSON в БД (фичи, вероятности, top features XGBoost, LSTM).
- **Бэктест** и **обучение** XGBoost через API (`/api/ml/...`).
- **Панель на русском** (графики эквити, таблицы, настройки).

## Институциональная архитектура и портфель

- Целевая модель платформы (слои, observability, AWS, research): **`docs/INSTITUTIONAL_ARCHITECTURE.md`**.
- **Портфельный менеджер:** `backend/app/services/portfolio_manager.py` — inverse-vol, **risk parity (ERC)**, **min-variance**, **mean-variance**, сжатие ковариации, клип по корреляциям, **динамическая аллокация** по вектору сигналов (`PORTFOLIO_SIGNAL_TEMPERATURE` в `.env`).
- **HTTP:** `POST /api/portfolio/allocate` (тело: `symbols`, `equity_usdt`, `lookback`, `method`, опционально `signal_scores`), `GET /api/portfolio/methods`.
- **Quant-fund слой:** `docs/QUANT_FUND_RISK.md` — лимиты концентрации и альт-кластера, опциональный vol-targeting по эквити, выбор кандидата с **ERC + tilt** при нескольких сигналах (`FUND_*` в `.env`). Снимок книги: **`GET /api/risk/fund`**.
- Для оптимизации используется **scipy** (`requirements.txt`).

## Production: supervisor / systemd / PM2 / watchdog

- Полная инструкция: **`docs/PRODUCTION_ORCHESTRATION.md`**
- Шаблоны: `deploy/supervisord.conf`, `deploy/systemd/*.service`, `deploy/ecosystem.config.cjs` (PM2)
- Внешний watchdog: `WATCHDOG_ENABLED=true`, затем из каталога `backend`: `python -m app.services.watchdog`
- Мульти-стратегии (тренд / флэт / breakout / ML): `backend/app/strategies/`, маршрутизатор `app/services/strategy_selector.py`
- API: `GET /api/strategies/summary`, `GET /api/trading/control`, `GET /api/health/watchdog`

## Структура

```
bybit-scalper-platform/
├── docker-compose.yml
├── .env.example
├── deploy/
│   ├── supervisord.conf
│   ├── ecosystem.config.cjs
│   └── systemd/
├── docs/
│   ├── INSTITUTIONAL_ARCHITECTURE.md
│   ├── MONITORING.md
│   ├── QUANT_FUND_RISK.md
│   ├── PRODUCTION_ORCHESTRATION.md
│   └── API_KEYS.md
├── infra/aws/            # ECS, Terraform-каркас, README по деплою
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── ml/           # фичи, XGBoost, LSTM
│   │   ├── models/       # SQLAlchemy ORM
│   │   ├── schemas/
│   │   └── services/     # Bybit, бот, бэктест
│   ├── models/           # веса ML (xgboost_signal.json, lstm_scalper.pt)
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/             # React + Recharts
└── scripts/setup.ps1
```

## Локально без Docker (SQLite)

Если Docker не запущен: см. **`docs/LOCAL_RUN.md`** — venv в `backend/`, файл `backend/.env` с `DATABASE_URL=sqlite:///./dev.db`, затем `uvicorn` и `npm run dev`.

## Быстрый старт (Docker)

1. Скопируйте окружение:

   ```powershell
   cd bybit-scalper-platform
   .\scripts\setup.ps1
   ```

2. Заполните `.env`: ключи **Bybit testnet** (`BYBIT_TESTNET=true`), при необходимости `SENTRY_DSN_BACKEND`, `API_SECRET`.

3. Запуск:

   ```powershell
   docker compose up --build
   ```

4. Откройте UI: **http://localhost:5173** (nginx проксирует `/api` на бэкенд).

5. API напрямую: **http://localhost:8000/docs**

## Локально без Docker (разработка)

- Поднимите PostgreSQL (порт в примере `5433`).
- `cd backend && python -m venv .venv && .\.venv\Scripts\activate`
- `pip install -r requirements.txt`
- `copy ..\.env.example ..\.env` и выставьте `DATABASE_URL` на локальный Postgres.
- `uvicorn app.main:app --reload --app-dir .` из каталога `backend` (или `PYTHONPATH=backend`).

Фронт: `cd frontend && npm install && npm run dev` — прокси на `localhost:8000` уже в `vite.config.js`.

## Реальная торговля

1. В `.env`: `BYBIT_TESTNET=false`, боевые ключи, **`CONFIRM_REAL_TRADING=true`**.
2. В UI: ввести `API_SECRET`, фразу **`ENABLE_LIVE`**, вызвать «Включить реальный режим» (заголовок `X-API-Secret`).

## Vercel (фронтенд)

- Сборка: `cd frontend && npm run build`.
- Задайте переменную **`VITE_API_BASE`** = URL вашего бэкенда (HTTPS).
- Секрет передаётся с фронта только в браузере пользователя — храните `API_SECRET` вне репозитория.

## Мониторинг (Sentry + Datadog)

Подробно: **`docs/MONITORING.md`**.

- **Sentry:** `SENTRY_DSN_BACKEND`, `SENTRY_ENVIRONMENT`, sample rates — см. `.env.example`.
- **Метрики (DogStatsD):** `METRICS_ENABLED=true`, `DD_AGENT_HOST` (например `localhost` или `datadog` в Docker-сети), опционально `METRICS_PREFIX`, `METRICS_DEFAULT_TAGS`.
- **Локально с Agent:** `docker compose -f docker-compose.yml -f docker-compose.datadog.yml --profile datadog up -d` (нужен `DD_API_KEY` в `.env`).
- **Пробы:** `GET /api/health` (liveness), `GET /api/health/ready` (readiness + PostgreSQL).

Фронт: `VITE_SENTRY_DSN` при сборке (опционально).

## AWS (прод)

Черновик развёртывания: **`infra/aws/README.md`** (ECR, ECS Fargate, RDS, Secrets Manager, ALB, sidecar Datadog). Пример task definition: `infra/aws/ecs/task-definition.json`. Terraform-каркас: `infra/aws/terraform/`.

## Firecrawl / Browserbase

В каркас не включены отдельные сервисы: при необходимости добавьте сбор новостей/страниц через Firecrawl MCP и фоновые задачи в FastAPI; Browserbase — для сценариев с браузером. В README зафиксировано как точка расширения.

## Команды API (кратко)

| Метод | Путь | Описание |
|--------|------|----------|
| GET/PATCH | `/api/settings` | Настройки |
| POST | `/api/settings/real-mode` | Реальный режим (+ `X-API-Secret`) |
| POST | `/api/bot/start` `/stop` | Вкл/выкл торговую логику |
| GET | `/api/risk/fund` | Снимок quant-риска (gross, концентрация, лимиты) |
| GET/POST/DELETE | `/api/keys/bybit`, `/api/keys/bybit/status`, `/api/keys/bybit/verify` | Ключи Bybit (зашифровано в БД; нужен `X-API-Secret`; см. `docs/API_KEYS.md`) |
| GET | `/api/trades`, `/api/positions`, `/api/equity` | Данные |
| POST | `/api/ml/backtest?symbol=...` | Бэктест |
| POST | `/api/ml/train?symbol=...` | Обучение и сохранение XGBoost |

## Алгоритмический уровень (фонд)

- **Regime**: ADX + DI, кластер волатильности (short/long σ), режимы `high_volatility` / `trend_*` / `flat`; множители edge/SL/размера (`config.py` / `.env`).
- **Walk-forward**: `app/ml/walk_forward.py` + `POST /api/ml/backtest/realistic` — скользящие окна, **purge** между train и test, исполнение с **spread/slippage/latency/fees**.
- **Meta-filter**: `meta_xgboost.json`, обучение `POST /api/ml/train/meta` (стек фич + режим + edge).
- **Динамический риск**: `dynamic_risk.py` — сжатие при просадке от пика, мягкий буст при росте к `paper_initial_balance`.
- **Корреляции**: кэш матрицы доходностей, порог `CORR_MAX_PAIR`, суммарная экспозиция `MAX_TOTAL_EXPOSURE_RATIO`.
- **Логи**: `LOG_LEVEL`, строки `signal_reject` с причиной; исполнение в `explanation.execution` / `close`.

## Заметки по укреплению (риск / ML / стратегия)

- **Эквити в paper**: виртуальный баланс — свободная маржа; NAV = свободно + залог по открытым позициям; дневной стоп и просадка считаются по NAV.
- **Фильтры**: минимальный `combined` edge (`SIGNAL_MIN_EDGE`), порог уверенности модели (`MIN_MODEL_CONFIDENCE`), диапазон `ATR_PCT_*`, опционально согласование с MACD (`USE_MACD_MOMENTUM_FILTER`).
- **Позиция**: потолок маржи от эквити (`MAX_MARGIN_PCT_EQUITY`), минимальный RR на уровне стопов (`MIN_RISK_REWARD`), дневной circuit breaker (`DAILY_LOSS_LIMIT_PCT`).
- **Live**: позиция в БД создаётся только после успешного ордера; неудача логируется как `failed`.
- **XGBoost**: обучение с `sample_weight` по балансу классов; расширенный набор фич (`atr_pct`, `ret5`). После смены фич переобучите модель (`/api/ml/train`).

## Предупреждение

Торговля криптовалютами сопряжена с риском. Тестируйте на testnet/paper. Авторы шаблона не несут ответственности за убытки.
"# bybit-scalper-platform" 
