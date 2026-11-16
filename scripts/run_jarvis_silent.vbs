' Windowless Windows 11 Desktop HUD launcher (zero console window flash)
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
projectDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
pythonw = projectDir & "\.venv\Scripts\pythonw.exe"
shell.CurrentDirectory = projectDir

If fso.FileExists(pythonw) Then
    shell.Run """" & pythonw & """ -m jarvis", 1, False
Else
    shell.Run "pythonw -m jarvis", 1, False
End If
