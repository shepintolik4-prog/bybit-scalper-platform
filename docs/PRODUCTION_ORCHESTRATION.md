# Production: supervisor, watchdog, алерты, стратегии, Grafana

## Процессы

| Компонент | Назначение |
|-----------|------------|
| `uvicorn app.main:app` | API + встроенный цикл бота |
| `npm run preview` (или dev) | React UI |
| `python -m app.services.watchdog` | Внешний health / алерты / опциональный рестарт |

### Linux — systemd

Файлы-шаблоны: `deploy/systemd/*.service`. Скопируйте в `/etc/systemd/system/`, поправьте `User`, `WorkingDirectory`, `ExecStart`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bybit-scalper-backend.service
sudo systemctl enable --now bybit-scalper-frontend.service
# опционально:
sudo systemctl enable --now bybit-scalper-watchdog.service
```

### Linux — supervisord

См. `deploy/supervisord.conf` (include из основного `supervisord.conf`).

### Windows — PM2

```powershell
npm install -g pm2
# В ecosystem.config.cjs замените script на `.venv\Scripts\python.exe` (уже для Windows) или на `python` из venv Linux.
pm2 start deploy/ecosystem.config.cjs
pm2 save
pm2 startup
```

### Windows — NSSM (службы)

1. Установите [NSSM](https://nssm.cc/).
2. **Application** → Path: `C:\...\backend\.venv\Scripts\python.exe`  
   **Arguments**: `-m uvicorn app.main:app --host 0.0.0.0 --port 8000`  
   **Startup directory**: `...\backend`
3. Аналогично вторую службу для `npm run preview` (Path: `npm`, Args: `run preview -- --host 0.0.0.0 --port 5173`, cwd: `frontend`).

Авто-редактирование кода **не используется**; только рестарт процесса.

---

## Watchdog

Модуль: `app/services/watchdog.py`.

Переменные окружения (см. `.env.example`):

- `WATCHDOG_ENABLED=true` — обязательно для цикла.
- `WATCHDOG_API_BASE` — URL API (по умолчанию `http://127.0.0.1:8000`).
- `WATCHDOG_INTERVAL_SEC`, `WATCHDOG_NO_TRADE_SEC`, `WATCHDOG_MAX_TICK_SEC`.
- `WATCHDOG_RESTART_CMD` — shell-команда рестарта backend (пусто = только алерт).

Проверки:

- `GET /api/health/ready`
- `GET /api/health/watchdog` — `last_tick_seconds`, возраст последней сделки, флаг паузы.

---

## Алерты

`app/services/alerts.py`:

- Лог-файл: `ALERT_LOG_PATH` (по умолчанию `data/alerts.log`).
- Telegram: `ALERT_TELEGRAM_BOT_TOKEN`, `ALERT_TELEGRAM_CHAT_ID`.

Sentry остаётся для исключений; правила «ошибки растут» настраиваются в Sentry/Prometheus Alertmanager.

---

## Smart pause

Файл `TRADING_CONTROL_PATH` (`data/trading_control.json`):

- `smart_pause_drawdown_pct` — пауза **новых** входов при просадке от пика (порог отдельно от жёсткого `max_drawdown` в настройках БД).
- `smart_pause_equity_vol_mult` — отношение краткой vol к длинной по ряду equity.
- `smart_pause_auto_clear_sec` — авто-снятие smart-паузы после стабилизации (0 = выкл.).

API (секрет `X-API-Secret`):

- `GET /api/trading/control`
- `POST /api/trading/pause` — тело `{ "reason": "..." }`
- `POST /api/trading/resume`

---

## Мульти-стратегии

- Каталог `backend/app/strategies/`: trend following, mean reversion, volatility breakout.
- `ml_hybrid` — XGBoost + LSTM (как раньше).
- Маршрутизатор: `app/services/strategy_selector.py` по `MarketRegime`.
- `STRATEGY_ROUTER_MODE=ml_only` — отключить rule-стратегии.
- Статистика и авто-отключение слабых: `data/strategy_stats.json`, пороги `STRATEGY_MIN_TRADES_FOR_DISABLE`, `STRATEGY_MIN_WINRATE_DISABLE`.
- API: `GET /api/strategies/summary`, `POST /api/strategies/enable` (с секретом).

---

## Prometheus / Grafana

Метрики: `GET /metrics` (как задано в `PROMETHEUS_METRICS_PATH`).

Дополнительно:

- `scalper_scan_fetch_seconds` — длительность загрузки OHLCV за цикл.
- `scalper_bot_ticks_total`
- `scalper_portfolio_drawdown_pct`
- `scalper_strategy_router_selections_total{strategy_id}`
- `scalper_strategy_winrate{strategy_id}`, `scalper_strategy_closed_trades{strategy_id}`

Grafana: datasource Prometheus → импорт панелей вручную или из `monitoring/prometheus_alerts.example.yml` для Alertmanager.

---

## NSSM / пути

Все пути в `deploy/*` — **шаблоны**. Замените `/opt/scalper` или пользователя `scalper` на свои.
