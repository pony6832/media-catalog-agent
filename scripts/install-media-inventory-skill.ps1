param(
    [string]$Destination = (Join-Path $env:USERPROFILE '.codex\skills\media-inventory'),
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$backupPath = $null

try {
    $projectRootPath = (Resolve-Path -LiteralPath $ProjectRoot -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $projectRootPath -PathType Container)) {
        throw "ProjectRoot 不是資料夾：$projectRootPath"
    }

    $sourceSkill = Join-Path $projectRootPath 'skills\media-inventory'
    if (-not (Test-Path -LiteralPath $sourceSkill -PathType Container)) {
        throw "找不到來源 Skill：$sourceSkill"
    }

    $destinationFull = [IO.Path]::GetFullPath($Destination)
    if ([IO.Path]::GetFileName($destinationFull) -ne 'media-inventory') {
        throw "Destination 必須以 media-inventory 為資料夾名稱：$destinationFull"
    }

    $destinationParent = Split-Path -Parent $destinationFull
    if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    }
    $destinationParent = (Resolve-Path -LiteralPath $destinationParent -ErrorAction Stop).Path
    $destinationFull = Join-Path $destinationParent 'media-inventory'

    $sourceResolved = (Resolve-Path -LiteralPath $sourceSkill -ErrorAction Stop).Path
    if ($destinationFull -eq $sourceResolved) {
        throw 'Destination 不能與專案來源 Skill 相同'
    }

    if (Test-Path -LiteralPath $destinationFull) {
        $destinationItem = Get-Item -LiteralPath $destinationFull -Force
        if (-not $destinationItem.PSIsContainer) {
            throw "Destination 已存在但不是資料夾：$destinationFull"
        }
        if ($destinationItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Destination 不可為符號連結或 reparse point：$destinationFull"
        }

        $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupPath = Join-Path $destinationParent "media-inventory.backup-$timestamp"
        if (Test-Path -LiteralPath $backupPath) {
            throw "備份路徑已存在，停止安裝：$backupPath"
        }
        Move-Item -LiteralPath $destinationFull -Destination $backupPath -ErrorAction Stop
    }

    Copy-Item -LiteralPath $sourceResolved -Destination $destinationFull -Recurse -ErrorAction Stop

    $pythonCommand = @(Get-Command python -CommandType Application -ErrorAction Stop)[0]
    $runtimeRoot = Join-Path $destinationFull '.runtime'
    & $pythonCommand.Source -m venv $runtimeRoot
    if ($LASTEXITCODE -ne 0) {
        throw "建立私有 Python 環境失敗，exit=$LASTEXITCODE"
    }

    $runtimePython = Join-Path $runtimeRoot 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
        throw "找不到私有 Python：$runtimePython"
    }

    & $runtimePython -m pip install $projectRootPath
    if ($LASTEXITCODE -ne 0) {
        throw "安裝本機 media-catalog 專案失敗，exit=$LASTEXITCODE"
    }

    $quickValidator = Join-Path $env:USERPROFILE '.codex\skills\.system\skill-creator\scripts\quick_validate.py'
    if (-not (Test-Path -LiteralPath $quickValidator -PathType Leaf)) {
        throw "找不到 Skill 驗證器：$quickValidator"
    }
    & $pythonCommand.Source $quickValidator $destinationFull
    if ($LASTEXITCODE -ne 0) {
        throw "Skill 套件驗證失敗，exit=$LASTEXITCODE"
    }

    $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $smokeRoot = Join-Path $temporaryBase ("media-inventory-smoke-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $smokeRoot -ErrorAction Stop | Out-Null
    [IO.File]::WriteAllBytes((Join-Path $smokeRoot 'sample.jpg'), [byte[]](1, 2, 3))

    $launcher = Join-Path $destinationFull 'scripts\run_media_catalog.ps1'
    $smokeOutput = & $launcher -RootPath $smokeRoot 2>&1
    $smokeExitCode = $LASTEXITCODE
    if ($smokeExitCode -ne 0 -or ($smokeOutput -join "`n") -notmatch 'MEDIA_CATALOG_READY') {
        throw "Skill 啟動測試失敗，exit=$smokeExitCode output=$($smokeOutput -join ' ')"
    }

    $backupLabel = if ($null -eq $backupPath) { 'none' } else { $backupPath }
    Write-Output "MEDIA_INVENTORY_SKILL_READY destination=$destinationFull backup=$backupLabel"
}
catch {
    $backupLabel = if ($null -eq $backupPath) { 'none' } else { $backupPath }
    [Console]::Error.WriteLine(
        "MEDIA_INVENTORY_SKILL_ERROR backup=$backupLabel error=$($_.Exception.Message)"
    )
    exit 1
}
