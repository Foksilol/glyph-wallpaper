# Build GlyphWallpaper.exe (PyInstaller, onedir) and the release archive.
# Run from repo root:  powershell -File scripts/build.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name GlyphWallpaper `
    --icon "$root\app.ico" `
    --add-data "$root\app.ico;." `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root\build" `
    "$root\app.py"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller не отработал (проверьте, что установлен: pip install -r requirements-dev.txt)"
}

$out = Join-Path $root "dist\GlyphWallpaper"
if (-not (Test-Path $out)) { throw "Build not found: $out" }

# ship installer + docs inside the release folder so Install.bat works there
Copy-Item "$root\install.ps1" $out -Force
Copy-Item "$root\Install.bat" $out -Force
Copy-Item "$root\Uninstall.bat" $out -Force
Copy-Item "$root\LICENSE" $out -Force
Copy-Item "$root\README.md" $out -Force

$zip = Join-Path $root "dist\GlyphWallpaper-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path "$out\*" -DestinationPath $zip -CompressionLevel Optimal

Write-Host "Build: $out"
Write-Host "Zip:   $zip"
