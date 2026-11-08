Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
currentPath = FSO.GetParentFolderName(WScript.ScriptFullName)
targetScript = FSO.BuildPath(currentPath, "role_analyzer_lab.pyw")

On Error Resume Next
WshShell.Run "pythonw """ & targetScript & """", 0, False

If Err.Number <> 0 Then
    Err.Clear
    WshShell.Run "python """ & targetScript & """", 0, False
End If
