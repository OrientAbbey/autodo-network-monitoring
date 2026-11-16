<#
.SYNOPSIS
    Démo AUTODO sans base de données : base locale SQLite + sources DIAGNET simulées (en mémoire).

.DESCRIPTION
    Génère le catalogue simulé (db_config.json), optionnellement un scénario d'anomalie, charge
    demo/env.demo.sqlite puis démarre l'application (http://127.0.0.1:8501 par défaut).

.EXAMPLE
    .\run_demo_sqlite.ps1
    .\run_demo_sqlite.ps1 zero douala_camtel      # injecte une anomalie avant de démarrer
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = if ($env:DEMO_PYTHON) { $env:DEMO_PYTHON } else { Join-Path $repo ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $py)) { $py = "python" }

Push-Location $repo
try {
    & $py -m core.demo_data config (Join-Path $repo "db_config.json")

    if ($args.Count -ge 1) {
        & $py -m core.demo_data scenario @args
    }

    foreach ($line in Get-Content (Join-Path $PSScriptRoot "env.demo.sqlite")) {
        $line = $line.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $parts = $line.Split("=", 2)
        if ($parts.Count -eq 2) {
            [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim().Trim('"'))
        }
    }

    Write-Host "AUTODO démo SQLite — http://127.0.0.1:$($env:APP_PORT_UNIFIED)" -ForegroundColor Green
    & $py main.py
}
finally {
    Pop-Location
}
