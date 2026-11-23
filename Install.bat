@echo off
chcp 65001 >nul
echo Installing Glyph Wallpaper...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 pause
