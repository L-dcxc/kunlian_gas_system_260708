param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PythonArgs
)

$ErrorActionPreference = "Stop"
$candidates = @(
    @("python"),
    @("py", "-3"),
    @("python3")
)

foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $probeArgs = @()
    if ($candidate.Count -gt 1) {
        $probeArgs = $candidate[1..($candidate.Count - 1)]
    }

    try {
        & $exe @probeArgs -c "import sys; raise SystemExit(0)" *> $null
    } catch {
        continue
    }

    if ($LASTEXITCODE -eq 0) {
        & $exe @probeArgs @PythonArgs
        exit $LASTEXITCODE
    }
}

Write-Error "No usable Python interpreter found. Tried: python, py -3, python3."
exit 1
