# Обёртка для PowerShell: в PowerShell команда `bash` это WSL, а нужен Git Bash с вашими SSH-ключами.
#   .\scripts\set_secret.ps1 TELEGRAM_BOT_TOKEN
param([Parameter(Mandatory = $true)][string]$Key)
$candidates = @(
  "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe",
  "$env:LOCALAPPDATA\Programs\Git\usr\bin\bash.exe",
  "C:\Program Files\Git\bin\bash.exe"
)
$bash = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $bash) { Write-Error "Git Bash не найден"; exit 1 }
& $bash -l "$PSScriptRoot/set_secret.sh" $Key
exit $LASTEXITCODE
