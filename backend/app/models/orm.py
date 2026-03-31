from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    paper_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    bot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    leverage: Mapped[int] = mapped_column(Integer, default=5)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=1.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=15.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=3)
    real_mode_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    virtual_balance: Mapped[float] = mapped_column(Float, default=10000.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TradeRecord(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_usdt: Mapped[float] = mapped_column(Float)
    pnl_usdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    trail_active: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    explanation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="paper")
    status: Mapped[str] = mapped_column(String(16), default="open")
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_status: Mapped[str] = mapped_column(String(24), default="unknown")
    filled_contracts: Mapped[float] = mapped_column(Float, default=0.0)
    data_source: Mapped[str] = mapped_column(String(16), default="db")
    lifecycle_state: Mapped[str] = mapped_column(String(16), default="filled", index=True)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("symbol", "mode", name="uq_positions_symbol_mode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(64), index=True)
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(Float)
    size_usdt: Mapped[float] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer, default=5)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float] = mapped_column(Float)
    highest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    lowest_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    trail_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    explanation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="paper")
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    contracts_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_source: Mapped[str] = mapped_column(String(16), default="db")
    last_mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_exchange_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(16), default="filled", index=True)


class EquityPoint(Base):
    __tablename__ = "equity_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    equity: Mapped[float] = mapped_column(Float)
    balance: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(16), default="paper")


class BybitApiCredentials(Base):
    """Одна строка id=1: API key/secret в виде Fernet-токенов."""

    __tablename__ = "bybit_api_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    is_testnet: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
