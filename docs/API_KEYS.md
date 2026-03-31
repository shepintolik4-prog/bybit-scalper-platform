# Безопасное хранение API-ключей Bybit

## Поведение

1. Пользователь вводит ключ и секрет в UI (`/settings/api-keys`) с заголовком `X-API-Secret` (совпадает с `API_SECRET` на сервере).
2. Бэкенд проверяет ключи через **ccxt** (`fetch_balance` на выбранном testnet/mainnet).
3. Значения шифруются **Fernet**; мастер-ключ — **`SECRET_KEY`** в `.env` (минимум 16 символов). От производного ключа используется SHA-256 → Fernet.
4. В ответах API **никогда** нет открытых ключей — только статус (`configured`, `source`, `is_testnet`, `credentials_usable`).

## Переменные окружения

| Переменная    | Назначение                                      |
|---------------|-------------------------------------------------|
| `SECRET_KEY`  | Мастер-ключ для шифрования строк в БД           |
| `API_SECRET`  | Секрет для заголовка `X-API-Secret` у клиентов  |
| `BYBIT_*`     | Fallback, если записи в БД нет                  |

Приоритет при торговле: **запись в БД** (если расшифровка успешна) → **переменные окружения**.

## HTTP API

| Метод  | Путь                      | Описание                          |
|--------|---------------------------|-----------------------------------|
| GET    | `/api/keys/bybit/status`  | Статус (требует `X-API-Secret`)   |
| POST   | `/api/keys/bybit`         | Сохранить (тело: ключи + testnet) |
| POST   | `/api/keys/bybit/verify` | Проверить сохранённые или тело    |
| DELETE | `/api/keys/bybit`         | Удалить запись из БД              |
| GET    | `/api/keys/bybit/source-debug` | Только `active_source` (без секретов) |

Метрики DogStatsD: `api.keys.bybit` (счётчики save/verify/delete). Ошибки — в Sentry.

## Миграция БД

Файл: `backend/migrations/001_bybit_api_credentials.sql`.  
В разработке таблица создаётся автоматически через `Base.metadata.create_all`.

## Ротация и прод

- Смена **`SECRET_KEY`** делает старые ciphertext нечитаемыми — сохраните ключи заново через UI.
- **AWS:** храните `SECRET_KEY` в [Secrets Manager](https://docs.aws.amazon.com/secretsmanager/); в ECS подставляйте в task definition из секрета. Долгосрочно — шифрование KMS + отдельный data key для колонок (вне рамок текущего каркаса).

## Реальный режим

Включение live (`POST /api/settings/real-mode`) дополнительно требует **`acknowledge_risks: true`** в теле запроса и прежние проверки (`CONFIRM_REAL_TRADING`, фраза `ENABLE_LIVE`).
