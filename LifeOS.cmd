@echo off
chcp 65001 >nul
setlocal
title LifeOS

rem ---------------------------------------------------------------------------
rem  LifeOS 启动入口（命令行/排障用）
rem
rem  正常使用请直接双击 LifeOS.vbs —— 它用 python.exe + 隐藏窗口拉起，不弹黑窗。
rem  本 .cmd 保留作排障：双击会闪一下控制台，LifeOS.cmd debug 则前台运行、日志可见。
rem
rem  Python 优先顺序：随包便携版 runtime\python > 仓库 .venv > 系统 PATH 里的 python
rem
rem  为什么不用 pythonw.exe：它在本机（360 + 腾讯电脑管家）被按「可执行文件」
rem  粒度拦掉回环连接，asyncio 建事件循环时卡在 socket.socketpair()，核心永不启动。
rem  同一个 runtime 目录下 python.exe 正常，见 LifeOS.vbs 顶部说明。
rem ---------------------------------------------------------------------------
set "ROOT=%~dp0"

set "PY=%ROOT%runtime\python\python.exe"
if not exist "%PY%" (
    set "PY=%ROOT%.venv\Scripts\python.exe"
)
if not exist "%PY%" (
    set "PY=python"
)

rem lifeos 包在 services\core 下；先切过去，免得依赖调用者当前目录
cd /d "%ROOT%services\core"

if /i "%~1"=="debug" goto foreground

rem 静默启动：交给 VBS 用 python.exe + 隐藏窗口拉起（不弹黑窗）
wscript //nologo "%ROOT%LifeOS.vbs"
endlocal
exit /b 0

:foreground
"%PY%" -m lifeos.launcher
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo LifeOS 没能启动（错误码 %RC%）。
    echo 详细日志：%USERPROFILE%\.lifeos\launcher-core.log
    pause
)
endlocal
