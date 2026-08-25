param(
    [string]$RootPath = ''
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimePythonw = Join-Path $skillRoot '.runtime\Scripts\pythonw.exe'

if (-not (Test-Path -LiteralPath $runtimePythonw -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "MEDIA_STATUS_UI_ERROR missing_skill_runtime=$runtimePythonw"
    )
    exit 2
}

$uiArguments = @(
    '-m', 'media_catalog.status_ui',
    '--skill-root', $skillRoot
)
if (-not [string]::IsNullOrWhiteSpace($RootPath)) {
    $uiArguments += @('--root', $RootPath)
}

& $runtimePythonw @uiArguments
exit $LASTEXITCODE
