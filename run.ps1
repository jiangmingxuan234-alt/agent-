param(
    [Parameter(Mandatory = $true)]
    [string]$Repo,

    [Parameter(Mandatory = $true)]
    [string]$Request,

    [string]$TestCommand = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Workflow = Join-Path $ProjectRoot ".venv\Scripts\specialist-workflow.exe"

if (-not (Test-Path $Workflow)) {
    throw "Workflow is not installed. Run .\install.ps1 first."
}

& $Workflow run --repo $Repo --request $Request --test-command $TestCommand

