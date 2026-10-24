@echo off
chcp 65001 >nul
setlocal
title LifeOS troubleshooting

rem ---------------------------------------------------------------------------
rem  LifeOS fallback launcher - runs in the foreground, console visible
rem
rem  Normal use: double-click LifeOS.vbs - silent start, no console window.
rem  Use this file when nothing seems to happen on double-click, or when you
rem  want to watch the log: the core process runs here and its output shows
rem  up directly in this window.
rem
rem  Note: closing this window stops LifeOS. Do not select text in this
rem  window - the console quick-edit mode pauses output and looks like a hang.
rem
rem  IMPORTANT: keep this file pure ASCII - see LifeOS-setup.cmd for why.
rem ---------------------------------------------------------------------------

set "ROOT=%~dp0"

echo ============================================
echo   LifeOS troubleshooting mode
echo   - Closing this window stops LifeOS
echo   - Do not select text here, output pauses
echo   - Full log: %USERPROFILE%\.lifeos\launcher-core.log
echo ============================================
echo.

rem reuse the debug branch of LifeOS.cmd instead of duplicating the
rem Python lookup logic in two places
call "%ROOT%LifeOS.cmd" debug

echo.
echo LifeOS exited. Everything above is the output of this run.
pause
endlocal
