@echo off
chcp 65001 >nul
title Glyph Wallpaper - Install
set "LOG=%~dp0install.log"
echo Installing Glyph Wallpaper, please wait...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" > "%LOG%" 2>&1
type "%LOG%"
echo.
if errorlevel 1 (
  echo INSTALLATION FAILED - see the messages above.
) else (
  echo DONE. The app window should be open now.
)
echo.
pause
