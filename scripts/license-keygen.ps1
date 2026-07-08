$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PythonSelector = Join-Path $ScriptDir "python.ps1"

& $PythonSelector (Join-Path $RepoRoot "tools\license_keygen_gui.py")
