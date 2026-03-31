# Мониторинг: Sentry + Datadog

## Sentry

- **Переменные:** `SENTRY_DSN_BACKEND`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_PROFILES_SAMPLE_RATE`.
- **Инициализация:** `app/monitoring/sentry_setup.py` — FastAPI + SQLAlchemy интеграции.
- **Бизнес-события:** `capture_trade_event()` для сделок/алертов (опционально вызывать из `bot_engine`).

## Datadog (метрики)

Приложение шлёт **DogStatsD** (UDP) на `DD_AGENT_HOST:DD_DOGSTATSD_PORT` (по умолчанию 8125).

- **Включение:** `METRICS_ENABLED=true`, задать `DD_AGENT_HOST` (например `localhost` или `datadog` в Docker-сети).
- **Префикс метрик:** `METRICS_PREFIX` (по умолчанию `bybit_scalper`).
- **Теги по умолчанию:** `METRICS_DEFAULT_TAGS=env:prod,region:eu` (через запятую `key:value`).

### Автоматические метрики

- `bybit_scalper.http.request.duration_ms` — время ответа API (ms), теги: `method`, `path`, `status`.
- `bybit_scalper.http.request.count` — счётчик запросов, теги: `method`, `status_class`.

`/api/health` не учитывается (без шума для probes).

### Локально с Agent

```bash
docker compose -f docker-compose.yml -f docker-compose.datadog.yml --profile datadog up -d
```

В `.env` нужны `DD_API_KEY`, `METRICS_ENABLED=true`, `DD_AGENT_HOST=datadog` (подставляется override-файлом).

### Health для оркестраторов

- `GET /api/health` — liveness (лёгкий).
- `GET /api/health/ready` — readiness + проверка PostgreSQL (`SELECT 1`).

Используйте в **ECS/ALB**: liveness = `/api/health`, readiness = `/api/health/ready`.

## Алерты (рекомендация)

В Datadog: мониторы на рост 5xx, latency p95, ошибки Sentry; бизнес-алерты — по метрикам из `capture_trade_event` / кастомным `incr()` (расширение позже).
