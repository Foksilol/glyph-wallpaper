@echo off
chcp 65001 >nul
title Glyph Wallpaper - Uninstall
echo Removing Glyph Wallpaper, please wait...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
echo.
echo DONE. Your wallpaper has been restored.
echo.
pause
