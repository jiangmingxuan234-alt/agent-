$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11 or newer is required."
}

if (-not (Test-Path $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e "$ProjectRoot[dev]"

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "Run: .\.venv\Scripts\specialist-workflow.exe doctor"

