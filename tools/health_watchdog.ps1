# Шаблон внешнего self-heal: проверка готовности API и опциональный рестарт.
# Не изменяет исходный код; настройте $RestartCommand под свой способ запуска.
param(
  [string]$BaseUrl = "http://127.0.0.1:8000",
  [int]$IntervalSec = 60,
  [string]$RestartCommand = ""
)
$ready = "$BaseUrl/api/health/ready"
while ($true) {
  try {
    $r = Invoke-WebRequest -Uri $ready -UseBasicParsing -TimeoutSec 15
    if ($r.StatusCode -ne 200) { throw "status $($r.StatusCode)" }
  } catch {
    Write-Host "$(Get-Date -Format o) READY FAIL: $_"
    if ($RestartCommand) {
      Invoke-Expression $RestartCommand
    }
  }
  Start-Sleep -Seconds $IntervalSec
}
