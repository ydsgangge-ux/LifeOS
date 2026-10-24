@echo off
chcp 65001 >nul
setlocal
title LifeOS

rem ---------------------------------------------------------------------------
rem  LifeOS launcher - command line / troubleshooting entry point
rem
rem  Normal use: double-click LifeOS.vbs. It starts python.exe with a hidden
rem  window, so no black console appears.
rem  This .cmd is for troubleshooting: double-clicking it flashes a console,
rem  and "LifeOS.cmd debug" runs in the foreground with the log visible.
rem
rem  Python lookup order: bundled portable runtime\python > repo .venv > PATH
rem
rem  Why not pythonw.exe: on machines running 360 or Tencent PC Manager it is
rem  blocked per-executable, which kills loopback connections. asyncio then
rem  hangs in socket.socketpair() while creating the event loop and the core
rem  never starts. python.exe from the same runtime folder works fine.
rem  See the notes at the top of LifeOS.vbs.
rem
rem  IMPORTANT: keep this file pure ASCII - see LifeOS-setup.cmd for why.
rem ---------------------------------------------------------------------------
set "ROOT=%~dp0"

set "PY=%ROOT%runtime\python\python.exe"
if not exist "%PY%" (
    set "PY=%ROOT%.venv\Scripts\python.exe"
)
if not exist "%PY%" (
    set "PY=python"
)

rem the lifeos package lives under services\core; switch there so the caller's
rem current directory does not matter
cd /d "%ROOT%services\core"

if /i "%~1"=="debug" goto foreground

rem silent start - hand off to the VBS launcher so no console window appears
wscript //nologo "%ROOT%LifeOS.vbs"
endlocal
exit /b 0

:foreground
"%PY%" -m lifeos.launcher
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo LifeOS failed to start - exit code %RC%.
    echo Full log: %USERPROFILE%\.lifeos\launcher-core.log
    pause
)
endlocal
