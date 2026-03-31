# Execution layer (live Bybit) — сверка, ордера, метрики

Цель: **источник истины по позициям — биржа**, БД — отражение + аудит; избежать «открытой позиции в БД при пустом счёте» и двойных входов.

## Компоненты

| Модуль | Назначение |
|--------|------------|
| `app/services/exchange_sync.py` | `fetch_positions_from_bybit()`, `sync_positions_with_db()`, `fetch_position_marks_for_symbols()`, `attempt_reduce_only_market_close()` |
| `app/services/order_tracker.py` | `track_order_status()`, `update_trade_record()`, `verify_position_opened_after_order()` |
| `app/services/bot_engine.py` | Live: pending → ордер с `orderLinkId` → трек → **failsafe** подтверждение позиции → только тогда `Position`; `_manage_open`: периодическая сверка, mark из `fetch_positions`, `fetch_open_orders`, выход через `reduceOnly` |
| `app/database_migrations.py` | `run_execution_schema_migrations()` после `create_all`: новые колонки + пересборка SQLite `positions` при отсутствии `data_source` (UNIQUE `symbol`+`mode`) |

## Поток входа (live)

1. `TradeRecord` со статусом **`pending`**, `client_order_id` = UUID hex (32 символа), `order_status=pending`.
2. `create_order(..., params={orderLinkId, stopLoss, takeProfit})`.
3. При исключении: `TradeRecord` → `failed`, **нет** `Position`, метрика `execution_orders_failed_total{reason="create_exception"}`.
4. `track_order_status` — обновление `exchange_order_id`, `filled_contracts`, `order_status` (в т.ч. partial).
5. `verify_position_opened_after_order` — опрос `fetch_positions_from_bybit` до `LIVE_ORDER_CONFIRM_TIMEOUT_SEC`. Нет позиции → `failed`, **`no_position_confirm`**, **нет** строки `Position`.
6. При успехе: `Position` с `data_source=exchange`, поля маржи/контрактов с биржи, `TradeRecord` → `open`, `order_status=filled`.

Идемпотентность: повторный вход по тому же символу блокируется открытым `pending`/`open` по `TradeRecord` и по `Position`. Новый `orderLinkId` каждый раз.

## Поток сопровождения (live)

- Каждые `EXCHANGE_SYNC_INTERVAL_SEC` (если `EXCHANGE_SYNC_ENABLED`): `sync_positions_with_db` — закрытие фантомов в БД, усыновление сирот, правка размера/входа.
- Каждый тик: mark-цена из **одного** `fetch_positions` (кэш по символам открытых позиций), fallback на 1m OHLCV; `fetch_open_orders` по символам (диагностика, лог).
- Условие выхода (SL/TP/trail): перед закрытием в БД вызывается **`attempt_reduce_only_market_close`** (reduceOnly market). При ошибке API позиция **не** удаляется из БД в этом тике.

## Частичное исполнение

- Для market-входа редко; `track_order_status` обновляет `filled_contracts` и пересчитывает маржу/`entry_price` у открытой `Position` при статусе `open`.

## Prometheus

| Метрика | Смысл |
|---------|--------|
| `scalper_execution_orders_submitted_total` | Попытки входа |
| `scalper_execution_orders_failed_total{reason}` | `create_exception`, `no_position_confirm` |
| `scalper_execution_orders_filled_total` | Переход ордера в filled (без дублей при повторном треке) |
| `scalper_execution_order_partial_total` | Частичные исполнения |
| `scalper_execution_sync_mismatch_total{kind}` | `phantom_db`, `size`, `entry`, `orphan_adopted` |

Примеры PromQL:

- Доля неуспешных входов:  
  `rate(scalper_execution_orders_failed_total[5m]) / rate(scalper_execution_orders_submitted_total[5m])`
- Частичные:  
  `rate(scalper_execution_order_partial_total[5m]) / rate(scalper_execution_orders_submitted_total[5m])`

## Переменные окружения

См. `.env.example`: `EXCHANGE_SYNC_*`, `LIVE_ORDER_CONFIRM_*`.

## SQLite / миграции

При первом запуске после обновления старый `dev.db` без колонок: автоматическая пересборка `positions` (данные копируются). Рекомендуется бэкап. PostgreSQL: `ADD COLUMN IF NOT EXISTS` для новых полей.

## UI

Дашборд: у позиций и сделок отображаются **режим**, **источник данных** (`db` / `exchange`), **статус ордера**, сокращённый **client order id**, **mark** для позиций.

## Sentry

Исключения при `create_order`, сверке, reduceOnly и в `track_order_status` отправляются через `sentry_sdk.capture_exception` там, где перехват без подавления бизнес-логики.
