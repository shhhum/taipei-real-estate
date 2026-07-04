<#
.SYNOPSIS
    Run the Taipei real-estate pipeline unbuffered, logging to runs/<timestamp>.log.

.DESCRIPTION
    Resolves the project interpreter (.venv, falling back to `uv run`), forces
    unbuffered UTF-8 output, and streams stdout+stderr into a timestamped log
    under runs/ (gitignored). Any extra arguments are passed through to
    `python -m src.main`.

.EXAMPLE
    ./scripts/run.ps1 --dry-run
        Dry run (no Airtable writes), logged to runs/2026-07-04-1132.log.

.EXAMPLE
    $env:SITE_591_LIMIT = "50"; ./scripts/run.ps1 --dry-run
        Capped dry run.

.NOTES
    Tail the log live from another terminal:  Get-Content <log> -Wait -Tail 20
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PassThruArgs
)

$ErrorActionPreference = 'Stop'

# Repo root = parent of this script's directory.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Resolve the interpreter: prefer the project venv, fall back to `uv run`.
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPy) {
    $exe = $venvPy
    $runArgs = @('-u', '-m', 'src.main') + $PassThruArgs
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    $exe = 'uv'
    $runArgs = @('run', 'python', '-u', '-m', 'src.main') + $PassThruArgs
} else {
    throw "No interpreter found: expected .venv\Scripts\python.exe or uv on PATH."
}

# Timestamped log under runs/ (gitignored).
$logDir = Join-Path $root 'runs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format 'yyyy-MM-dd-HHmm'
$log = Join-Path $logDir "$stamp.log"

# Unbuffered, UTF-8 output so Chinese district names survive and progress
# flushes live into the log.
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONIOENCODING = 'utf-8'

Write-Host "Interpreter : $exe"
Write-Host "Command     : $exe $($runArgs -join ' ')"
Write-Host "Log         : $log"
Write-Host "Tail live   : Get-Content `"$log`" -Wait -Tail 20"
Write-Host ""

# Merge stdout+stderr at the OS level via cmd. Doing the 2>&1 inside cmd (rather
# than PowerShell) keeps native stderr from being wrapped as NativeCommandError,
# writes raw UTF-8 bytes to the log, and propagates python's real exit code.
$quoted = ($runArgs | ForEach-Object {
    if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
}) -join ' '
& cmd /c "`"$exe`" $quoted > `"$log`" 2>&1"
$code = $LASTEXITCODE

Write-Host ""
Write-Host "Exit code   : $code"
Write-Host "Log written : $log"
exit $code
