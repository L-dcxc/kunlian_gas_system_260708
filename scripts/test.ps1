$ErrorActionPreference = "Stop"
& "$PSScriptRoot\python.ps1" -m unittest discover -s tests
exit $LASTEXITCODE
