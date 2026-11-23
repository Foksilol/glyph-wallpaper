param(
    [switch]$NoLaunch,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$Name = "GlyphWallpaper"
$Here = $PSScriptRoot
$Dst  = Join-Path $env:LOCALAPPDATA $Name
$Exe  = Join-Path $Dst "$Name.exe"

$Desktop  = [Environment]::GetFolderPath("Desktop")
$Programs = [Environment]::GetFolderPath("Programs")
$Shortcut = "Glyph Wallpaper.lnk"

function Stop-App {
    Get-Process -Name $Name -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 400
}

function Remove-Shortcuts {
    foreach ($p in @((Join-Path $Desktop $Shortcut),
                     (Join-Path $Programs $Shortcut))) {
        if (Test-Path $p) { Remove-Item $p -Force }
    }
}

if ($Uninstall) {
    Stop-App
    Remove-Shortcuts
    if (Test-Path $Dst) { Remove-Item $Dst -Recurse -Force }
    Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
        -Name $Name -ErrorAction SilentlyContinue
    Write-Host "Glyph Wallpaper removed." -ForegroundColor Green
    return
}

# Build may be in dist\GlyphWallpaper (source checkout) or next to this script
# (unpacked release archive).
$Candidates = @((Join-Path $Here "dist\$Name"), $Here)
$Src = $Candidates | Where-Object {
    Test-Path (Join-Path $_ "$Name.exe")
} | Select-Object -First 1

if (-not $Src) {
    Write-Host ""
    Write-Host "No build found: $Name.exe is missing." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Option 1: download GlyphWallpaper-win64.zip from Releases,"
    Write-Host "          extract it and run Install.bat from that folder."
    Write-Host "Option 2: build from source:"
    Write-Host "          pip install -r requirements.txt -r requirements-dev.txt"
    Write-Host "          powershell -File scripts\build.ps1"
    Write-Host ""
    if (-not $NoLaunch) { Read-Host "Press Enter to close" }
    exit 1
}

Stop-App
if ((Resolve-Path $Src).Path -ne (Resolve-Path $Dst -ErrorAction SilentlyContinue).Path) {
    if (Test-Path $Dst) { Remove-Item $Dst -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Dst | Out-Null
    Copy-Item -Path (Join-Path $Src "*") -Destination $Dst -Recurse -Force
}

$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @($Desktop, $Programs)) {
    $path = Join-Path $dir $Shortcut
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $Exe
    $lnk.WorkingDirectory = $Dst
    $lnk.IconLocation = "$Exe,0"
    $lnk.Description = "Character live wallpaper for Windows"
    $lnk.Save()
}

Write-Host "Installed to $Dst" -ForegroundColor Green
Write-Host "Shortcuts created: Desktop and Start menu." -ForegroundColor DarkGray
if (-not $NoLaunch) { Start-Process -FilePath $Exe }
