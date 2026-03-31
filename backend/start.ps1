# Запуск API из каталога backend (можно вызывать из любой папки: powershell -File "...\backend\start.ps1")
Set-Location $PSScriptRoot
if (-not (Test-Path .\.venv\Scripts\Activate.ps1)) {
    Write-Host "Сначала: python -m venv .venv && .\.venv\Scripts\Activate.ps1 && pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}
& .\.venv\Scripts\Activate.ps1
Write-Host "Каталог: $(Get-Location)" -ForegroundColor Green
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
