# Quant-fund слой риска

Цель — приблизить поведение к **multi-manager / PM**: не только «лучший сигнал», но и **книга** (концентрация, кластеры ликвидности), опционально **vol-targeting** по истории эквити и **ERC + tilt** при выборе кандидата среди нескольких прошедших фильтры инструментов.

## Модули

| Компонент | Файл | Назначение |
|-----------|------|------------|
| Лимиты PM | `app/services/fund_risk.py` | Доля крупнейшего имени в gross notional, потолок доли «альт»-кластера относительно мейджоров |
| Vol overlay | `fund_risk.vol_scale_from_equity_history` | По точкам `equity_points`: если относительная волатильность эквити выше порога — уменьшение размера новой позиции |
| ERC-выбор | `pick_candidate_with_portfolio_tilt` | При нескольких кандидатах: оценка ковариации по 5m-доходностям, **risk parity ERC** + dynamic tilt по сигналу (`portfolio_signal_temperature`) |
| Снимок API | `GET /api/risk/fund` | Текущая gross-книга, доли, лимиты |

## Переменные окружения (см. `.env.example`)

- **`FUND_RISK_ENABLED`** — мастер-переключатель лимитов концентрации/кластера (по умолчанию true).
- **`FUND_MAX_SINGLE_NAME_GROSS_PCT`** — максимальная доля одного имени в суммарном gross (маржа×плечо), напр. `0.52`.
- **`FUND_MAX_ALT_CLUSTER_SHARE`** — максимальная доля номинала вне мейджоров; `1.0` = выкл.
- **`FUND_MAJOR_SYMBOLS`** — список ccxt-символов мейджоров (BTC/ETH по умолчанию).
- **`FUND_VOL_TARGETING_ENABLED`**, **`FUND_VOL_REL_THRESHOLD`**, **`FUND_VOL_SIZE_SCALE_FLOOR`** — vol overlay.
- **`FUND_PORTFOLIO_TILT_ENABLED`** — при нескольких кандидатах за тик выбирать не `argmax |edge|`, а максимум `|edge| × ERC_weight` (с учётом tilt).
- **`FUND_PORTFOLIO_TILT_METHOD`** — метод базовых весов (`risk_parity_erc`, `inverse_volatility`, …).
- **`FUND_TILT_W_MAX`**, **`FUND_TILT_MAX_PAIR_CORRELATION`** — ограничения оптимизатора при tilt.

## Отказы в логе

События `signal_reject` с `reason=fund_limit` и полем `detail` (концентрация или alt cap). При отключённом `fund_risk_enabled` проверки не режут сделки.

## Связь с остальным стеком

- **Корреляция пар** (`CORR_MAX_PAIR`) и **потолок экспозиции** (`MAX_TOTAL_EXPOSURE_RATIO`) остаются в `correlation_service` / `bot_engine` до слоя fund.
- **Портфельный API** (`POST /api/portfolio/allocate`) — ручная/оффлайн аллокация; **tilt в боте** использует ту же математику `PortfolioManager`, но внутри цикла по кандидатам.
