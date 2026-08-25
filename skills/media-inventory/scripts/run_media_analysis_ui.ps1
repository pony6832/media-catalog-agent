param(
    [Parameter(Mandatory = $true)]
    [string]$RootPath
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimePython = Join-Path $skillRoot '.runtime\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "MEDIA_STATUS_UI_ERROR missing_skill_runtime=$runtimePython"
    )
    exit 2
}

& $runtimePython -m media_catalog.status_ui --root $RootPath --skill-root $skillRoot
exit $LASTEXITCODE
