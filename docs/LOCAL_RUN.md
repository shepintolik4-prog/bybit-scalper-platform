# Локальный запуск без Docker

Используйте, если Docker Desktop недоступен или не запущен.

## 1. Backend

```powershell
cd bybit-scalper-platform\backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

Скопируйте `backend\.env` из репозитория (или создайте): в нём заданы `DATABASE_URL=sqlite:///./dev.db`, `SECRET_KEY`, `API_SECRET`.

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

Проверка: `curl.exe http://127.0.0.1:8000/api/health`

Ключи: `curl.exe -H "X-API-Secret: <ваш API_SECRET>" http://127.0.0.1:8000/api/keys/bybit/status`

> На Windows для HTTP-запросов надёжнее `curl.exe`, чем `Invoke-WebRequest` (прокси/SSL).

## 2. Frontend

```powershell
cd bybit-scalper-platform\frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Откройте http://127.0.0.1:5173/ и http://127.0.0.1:5173/settings/api-keys

На дашборде в поле **API Secret** укажите то же значение, что и `API_SECRET` в `backend/.env` (в примере часто `dev-secret`), нажмите «Сохранить секрет в браузере». Без этого заголовка `X-API-Secret` вернут **401**: старт/стоп бота, `PATCH /api/settings`, обучение ML, бэктест и `POST /api/portfolio/allocate`.

## 3. Docker (когда доступен)

```powershell
cd bybit-scalper-platform
copy .env.example .env
docker compose up -d --build
```

Backend: http://localhost:8000 · UI через nginx: http://localhost:5173
