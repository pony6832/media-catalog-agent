param(
    [Parameter(Mandatory = $true)]
    [string]$RootPath
)

$ErrorActionPreference = 'Stop'
$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimePython = Join-Path $skillRoot '.runtime\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "MEDIA_ANALYSIS_ERROR missing_skill_runtime=$runtimePython"
    )
    exit 2
}

& $runtimePython -m media_catalog.cli analyze-all $RootPath --skill-root $skillRoot
exit $LASTEXITCODE
