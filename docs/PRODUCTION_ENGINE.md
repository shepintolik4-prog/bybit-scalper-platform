# Production trading engine (bybit-scalper-platform)

Слой «фондового» уровня поверх существующего бота: **сначала защита капитала**, затем оптимизация прибыли.

## State machine

Файл: `backend/app/models/trade_state.py` — enum `PositionLifecycleState`, граф переходов `NEW → PENDING → PARTIAL|FILLED → CLOSING → CLOSED` и `FAILED`.

Поля БД: `trades.lifecycle_state`, `positions.lifecycle_state` (миграция в `database_migrations.py`).

## Централизация и гонки

- `backend/app/services/state_manager.py` — единая точка смены `lifecycle_state`, `symbol_operation_lock` (per `symbol+mode`, `threading.RLock`, таймаут 30s).
- Метрики: `scalper_race_condition_detected_total`, `scalper_invalid_state_transition_total`.

## Kill switch

`backend/app/services/kill_switch.py` — условия: просадка, всплеск ошибок REST, волатильность equity (ATR spike), минимальная equity.

Действие: `set_pause(..., source="kill_switch")`, алерт, Sentry, шина событий, `scalper_kill_switch_triggered_total{reason=...}` (низкая кардинальность).

## Event bus

`backend/app/services/event_bus.py` — `signal_generated`, `order_created`, `order_filled`, `position_closed` (+ kill/consistency).

## Consistency

`backend/app/services/consistency_checks.py` — сравнение открытых сделок/позиций, live: баланс и позиции с биржей. Счётчик `scalper_consistency_errors_total{check=...}`.

## Risk guards

`backend/app/services/risk_guards.py` — сделок в минуту, circuit breaker после серии сбоев, лимит гросс-экспозиции на символ.

## Profit engine

`backend/app/services/profit_engine.py`:

- динамический порог edge и пол confidence (просадка + статистика стратегии);
- масштаб размера позиции (confidence, ATR, просадка, winrate стратегии);
- бонус диверсификации в скоринге кандидатов;
- метрики окна (Sharpe proxy по equity, winrate, expectancy, лучшая стратегия);
- `apply_adaptive_learning` — шаг `min_confidence_floor` в `adaptive_state.json`.

Интеграция — в `bot_engine._tick_body_inner` и панель скана (`strategy_panel`).

## API и UI

- `GET /api/system/status` — `health` (OK / WARNING / STOPPED), kill switch, circuit breaker, последняя сверка.
- Дашборд: блок состояния системы, колонки Lifecycle в позициях и сделках.
- Prometheus: `/metrics` — перечисленные выше счётчики и `scalper_system_health`.

## Переменные окружения

См. блок в корневом `.env.example` (`KILL_SWITCH_*`, `MAX_TRADES_PER_MINUTE`, `CONSISTENCY_*`, `PROFIT_*`, …).
