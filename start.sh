#!/usr/bin/env bash
# Запуск стека локально: PostgreSQL должен быть доступен (docker-compose или локальный порт).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/backend/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "$ROOT/backend/.venv/bin/activate"
fi

echo "[start] backend uvicorn :8000"
(cd "$ROOT/backend" && exec uvicorn app.main:app --host 0.0.0.0 --port 8000) &
PID_BE=$!

echo "[start] frontend vite :5173"
(cd "$ROOT/frontend" && exec npm run dev) &
PID_FE=$!

echo "[start] monitoring: см. docs/AUTONOMOUS_FUND.md (Prometheus/Grafana docker-compose при необходимости)"
trap 'kill $PID_BE $PID_FE 2>/dev/null; exit' INT TERM
wait
