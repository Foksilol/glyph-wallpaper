@echo off
chcp 65001 >nul
echo Removing Glyph Wallpaper...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
pause
