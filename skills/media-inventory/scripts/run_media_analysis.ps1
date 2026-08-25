param(
    [Parameter(Mandatory = $true)]
    [string]$RootPath
)

$ErrorActionPreference = 'Stop'
$uiLauncher = Join-Path $PSScriptRoot 'run_media_analysis_ui.ps1'

if (-not (Test-Path -LiteralPath $uiLauncher -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "MEDIA_ANALYSIS_ERROR missing_ui_launcher=$uiLauncher"
    )
    exit 2
}

& $uiLauncher -RootPath $RootPath
exit $LASTEXITCODE
