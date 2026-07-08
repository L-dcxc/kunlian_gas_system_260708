$ErrorActionPreference = "Stop"
& "$PSScriptRoot\python.ps1" -m ruff format app tests
exit $LASTEXITCODE
