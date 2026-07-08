param(
    [string]$AppPath = "",
    [string]$Python = "py",
    [string[]]$PythonArgs = @("-3")
)

$ErrorActionPreference = "Stop"

function Convert-ToSafeOutput {
    param([object[]]$Output)

    $text = ($Output | ForEach-Object { [string]$_ }) -join "`n"
    $text = $text -replace "(?i)(password|passwd|pwd|authorization_code|auth_code|license_code|api[_-]?token|token|secret|key|machine[_-]?id|machine[_-]?code|hardware[_-]?id)\s*[:=]\s*[^\s,;]+", '$1=<redacted>'
    $text = $text -replace "(?i)(?<![A-Za-z])[A-Z]:[\\/][^\s,;]+", "<path>"
    if ($text.Length -gt 800) {
        return $text.Substring(0, 800)
    }
    return $text
}

function Invoke-AppSmoke {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [string[]]$BaseArgs = @(),
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $Executable @BaseArgs @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        $safeOutput = Convert-ToSafeOutput -Output $output
        throw "Smoke command failed. Sanitized output: $safeOutput"
    }
}

function Test-RequiredDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $target = Join-Path $Root $Name
    if (-not (Test-Path -LiteralPath $target -PathType Container)) {
        throw "Missing runtime directory: $Name"
    }
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$dataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("gas_alarm_packaging_smoke_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

try {
    if ($AppPath) {
        $resolvedApp = Resolve-Path $AppPath
        $executable = $resolvedApp.Path
        $baseArgs = @()
    } else {
        $executable = $Python
        $baseArgs = $PythonArgs + @((Join-Path $repoRoot "app\main.py"))
    }

    Invoke-AppSmoke -Executable $executable -BaseArgs $baseArgs -Arguments @("--data-dir", $dataDir, "--platform-smoke")
    Invoke-AppSmoke -Executable $executable -BaseArgs $baseArgs -Arguments @("--data-dir", $dataDir, "--smoke-shell")

    foreach ($name in @("maps", "backups", "logs", "config", "db")) {
        Test-RequiredDirectory -Root $dataDir -Name $name
    }

    $configPath = Join-Path (Join-Path $dataDir "config") "config.json"
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "Missing runtime config file"
    }
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($config.runtime.debug -ne $false) {
        throw "Runtime DEBUG default is not false"
    }
    if ($config.api.bind_address -ne "127.0.0.1") {
        throw "API default bind address is not loopback"
    }
    if ($config.api.enabled -ne $false) {
        throw "API default enabled flag is not false"
    }

    Write-Host "Packaging smoke passed: platform=true shell=true dirs=maps,backups,logs,config,db debug=false api=loopback"
} finally {
    Remove-Item -LiteralPath $dataDir -Recurse -Force -ErrorAction SilentlyContinue
}
