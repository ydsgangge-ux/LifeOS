' LifeOS 静默启动器（P6 A1：双击即用，且不出现黑色终端窗口）
'
' 为什么不用 pythonw.exe：
'   pythonw.exe 是没有控制台的 GUI 子系统解释器，但它在本机（360 + 腾讯电脑管家
'   同时开启时）**被按「可执行文件」粒度拦掉了回环连接** —— 实测同一个
'   runtime\python 目录下，python.exe 的 127.0.0.1 connect/accept 正常，
'   pythonw.exe 则 100% 超时。而 asyncio 在 Windows 上建事件循环时要用
'   socket.socketpair()，CPython 在 Windows 的回退实现正是「回环 connect + accept」，
'   于是核心会永久卡在启动阶段、连握手行都吐不出来（表现为双击后毫无反应）。
'
' 所以这里改用 python.exe + 窗口样式 0（隐藏）拉起，既避开被拦的 pythonw.exe，
' 又不需要任何终端窗口。命令行排障仍用 LifeOS.cmd debug。

Option Explicit

Dim fso, sh, root, py, coreDir, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)

' Python 三档：随包便携版 > 仓库 .venv > PATH
py = root & "\runtime\python\python.exe"
If Not fso.FileExists(py) Then py = root & "\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then py = "python"

coreDir = root & "\services\core"
sh.CurrentDirectory = coreDir

cmd = """" & py & """ -m lifeos.launcher"

' 0 = 隐藏窗口，False = 不等待（启动器自己会等核心退出）
sh.Run cmd, 0, False
