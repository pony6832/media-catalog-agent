param(
    [Parameter(Mandatory = $true)]
    [string]$RootPath
)

$ErrorActionPreference = 'Stop'

$skillRoot = Split-Path -Parent $PSScriptRoot
$runtimePython = Join-Path $skillRoot '.runtime\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "MEDIA_CATALOG_ERROR 找不到 Skill 私有執行環境：$runtimePython"
    )
    exit 2
}

& $runtimePython -m media_catalog.cli start -- $RootPath
exit $LASTEXITCODE
