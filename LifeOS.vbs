' LifeOS silent launcher - double-click to run, with no black terminal window
'
' Why not pythonw.exe:
'   pythonw.exe is the GUI-subsystem interpreter. On machines running 360 and
'   Tencent PC Manager together, it gets blocked per-executable and loopback
'   connections time out 100%. In the same runtime\python folder python.exe
'   connects and accepts on 127.0.0.1 just fine.
'   asyncio on Windows calls socket.socketpair() when creating the event loop,
'   and CPython's fallback implementation is a loopback connect + accept. So
'   the core hangs forever during startup and never prints the handshake line,
'   which looks like "double-click does nothing".
'
'   Hence python.exe with window style 0 - hidden. This avoids the blocked
'   pythonw.exe and still shows no console. For command-line troubleshooting
'   use LifeOS.cmd debug.
'
' Keep this file pure ASCII: WSH reads .vbs as ANSI, so non-ASCII text breaks
' on systems with a different code page.

Option Explicit

Dim fso, sh, root, py, coreDir, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)

' Python lookup: bundled portable runtime > repo .venv > PATH
py = root & "\runtime\python\python.exe"
If Not fso.FileExists(py) Then py = root & "\.venv\Scripts\python.exe"
If Not fso.FileExists(py) Then py = "python"

coreDir = root & "\services\core"
sh.CurrentDirectory = coreDir

cmd = """" & py & """ -m lifeos.launcher"

' 0 = hidden window, False = do not wait; the launcher waits for the core
sh.Run cmd, 0, False
