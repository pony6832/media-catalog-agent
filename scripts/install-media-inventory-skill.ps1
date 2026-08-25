param(
    [string]$Destination = (Join-Path $env:USERPROFILE '.codex\skills\media-inventory'),
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
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

    & $runtimePython -m pip install --disable-pip-version-check --no-cache-dir --no-compile 'setuptools>=68'
    if ($LASTEXITCODE -ne 0) {
        throw "安裝 setuptools 建置工具失敗，exit=$LASTEXITCODE"
    }

    & $runtimePython -m pip install --disable-pip-version-check --no-cache-dir --no-compile --no-build-isolation $projectRootPath
    if ($LASTEXITCODE -ne 0) {
        throw "安裝本機 media-catalog 專案失敗，exit=$LASTEXITCODE"
    }

    & $runtimePython -c "import tkinter; import media_catalog.status_ui"
    if ($LASTEXITCODE -ne 0) {
        throw "Tkinter 或 Media Catalog A+ 狀態 UI 無法載入，exit=$LASTEXITCODE"
    }

    $nodeCommand = @(Get-Command node -CommandType Application -ErrorAction Stop)[0]
    $npmCommand = @(Get-Command npm.cmd -CommandType Application -ErrorAction Stop)[0]
    $nodeVersion = (& $nodeCommand.Source --version).Trim()
    if ($LASTEXITCODE -ne 0 -or $nodeVersion -notmatch '^v(?<major>\d+)\.') {
        throw "Unable to read Node.js version: $nodeVersion"
    }
    if ([int]$Matches['major'] -lt 18) {
        throw "Node.js 18 or newer is required: $nodeVersion"
    }

    $mcpRoot = Join-Path $destinationFull '.tools\mcp-video-analyzer'
    $npmCache = Join-Path $mcpRoot '.npm-cache'
    New-Item -ItemType Directory -Path $mcpRoot -Force | Out-Null
    & $npmCommand.Source install --prefix $mcpRoot --no-save --omit=dev --cache $npmCache --no-audit --no-fund 'mcp-video-analyzer@0.8.0'
    if ($LASTEXITCODE -ne 0) {
        throw "mcp-video-analyzer install failed: exit=$LASTEXITCODE"
    }

    $mcpExecutable = Join-Path $mcpRoot 'node_modules\.bin\mcp-video-analyzer.cmd'
    $mcpPackagePath = Join-Path $mcpRoot 'node_modules\mcp-video-analyzer\package.json'
    if (-not (Test-Path -LiteralPath $mcpExecutable -PathType Leaf)) {
        throw "Missing mcp-video-analyzer executable: $mcpExecutable"
    }
    if (-not (Test-Path -LiteralPath $mcpPackagePath -PathType Leaf)) {
        throw "Missing mcp-video-analyzer package metadata: $mcpPackagePath"
    }
    $mcpPackage = Get-Content -LiteralPath $mcpPackagePath -Raw | ConvertFrom-Json
    if (
        $mcpPackage.name -ne 'mcp-video-analyzer' -or
        $mcpPackage.version -ne '0.8.0'
    ) {
        throw "Unexpected mcp-video-analyzer package: name=$($mcpPackage.name) version=$($mcpPackage.version)"
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
    $analysisLauncher = Join-Path $destinationFull 'scripts\run_media_analysis.ps1'
    $uiLauncher = Join-Path $destinationFull 'scripts\run_media_analysis_ui.ps1'
    if (-not (Test-Path -LiteralPath $analysisLauncher -PathType Leaf)) {
        throw "Missing media analysis launcher: $analysisLauncher"
    }
    if (-not (Test-Path -LiteralPath $uiLauncher -PathType Leaf)) {
        throw "Missing Media Catalog A+ UI launcher: $uiLauncher"
    }
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
