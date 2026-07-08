$ErrorActionPreference = "Stop"
& "$PSScriptRoot\python.ps1" app/main.py
exit $LASTEXITCODE
