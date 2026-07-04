#!/usr/bin/env pwsh
# Тонкая обёртка: запускает бенчмарк через общий .venv репозитория.
# Все аргументы пробрасываются в `python -m benchmark.run`, напр.:
#   .\benchmark\run.ps1 --sample 5
#   .\benchmark\run.ps1 --only-category structural --modes auto
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot   # metalcrow/
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Push-Location $repo
try { & $py -m benchmark.run @args }
finally { Pop-Location }
