"""
Идемпотентные патчи схемы БД (SQLite / PostgreSQL) для execution-слоя.
SQLite: если нет positions.data_source — пересборка positions (UNIQUE symbol+mode + новые поля).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("scalper.migrations")


def _sqlite_table_sql(conn: Any, name: str) -> str | None:
    row = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).fetchone()
    return row[0] if row else None


def _sqlite_rebuild_positions(engine: Engine) -> None:
    """Снять старый UNIQUE(symbol), ввести UNIQUE(symbol, mode)."""
    ddl_new = """
    CREATE TABLE positions_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        symbol VARCHAR(64) NOT NULL,
        side VARCHAR(8) NOT NULL,
        entry_price FLOAT NOT NULL,
        size_usdt FLOAT NOT NULL,
        leverage INTEGER NOT NULL,
        stop_loss FLOAT NOT NULL,
        take_profit FLOAT NOT NULL,
        highest_price FLOAT,
        lowest_price FLOAT,
        trail_price FLOAT,
        opened_at DATETIME NOT NULL,
        explanation_json TEXT,
        mode VARCHAR(16) NOT NULL DEFAULT 'paper',
        exchange_order_id VARCHAR(64),
        client_order_id VARCHAR(64),
        contracts_qty FLOAT,
        data_source VARCHAR(16) DEFAULT 'db',
        last_mark_price FLOAT,
        last_exchange_sync_at DATETIME,
        lifecycle_state VARCHAR(16) DEFAULT 'filled',
        CONSTRAINT uq_positions_symbol_mode UNIQUE (symbol, mode)
    );
    """
    with engine.begin() as conn:
        if not _sqlite_table_sql(conn, "positions"):
            return
        conn.execute(text(ddl_new))
        conn.execute(
            text(
                """
            INSERT INTO positions_new (
                id, symbol, side, entry_price, size_usdt, leverage, stop_loss, take_profit,
                highest_price, lowest_price, trail_price, opened_at, explanation_json, mode
            )
            SELECT
                id, symbol, side, entry_price, size_usdt, leverage, stop_loss, take_profit,
                highest_price, lowest_price, trail_price, opened_at, explanation_json,
                COALESCE(mode, 'paper')
            FROM positions
            """
            )
        )
        conn.execute(text("DROP TABLE positions"))
        conn.execute(text("ALTER TABLE positions_new RENAME TO positions"))
    logger.warning("SQLite: таблица positions пересобрана (UNIQUE symbol+mode + execution columns)")


def _sqlite_column_names(conn: Any, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {str(r[1]) for r in rows}


def _add_sqlite_columns(engine: Engine, table: str, defs: list[tuple[str, str]]) -> None:
    with engine.begin() as conn:
        existing = _sqlite_column_names(conn, table)
        for col, typ in defs:
            if col in existing:
                continue
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                logger.info("SQLite: %s.%s добавлена", table, col)
            except Exception as e:
                logger.warning("SQLite ADD COLUMN %s.%s: %s", table, col, e)


def _pg_add_columns(engine: Engine, table: str, defs: list[tuple[str, str]]) -> None:
    with engine.begin() as conn:
        for col, typ in defs:
            try:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS {col} {typ}'))
            except Exception as e:
                logger.warning("PG ADD COLUMN %s.%s: %s", table, col, e)


def run_execution_schema_migrations(engine: Engine) -> None:
    trades_cols = [
        ("exchange_order_id", "VARCHAR(64)"),
        ("client_order_id", "VARCHAR(64)"),
        ("order_status", "VARCHAR(24) DEFAULT 'unknown'"),
        ("filled_contracts", "FLOAT DEFAULT 0"),
        ("data_source", "VARCHAR(16) DEFAULT 'db'"),
    ]
    positions_extra = [
        ("exchange_order_id", "VARCHAR(64)"),
        ("client_order_id", "VARCHAR(64)"),
        ("contracts_qty", "FLOAT"),
        ("data_source", "VARCHAR(16) DEFAULT 'db'"),
        ("last_mark_price", "FLOAT"),
        ("last_exchange_sync_at", "DATETIME"),
    ]
    lifecycle_cols = [
        ("lifecycle_state", "VARCHAR(16) DEFAULT 'filled'"),
    ]

    dialect = engine.dialect.name
    if dialect == "sqlite":
        insp = inspect(engine)
        if insp.has_table("positions"):
            with engine.connect() as conn:
                cols = _sqlite_column_names(conn, "positions")
            if "data_source" not in cols:
                _sqlite_rebuild_positions(engine)
        _add_sqlite_columns(engine, "trades", trades_cols)
        _add_sqlite_columns(engine, "positions", positions_extra)
        _add_sqlite_columns(engine, "trades", lifecycle_cols)
        _add_sqlite_columns(engine, "positions", lifecycle_cols)
    else:
        _pg_add_columns(engine, "trades", trades_cols)
        _pg_add_columns(engine, "positions", positions_extra)
        _pg_add_columns(engine, "trades", lifecycle_cols)
        _pg_add_columns(engine, "positions", lifecycle_cols)

    _backfill_lifecycle_state(engine)


def _backfill_lifecycle_state(engine: Engine) -> None:
    """Согласовать lifecycle_state с legacy status/order_status."""
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE trades SET lifecycle_state = 'pending' "
                    "WHERE status = 'pending' AND (lifecycle_state IS NULL OR lifecycle_state = 'filled')"
                )
            )
            conn.execute(
                text(
                    "UPDATE trades SET lifecycle_state = 'failed' "
                    "WHERE status = 'failed' AND (lifecycle_state IS NULL OR lifecycle_state = 'filled')"
                )
            )
            conn.execute(
                text(
                    "UPDATE trades SET lifecycle_state = 'closed' "
                    "WHERE status = 'closed' AND (lifecycle_state IS NULL OR lifecycle_state = 'filled')"
                )
            )
            conn.execute(
                text(
                    "UPDATE trades SET lifecycle_state = 'partial' "
                    "WHERE status = 'open' AND LOWER(COALESCE(order_status,'')) = 'partially_filled' "
                    "AND (lifecycle_state IS NULL OR lifecycle_state = 'filled')"
                )
            )
            conn.execute(
                text(
                    "UPDATE trades SET lifecycle_state = 'filled' "
                    "WHERE status = 'open' AND LOWER(COALESCE(order_status,'')) <> 'partially_filled' "
                    "AND (lifecycle_state IS NULL OR lifecycle_state = 'filled')"
                )
            )
    except Exception as e:
        logger.warning("backfill lifecycle_state: %s", e)
