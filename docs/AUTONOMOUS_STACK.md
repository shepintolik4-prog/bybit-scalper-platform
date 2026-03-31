# Автономный контур (scalper backend)

Интеграции и поведение:

| Компонент | Назначение |
|-----------|------------|
| **Firecrawl** | `NEWS_URLS` → scrape markdown, тексты в sentiment |
| **Hugging Face** | `transformers` pipeline sentiment (локально), опционально `HF_TOKEN` |
| **Langfuse** | Трейсы открытия сделки (`trade_open`) с полным `explanation_json` |
| **Sentry** | Уже подключён в `app/monitoring/sentry_setup.py` |
| **Prometheus** | `GET /metrics` (см. `PROMETHEUS_METRICS_PATH`) — счётчики сделок, reject, equity gauge, гистограмма PnL, длительность тика |
| **Grafana** | Подключите Prometheus как datasource; импортируйте дашборд по метрикам `scalper_*` |
| **APScheduler** | `AUTO_RETRAIN_ENABLED` — переобучение XGB по интервалу |
| **Self-improve** | По закрытым сделкам сдвигается `edge_bias` в `ADAPTIVE_STATE_PATH` |

## Включение

1. Скопируйте переменные из `.env.example` в `.env` backend.
2. Для новостей: `NEWS_ENABLED=true`, `FIRECRAWL_API_KEY`, `NEWS_URLS`.
3. Для sentiment: `HF_SENTIMENT_ENABLED=true` (первый запуск скачает модель).
4. Для Langfuse: `LANGFUSE_ENABLED=true` и ключи из проекта Langfuse.
5. Для мониторинга: поднимите Prometheus (см. `docker-compose.monitoring.yml`) и укажите `scrape_configs` на `http://host:PORT/metrics`.

## API

- `GET /api/autonomy/status` — bias, флаги, превью sentiment без полного текста новостей.

## Browserbase

Опционально: `pip install playwright`, `playwright install chromium`, переменные `BROWSERBASE_*`. Функция `app/services/browserbase_client.fetch_rendered_text` для JS-страниц; торговый цикл по умолчанию использует Firecrawl.
