from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SettingsOut(BaseModel):
    paper_mode: bool
    bot_enabled: bool
    leverage: int
    risk_per_trade_pct: float
    max_drawdown_pct: float
    max_open_positions: int
    virtual_balance: float
    real_available: bool = Field(description="Можно ли включить реальный режим (env + подтверждение)")
    bot_runtime: dict[str, Any] = Field(
        default_factory=dict,
        description="Текущие параметры из .env (без секретов) — только чтение для панели",
    )


class SettingsIn(BaseModel):
    paper_mode: bool | None = None
    bot_enabled: bool | None = None
    leverage: int | None = Field(None, ge=1, le=125)
    risk_per_trade_pct: float | None = Field(None, ge=0.1, le=10)
    # 0 = отключить ограничение max drawdown.
    max_drawdown_pct: float | None = Field(None, ge=0, le=90)
    max_open_positions: int | None = Field(None, ge=1, le=50)
    virtual_balance: float | None = Field(None, ge=100)


class TradeOut(BaseModel):
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: float | None
    size_usdt: float
    pnl_usdt: float | None
    pnl_pct: float | None
    opened_at: datetime
    closed_at: datetime | None
    explanation: dict[str, Any] | None
    mode: str
    status: str
    lifecycle_state: str = "filled"
    exchange_order_id: str | None = None
    client_order_id: str | None = None
    order_status: str = "unknown"
    filled_contracts: float = 0.0
    data_source: str = "db"


class ForceCloseBySymbolIn(BaseModel):
    """Тело POST /api/positions/force-close — закрытие по символу (paper или live)."""

    symbol: str = Field(..., min_length=3, description="Например SOL/USDT:USDT")
    mode: str | None = Field(
        None,
        description="paper | live; если на символ одна строка в БД — можно опустить",
    )
    confirm_db_without_exchange: bool = Field(
        False,
        description="Только live: закрыть запись в БД без reduceOnly (если позиция уже снята вручную на бирже)",
    )

    @field_validator("mode")
    @classmethod
    def _mode_ok(cls, v: str | None) -> str | None:
        if v is None:
            return None
        x = v.strip().lower()
        if x not in ("paper", "live"):
            raise ValueError("mode must be paper, live or null")
        return x


class PositionOut(BaseModel):
    id: int
    symbol: str
    side: str
    entry_price: float
    size_usdt: float
    leverage: int
    stop_loss: float
    take_profit: float
    opened_at: datetime
    explanation: dict[str, Any] | None
    mode: str
    lifecycle_state: str = "filled"
    exchange_order_id: str | None = None
    client_order_id: str | None = None
    contracts_qty: float | None = None
    data_source: str = "db"
    last_mark_price: float | None = None
    unrealized_pnl_usdt: float | None = Field(None, description="Оценка нереализ. PnL по last/mark")
    pct_to_take_profit: float | None = Field(None, description="Остаток движения цены к TP, % к mark")
    pct_to_stop_loss: float | None = Field(None, description="Запас до SL, % к mark (меньше — ближе к выбиванию)")


class SystemStatusOut(BaseModel):
    health: str
    kill_switch_active: bool
    trading_paused: bool
    pause_reason: str | None = None
    pause_source: str | None = None
    circuit_breaker_open: bool
    consistency_last_ok: bool
    consistency_issues: list[str] = Field(default_factory=list)
    bot_enabled: bool
    paper_mode: bool
    settings_flags: dict[str, Any] = Field(default_factory=dict)


class EquityPointOut(BaseModel):
    ts: datetime
    equity: float
    balance: float
    mode: str


class BotStatusOut(BaseModel):
    running: bool
    paper_mode: bool
    bot_enabled: bool
    message: str


class RealModeConfirm(BaseModel):
    api_secret: str
    confirm_phrase: str = Field(description="Должно быть: ENABLE_LIVE")
    acknowledge_risks: bool = Field(
        default=False,
        description="Явное подтверждение осведомлённости о рисках реальной торговли",
    )


class BybitKeysIn(BaseModel):
    api_key: str = Field(..., min_length=4)
    api_secret: str = Field(..., min_length=4)
    is_testnet: bool = True


class BybitKeysVerifyIn(BaseModel):
    """Пустое тело — проверить сохранённые ключи; иначе проверить переданные (без сохранения)."""

    api_key: str | None = None
    api_secret: str | None = None
    is_testnet: bool | None = None


class BybitKeyStatusOut(BaseModel):
    configured: bool
    is_testnet: bool | None = None
    source: str = Field(description="database | environment | none")
    credentials_usable: bool | None = Field(
        default=None,
        description="Для source=database: удаётся ли расшифровать (SECRET_KEY)",
    )


class BacktestResult(BaseModel):
    symbol: str
    trades: int
    winrate: float
    pnl_pct: float
    max_dd_pct: float
    model_accuracy: float


class RealisticBacktestOut(BaseModel):
    symbol: str
    trades: int
    winrate: float
    pnl_pct: float
    max_dd_pct: float
    wf_windows: int
    avg_accuracy: float
