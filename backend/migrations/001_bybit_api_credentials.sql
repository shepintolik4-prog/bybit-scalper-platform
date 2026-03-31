-- Таблица зашифрованных API-ключей Bybit (Fernet).
-- Применение: psql $DATABASE_URL -f migrations/001_bybit_api_credentials.sql
-- Либо полагайтесь на SQLAlchemy create_all при старте приложения.

CREATE TABLE IF NOT EXISTS bybit_api_credentials (
    id INTEGER PRIMARY KEY,
    api_key_enc TEXT NOT NULL,
    api_secret_enc TEXT NOT NULL,
    is_testnet BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

COMMENT ON TABLE bybit_api_credentials IS 'Одна логическая запись id=1; ключи только в зашифрованном виде';
