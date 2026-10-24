@echo off
chcp 65001 >nul
setlocal
title LifeOS setup

rem ---------------------------------------------------------------------------
rem  One-click dependency setup for a fresh clone.
rem
rem  Usage:
rem    double-click LifeOS-setup.cmd        runtime deps only - what normal
rem                                         users need
rem    LifeOS-setup.cmd dev                 also install test + lint tooling
rem
rem  After it finishes, double-click LifeOS.vbs to run LifeOS.
rem  Requires Python 3.12+ and Node 20+ - both are checked before anything runs.
rem
rem  IMPORTANT: keep this file pure ASCII. cmd.exe parses bytes, so non-ASCII
rem  text here breaks on machines with a different default code page, and the
rem  window flashes closed before anyone can read the error.
rem ---------------------------------------------------------------------------

set "ROOT=%~dp0"
set "MODE=%~1"
set "PY="

echo ============================================
echo   LifeOS dependency setup
echo   When done, double-click LifeOS.vbs
echo ============================================
echo.

rem ---------- 1. find Python 3.12+ ----------
call :find_python
if not defined PY goto :no_python

"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python

"%PY%" -c "import sys;sys.stdout.write(sys.version.split()[0])" >"%TEMP%\lifeos_pyver.txt" 2>nul
set /p PYVER=<"%TEMP%\lifeos_pyver.txt"
del "%TEMP%\lifeos_pyver.txt" >nul 2>nul
echo [1/4] Python %PYVER% found
echo         %PY%
echo.

rem ---------- 2. virtual environment ----------
if exist "%ROOT%.venv\Scripts\python.exe" goto :venv_ready

echo [2/4] Creating virtual environment .venv - one time only
"%PY%" -m venv "%ROOT%.venv"
if errorlevel 1 goto :venv_fail
goto :pip_install

:venv_ready
echo [2/4] Existing .venv found, reusing it

:pip_install
set "PY=%ROOT%.venv\Scripts\python.exe"
echo.

rem ---------- 3. backend dependencies ----------
echo [3/4] Installing backend dependencies
echo         First run downloads tens of MB, please wait
"%PY%" -m pip install --upgrade pip >nul 2>nul
if /i "%MODE%"=="dev" goto :pip_dev
"%PY%" -m pip install -e "%ROOT%services\core"
if errorlevel 1 goto :pip_fail
goto :npm_step

:pip_dev
echo         Mode: dev - includes pytest / ruff / mypy
"%PY%" -m pip install -e "%ROOT%services\core[dev]"
if errorlevel 1 goto :pip_fail
goto :npm_step

:npm_step
echo.
echo [4/4] Installing frontend dependencies and building the web UI
where npm >nul 2>nul
if errorlevel 1 goto :no_node

for /f "tokens=1 delims=." %%m in ('node -v 2^>nul') do set "NODE_MAJOR=%%m"
set "NODE_MAJOR=%NODE_MAJOR:v=%"
if "%NODE_MAJOR%"=="" goto :npm_install
if %NODE_MAJOR% LSS 20 goto :node_too_old
if %NODE_MAJOR% LSS 22 echo         Note: Node %NODE_MAJOR% is old - 22+ recommended

:npm_install
cd /d "%ROOT%apps\web"
echo         Installing npm packages...
call npm install
if errorlevel 1 goto :npm_mirror
goto :npm_build

:npm_mirror
echo.
echo         Official registry unreachable - retrying via mirror
set "npm_config_registry=https://registry.npmmirror.com"
call npm install --no-package-lock
if errorlevel 1 goto :npm_fail

:npm_build
echo.
echo         Building the web UI...
call npm run build
if errorlevel 1 goto :build_fail

echo.
echo ============================================
echo   Done
echo ============================================
echo.
echo   Next step: double-click LifeOS.vbs
echo   To see logs or diagnose: double-click LifeOS-debug.cmd
echo   The data folder is created on first launch, no wizard.
echo.
echo   For development - tests and linting: LifeOS-setup.cmd dev
echo.
pause
endlocal
exit /b 0

rem ===========================================================================
rem  Subroutines and error messages
rem ===========================================================================

:find_python
rem prefer an existing venv, then the py launcher, then python on PATH
if exist "%ROOT%.venv\Scripts\python.exe" (
    set "PY=%ROOT%.venv\Scripts\python.exe"
    exit /b 0
)
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if defined PY exit /b 0
for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
exit /b 0

:no_python
echo [x] Python not found.
echo.
echo     Install Python 3.12 or newer: https://www.python.org/downloads/
echo     Tick "Add python.exe to PATH" during installation.
echo.
echo     Just want to use it without any environment? Download
echo     LifeOS-win-x64.zip from the Releases page, unzip, double-click
echo     LifeOS.vbs. No Python needed.
echo.
pause
endlocal
exit /b 1

:old_python
echo [x] Python is too old - 3.12 or newer is required.
echo         %PY%
echo.
echo     Install a newer version and run this script again.
pause
endlocal
exit /b 1

:venv_fail
echo [x] Could not create the virtual environment.
echo     Usually the folder is not writable, or antivirus blocked
echo     python -m venv. Moving the repo to a plain user folder such
echo     as D:\LifeOS also helps.
pause
endlocal
exit /b 1

:pip_fail
echo [x] Backend dependency install failed - see the pip output above.
echo     If the network is the problem, retry with a mirror:
echo         .venv\Scripts\python.exe -m pip install -e "services/core" -i https://pypi.tuna.tsinghua.edu.cn/simple
pause
endlocal
exit /b 1

:no_node
echo [x] Node.js not found - the npm command is missing.
echo.
echo     Install Node.js 20 or newer, 22 LTS recommended: https://nodejs.org/
echo     Open a new window afterwards so PATH takes effect.
echo.
echo     Just want the finished product? Download LifeOS-win-x64.zip
echo     from the Releases page - no Node needed.
pause
endlocal
exit /b 1

:node_too_old
echo [x] Node is too old - v%NODE_MAJOR% found, 20 or newer required.
echo     Upgrade at https://nodejs.org/ and run this script again.
pause
endlocal
exit /b 1

:npm_fail
echo [x] npm install failed - both the official registry and the mirror
echo     were tried. This is usually network or proxy related. Retry by hand:
echo         cd apps\web
echo         npm install --registry=https://registry.npmmirror.com
pause
endlocal
exit /b 1

:build_fail
echo [x] Frontend build failed - see the tsc / vite output above.
echo     Paste that output into a GitHub issue and we can look at it.
pause
endlocal
exit /b 1
