$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location ..
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Создан .env из .env.example — заполните BYBIT_API_KEY / BYBIT_API_SECRET"
}
New-Item -ItemType Directory -Force -Path "backend/models" | Out-Null
Write-Host "Готово. Далее: docker compose up --build"
