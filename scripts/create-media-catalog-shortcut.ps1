param(
    [Parameter(Mandatory = $true)]
    [string]$SkillRoot,
    [string]$DesktopPath = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()

try {
    $skillResolved = (Resolve-Path -LiteralPath $SkillRoot -ErrorAction Stop).Path
    $skillItem = Get-Item -LiteralPath $skillResolved -Force -ErrorAction Stop
    if (-not $skillItem.PSIsContainer) {
        throw "SkillRoot is not a directory: $skillResolved"
    }
    if ($skillItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "SkillRoot cannot be a reparse point: $skillResolved"
    }

    $uiLauncher = Join-Path $skillResolved 'scripts\run_media_analysis_ui.ps1'
    $runtimePythonw = Join-Path $skillResolved '.runtime\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $uiLauncher -PathType Leaf)) {
        throw "Missing UI launcher: $uiLauncher"
    }
    if (-not (Test-Path -LiteralPath $runtimePythonw -PathType Leaf)) {
        throw "Missing private pythonw.exe: $runtimePythonw"
    }

    if ([string]::IsNullOrWhiteSpace($DesktopPath)) {
        throw 'Desktop path cannot be empty'
    }
    $desktopResolved = (
        Resolve-Path -LiteralPath $DesktopPath -ErrorAction Stop
    ).Path
    $desktopItem = Get-Item -LiteralPath $desktopResolved -Force -ErrorAction Stop
    if (-not $desktopItem.PSIsContainer) {
        throw "Desktop is not a directory: $desktopResolved"
    }
    if ($desktopItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Desktop cannot be a reparse point: $desktopResolved"
    }

    $powerShellCommand = @(
        Get-Command powershell.exe -CommandType Application -ErrorAction Stop
    )[0]
    $powerShellPath = $powerShellCommand.Source
    $shortcutPath = Join-Path $desktopResolved 'Media Catalog A+ Stable.lnk'
    $shortcutArguments = (
        '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f
        $uiLauncher.Replace('"', '""')
    )
    $iconPath = Join-Path $env:WINDIR 'System32\shell32.dll'
    $iconLocation = "$iconPath,3"
    $localizedDescription = -join @(
        [char]0x5A92,
        [char]0x9AD4,
        [char]0x6574,
        [char]0x7406,
        [char]0x8207,
        [char]0x5206,
        [char]0x6790
    )
    $description = "Media Catalog A+ Stable $localizedDescription"

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $powerShellPath
    $shortcut.Arguments = $shortcutArguments
    $shortcut.WorkingDirectory = $skillResolved
    $shortcut.IconLocation = $iconLocation
    $shortcut.Description = $description
    $shortcut.Save()

    if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) {
        throw "Desktop shortcut was not created: $shortcutPath"
    }
    $saved = $shell.CreateShortcut($shortcutPath)
    $savedTarget = [IO.Path]::GetFullPath($saved.TargetPath)
    $expectedTarget = [IO.Path]::GetFullPath($powerShellPath)
    if (-not $savedTarget.Equals(
        $expectedTarget,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Shortcut TargetPath verification failed: $($saved.TargetPath)"
    }
    if ($saved.Arguments -ne $shortcutArguments) {
        throw "Shortcut Arguments verification failed: $($saved.Arguments)"
    }
    if (-not $saved.WorkingDirectory.Equals(
        $skillResolved,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Shortcut WorkingDirectory verification failed: $($saved.WorkingDirectory)"
    }
    if ($saved.IconLocation -ne $iconLocation) {
        throw "Shortcut IconLocation verification failed: $($saved.IconLocation)"
    }
    if ($saved.Description -ne $description) {
        throw "Shortcut Description verification failed: $($saved.Description)"
    }

    Write-Output "MEDIA_CATALOG_SHORTCUT_READY path=$shortcutPath"
}
catch {
    [Console]::Error.WriteLine(
        "MEDIA_CATALOG_SHORTCUT_ERROR error=$($_.Exception.Message)"
    )
    exit 1
}
