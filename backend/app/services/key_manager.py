"""
Шифрование API-ключей Bybit (Fernet, мастер-ключ SECRET_KEY из .env).
Секреты никогда не логируются и не отдаются в HTTP-ответах.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime

import ccxt
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import BybitApiCredentials

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    s = get_settings()
    raw = (s.secret_key or "").strip()
    if len(raw) < 16:
        raise ValueError("SECRET_KEY в .env обязателен для хранения ключей (минимум 16 символов)")
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_key(plain: str) -> str:
    """Возвращает токен Fernet (строка) для хранения в БД."""
    if not plain:
        raise ValueError("empty key material")
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_key(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Не удалось расшифровать ключи (SECRET_KEY изменён или данные повреждены)") from e


def validate_bybit_keys(api_key: str, api_secret: str, *, is_testnet: bool) -> None:
    """Проверка ключей через ccxt (fetch_balance)."""
    opts: dict = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    }
    ex = ccxt.bybit(opts)
    if is_testnet:
        ex.set_sandbox_mode(True)
    ex.fetch_balance()


def save_keys(
    db: Session,
    api_key: str,
    api_secret: str,
    *,
    is_testnet: bool,
) -> BybitApiCredentials:
    validate_bybit_keys(api_key, api_secret, is_testnet=is_testnet)
    enc_k = encrypt_key(api_key)
    enc_s = encrypt_key(api_secret)
    row = db.query(BybitApiCredentials).filter_by(id=1).first()
    now = datetime.utcnow()
    if row:
        row.api_key_enc = enc_k
        row.api_secret_enc = enc_s
        row.is_testnet = is_testnet
        row.updated_at = now
    else:
        row = BybitApiCredentials(
            id=1,
            api_key_enc=enc_k,
            api_secret_enc=enc_s,
            is_testnet=is_testnet,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return row


def load_keys(db: Session) -> tuple[str, str, bool] | None:
    """Расшифровка активной записи id=1 или None."""
    row = db.query(BybitApiCredentials).filter_by(id=1).first()
    if not row:
        return None
    try:
        k = decrypt_key(row.api_key_enc)
        s = decrypt_key(row.api_secret_enc)
        return k, s, bool(row.is_testnet)
    except ValueError:
        logger.warning("bybit_stored_keys_decrypt_failed")
        return None


def delete_keys(db: Session) -> bool:
    row = db.query(BybitApiCredentials).filter_by(id=1).first()
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def get_credential_source(db: Session | None) -> tuple[str, str, bool, str]:
    """
    Активные ключи для торговли и источник: database | environment.
    Приоритет: зашифрованная запись в БД, иначе .env.
    Если запись в БД есть, но расшифровка не удалась — лог и fallback на .env.
    """
    s = get_settings()
    if db is not None:
        row = db.query(BybitApiCredentials).filter_by(id=1).first()
        if row:
            loaded = load_keys(db)
            if loaded:
                k, sec, tn = loaded
                return k, sec, tn, "database"
            logger.warning(
                "bybit_stored_credentials_present_but_unusable_check_secret_key",
                extra={"id": row.id},
            )
    if (s.bybit_api_key or "").strip() and (s.bybit_api_secret or "").strip():
        return s.bybit_api_key, s.bybit_api_secret, s.bybit_testnet, "environment"
    return "", "", bool(s.bybit_testnet), "none"
