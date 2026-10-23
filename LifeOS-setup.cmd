@echo off
chcp 65001 >nul
setlocal
title LifeOS 一键安装依赖

rem ---------------------------------------------------------------------------
rem  从 GitHub 拉下来之后的第一步：把后端与前端依赖装齐，并构建前端。
rem
rem  用法：
rem    双击 LifeOS-setup.cmd           只装运行需要的依赖（普通人用这个）
rem    LifeOS-setup.cmd dev             连开发用的测试/lint 工具一起装
rem
rem  装完双击根目录的 LifeOS.vbs 就能用（见 README 第六节）。
rem  需要 Python 3.12+ 与 Node 20+（推荐 22+），脚本会先检查再动手。
rem ---------------------------------------------------------------------------

set "ROOT=%~dp0"
set "MODE=%~1"
set "PY="

echo ============================================
echo   LifeOS 一键安装依赖
echo   装好之后双击 LifeOS.vbs 就能用
echo ============================================
echo.

rem ---------- 1. 找 Python 3.12+ ----------
call :find_python
if not defined PY goto :no_python

"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python

"%PY%" -c "import sys;sys.stdout.write(sys.version.split()[0])" >"%TEMP%\lifeos_pyver.txt" 2>nul
set /p PYVER=<"%TEMP%\lifeos_pyver.txt"
del "%TEMP%\lifeos_pyver.txt" >nul 2>nul
echo [1/4] 找到 Python %PYVER%
echo         %PY%
echo.

rem ---------- 2. 虚拟环境 ----------
if exist "%ROOT%.venv\Scripts\python.exe" goto :venv_ready
echo [2/4] 创建虚拟环境 .venv（只需一次）
"%PY%" -m venv "%ROOT%.venv"
if errorlevel 1 goto :venv_fail
goto :pip_install

:venv_ready
echo [2/4] 已有虚拟环境 .venv，直接复用

:pip_install
set "PY=%ROOT%.venv\Scripts\python.exe"
echo.

rem ---------- 3. 后端依赖 ----------
echo [3/4] 安装后端依赖（第一次要下载几十 MB，请耐心等）
"%PY%" -m pip install --upgrade pip >nul 2>nul
if /i "%MODE%"=="dev" goto :pip_dev
"%PY%" -m pip install -e "%ROOT%services\core"
if errorlevel 1 goto :pip_fail
goto :npm_step

:pip_dev
echo         模式：dev（含 pytest / ruff / mypy 等）
"%PY%" -m pip install -e "%ROOT%services\core[dev]"
if errorlevel 1 goto :pip_fail
goto :npm_step

:npm_step
echo.
echo [4/4] 安装前端依赖并构建页面
where npm >nul 2>nul
if errorlevel 1 goto :no_node

for /f "tokens=1 delims=." %%m in ('node -v 2^>nul') do set "NODE_MAJOR=%%m"
set "NODE_MAJOR=%NODE_MAJOR:v=%"
if "%NODE_MAJOR%"=="" goto :npm_install
if %NODE_MAJOR% LSS 20 goto :node_too_old
if %NODE_MAJOR% LSS 22 echo        提示：Node 版本偏低（建议 22+），构建可能失败

:npm_install
cd /d "%ROOT%apps\web"
echo         正在安装 npm 依赖...
call npm install
if errorlevel 1 goto :npm_mirror
goto :npm_build

:npm_mirror
echo.
echo         官方源不通（常见于国内网络），换成镜像再试一次...
set "npm_config_registry=https://registry.npmmirror.com"
call npm install --no-package-lock
if errorlevel 1 goto :npm_fail

:npm_build
echo.
echo         正在构建前端...
call npm run build
if errorlevel 1 goto :build_fail

echo.
echo ============================================
echo   装好了
echo ============================================
echo.
echo   下一步：双击 %ROOT%LifeOS.vbs
echo   想看日志或启动失败：双击 LifeOS-debug.cmd
echo   第一次打开会自动创建数据目录，没有安装向导。
echo.
if /i not "%MODE%"=="dev" echo   想做开发（跑测试 / 代码检查）：LifeOS-setup.cmd dev
echo.
pause
endlocal
exit /b 0

rem ===========================================================================
rem  子过程与错误提示
rem ===========================================================================

:find_python
rem 优先用仓库里已有的 .venv，其次 py 启动器，最后 PATH 里的 python
if exist "%ROOT%.venv\Scripts\python.exe" (
    set "PY=%ROOT%.venv\Scripts\python.exe"
    exit /b 0
)
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if defined PY exit /b 0
for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
exit /b 0

:no_python
echo [x] 没找到 Python。
echo.
echo     请先装 Python 3.12 或更新的版本：https://www.python.org/downloads/
echo     安装时记得勾选 "Add python.exe to PATH"。
echo.
echo     如果你只是想在电脑上用、不想装环境：下载 Release 里的
echo     LifeOS-win-x64.zip，解压后双击 LifeOS.vbs 即可，不需要 Python。
echo.
pause
endlocal
exit /b 1

:old_python
echo [x] 当前 Python 版本太旧，需要 3.12 或更新。
echo         %PY%
echo.
echo     装新版后重跑本脚本即可。
pause
endlocal
exit /b 1

:venv_fail
echo [x] 创建虚拟环境失败。
echo     常见原因是目录不可写，或杀毒软件拦截了 python -m venv。
echo     也可以把仓库放到一个普通用户目录下再试（例如 D:\LifeOS）。
pause
endlocal
exit /b 1

:pip_fail
echo [x] 后端依赖安装失败。上面是 pip 的原始报错。
echo     网络不好时可以手动加镜像重试：
echo         .venv\Scripts\python.exe -m pip install -e "services/core" -i https://pypi.tuna.tsinghua.edu.cn/simple
pause
endlocal
exit /b 1

:no_node
echo [x] 没找到 Node.js（npm 命令）。
echo.
echo     请安装 Node.js 20 以上（推荐 22 LTS）：https://nodejs.org/
echo     装完重开一个窗口再跑本脚本，PATH 才会生效。
echo.
echo     只想要成品、不想装 Node：下载 Release 里的 LifeOS-win-x64.zip。
pause
endlocal
exit /b 1

:node_too_old
echo [x] Node 版本太旧（当前 v%NODE_MAJOR%），需要 20 以上。
echo     请升级后重试：https://nodejs.org/
pause
endlocal
exit /b 1

:npm_fail
echo [x] npm 依赖安装失败（官方源和镜像都试过了）。
echo     多半是网络或代理问题。可手动重试：
echo         cd apps\web
echo         npm install --registry=https://registry.npmmirror.com
pause
endlocal
exit /b 1

:build_fail
echo [x] 前端构建失败。上面是 tsc / vite 的原始报错。
echo     把这段报错贴到 GitHub Issues 即可。
pause
endlocal
exit /b 1
