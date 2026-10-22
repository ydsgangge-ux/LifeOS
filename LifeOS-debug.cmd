@echo off
chcp 65001 >nul
setlocal
title LifeOS 排障启动

rem ---------------------------------------------------------------------------
rem  LifeOS 保底入口（前台运行，窗口可见）
rem
rem  正常使用请双击 LifeOS.vbs：静默启动，不弹黑窗。
rem  这个文件是给「双击没反应 / 怀疑没启动 / 想看看日志」时用的：
rem  它在前台运行核心进程，日志和错误直接出现在这个窗口里。
rem
rem  注意：关闭这个窗口会停止 LifeOS；不要在这个窗口里选中文字，
rem  控制台的快速编辑模式会暂停输出，看起来像卡死。
rem ---------------------------------------------------------------------------

set "ROOT=%~dp0"

echo ============================================
echo   LifeOS 排障模式（前台运行）
echo   - 关闭此窗口会停止 LifeOS
echo   - 不要在此窗口选中文字，否则输出会暂停
echo   - 完整日志：%USERPROFILE%\.lifeos\launcher-core.log
echo ============================================
echo.

rem 复用 LifeOS.cmd 的 debug 分支，避免两处各写一套 Python 查找逻辑
call "%ROOT%LifeOS.cmd" debug

echo.
echo LifeOS 已退出。上面的内容就是这次启动的完整输出。
pause
endlocal
