$ErrorActionPreference = "Stop"
& "$PSScriptRoot\python.ps1" -m ruff check app tests
exit $LASTEXITCODE
