"""
Управление API-ключами Bybit: только зашифрованное хранение, без утечек в ответах.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import ccxt
import sentry_sdk
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import verify_api_secret
from app.database import get_db
from app.models.orm import BybitApiCredentials
from app.monitoring.statsd_client import incr, record_timing
from app.schemas.dto import BybitKeyStatusOut, BybitKeysIn, BybitKeysVerifyIn
from app.services.key_manager import (
    delete_keys,
    decrypt_key,
    get_credential_source,
    load_keys,
    save_keys,
    validate_bybit_keys,
)
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/keys", tags=["keys"])


def _key_status(db: Session) -> BybitKeyStatusOut:
    s = get_settings()
    row = db.query(BybitApiCredentials).filter_by(id=1).first()
    if row:
        usable: bool | None = None
        try:
            decrypt_key(row.api_key_enc)
            decrypt_key(row.api_secret_enc)
            usable = True
        except ValueError:
            usable = False
        return BybitKeyStatusOut(
            configured=True,
            is_testnet=bool(row.is_testnet),
            source="database",
            credentials_usable=usable,
        )
    env_ok = bool((s.bybit_api_key or "").strip() and (s.bybit_api_secret or "").strip())
    if env_ok:
        return BybitKeyStatusOut(
            configured=True,
            is_testnet=bool(s.bybit_testnet),
            source="environment",
            credentials_usable=True,
        )
    return BybitKeyStatusOut(configured=False, is_testnet=None, source="none", credentials_usable=None)


@router.get("/bybit/status", response_model=BybitKeyStatusOut)
def bybit_keys_status(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> BybitKeyStatusOut:
    return _key_status(db)


@router.post("/bybit", response_model=BybitKeyStatusOut)
def bybit_keys_save(
    body: BybitKeysIn,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> BybitKeyStatusOut:
    t0 = time.perf_counter()
    logger.info(
        "bybit_keys_save_attempt",
        extra={"testnet": body.is_testnet, "outcome": "pending"},
    )
    try:
        save_keys(db, body.api_key.strip(), body.api_secret.strip(), is_testnet=body.is_testnet)
    except ccxt.BaseError as e:
        incr("api.keys.bybit", 1, action="save", result="exchange_error")
        logger.info(
            "bybit_keys_save_attempt",
            extra={"testnet": body.is_testnet, "outcome": "exchange_error", "error": type(e).__name__},
        )
        raise HTTPException(status_code=400, detail=f"Bybit: {type(e).__name__}") from e
    except ValueError as e:
        incr("api.keys.bybit", 1, action="save", result="validation_error")
        logger.info(
            "bybit_keys_save_attempt",
            extra={"testnet": body.is_testnet, "outcome": "validation_error", "error": str(e)[:200]},
        )
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        sentry_sdk.capture_exception(e)
        incr("api.keys.bybit", 1, action="save", result="error")
        logger.info("bybit_keys_save_attempt", extra={"testnet": body.is_testnet, "outcome": "error"})
        raise HTTPException(status_code=500, detail="Ошибка сохранения ключей") from e
    finally:
        record_timing("api.keys.bybit.save_ms", (time.perf_counter() - t0) * 1000.0)

    incr("api.keys.bybit", 1, action="save", result="ok")
    logger.info("bybit_keys_save_attempt", extra={"testnet": body.is_testnet, "outcome": "ok"})
    return _key_status(db)


@router.post("/bybit/verify")
def bybit_keys_verify(
    body: BybitKeysVerifyIn = Body(default_factory=BybitKeysVerifyIn),
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> dict[str, Any]:
    t0 = time.perf_counter()
    b = body
    inline = bool(b.api_key and b.api_secret)
    try:
        if inline:
            if b.is_testnet is None:
                raise HTTPException(status_code=400, detail="Укажите is_testnet для проверки")
            validate_bybit_keys(b.api_key.strip(), b.api_secret.strip(), is_testnet=bool(b.is_testnet))
            logger.info("bybit_keys_verify", extra={"mode": "inline", "outcome": "ok"})
            incr("api.keys.bybit", 1, action="verify", result="ok", mode="inline")
            return {"ok": True, "mode": "inline"}
        loaded = load_keys(db)
        if not loaded:
            s = get_settings()
            if (s.bybit_api_key or "").strip() and (s.bybit_api_secret or "").strip():
                validate_bybit_keys(
                    s.bybit_api_key,
                    s.bybit_api_secret,
                    is_testnet=bool(s.bybit_testnet),
                )
                logger.info("bybit_keys_verify", extra={"mode": "environment", "outcome": "ok"})
                incr("api.keys.bybit", 1, action="verify", result="ok", mode="environment")
                return {"ok": True, "mode": "environment"}
            raise HTTPException(status_code=400, detail="Нет сохранённых ключей и не заданы в .env")
        k, sec, tn = loaded
        validate_bybit_keys(k, sec, is_testnet=tn)
        logger.info("bybit_keys_verify", extra={"mode": "database", "testnet": tn, "outcome": "ok"})
        incr("api.keys.bybit", 1, action="verify", result="ok", mode="database")
        return {"ok": True, "mode": "database", "is_testnet": tn}
    except HTTPException:
        raise
    except ccxt.BaseError as e:
        incr("api.keys.bybit", 1, action="verify", result="fail")
        raise HTTPException(status_code=400, detail=f"Bybit: {type(e).__name__}") from e
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.info("bybit_keys_verify", extra={"outcome": "fail", "error_type": type(e).__name__})
        incr("api.keys.bybit", 1, action="verify", result="fail")
        raise HTTPException(status_code=400, detail=f"Проверка не пройдена: {type(e).__name__}") from e
    finally:
        record_timing("api.keys.bybit.verify_ms", (time.perf_counter() - t0) * 1000.0)


@router.delete("/bybit", response_model=BybitKeyStatusOut)
def bybit_keys_delete(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> BybitKeyStatusOut:
    logger.info("bybit_keys_delete_attempt", extra={"outcome": "pending"})
    if delete_keys(db):
        incr("api.keys.bybit", 1, action="delete", result="ok")
        logger.info("bybit_keys_delete_attempt", extra={"outcome": "ok"})
    else:
        incr("api.keys.bybit", 1, action="delete", result="noop")
        logger.info("bybit_keys_delete_attempt", extra={"outcome": "noop"})
    return _key_status(db)


@router.get("/bybit/source-debug")
def bybit_key_source_debug(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_secret),
) -> dict[str, str]:
    """Только источник ключей, без секретов (для отладки интеграции)."""
    _, _, _, src = get_credential_source(db)
    return {"active_source": src}
