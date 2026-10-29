' run-hidden.vbs — מריץ את שרת PDF Redactor ללא חלון, וממתין לו (לצורך התאוששות מקריסה).
' מפנה stdout+stderr ל-logs\server.log כדי שאפשר יהיה לדבג נפילות.
'
' חשוב: python מופעל דרך הנתיב המלא של ה-venv, כדי שה-CommandLine של התהליך
' יכיל את נתיב הפרויקט ויהיה ניתן לזיהוי מדויק ע"י scripts\stop-pdfredactor.ps1.
' הריגה לפי שם התהליך בלבד ("python.exe") הייתה מפילה גם את שירות ה-ML של
' MONEY SAVER, שרץ אף הוא כ-python.exe.
' הנתיב חייב להיות עם backslash — cmd.exe מפרש "venv/Scripts/..." כמתג ונכשל.
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
logDir  = baseDir & "\logs"
logFile = logDir & "\server.log"

If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)
' rotation פשוט: אם הלוג עבר 5MB — מוחקים, כדי שלא יגדל ללא הגבלה
If fso.FileExists(logFile) Then
  If fso.GetFile(logFile).Size > 5242880 Then fso.DeleteFile(logFile)
End If

Set ts = fso.OpenTextFile(logFile, 8, True) ' 8 = append, True = create
ts.WriteLine "===== התחלת ריצה " & Now & " ====="
ts.Close

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = baseDir
' 0 = חלון מוסתר, True = להמתין עד שהתהליך מסתיים.
' --no-browser: בהפעלה אוטומטית לא פותחים דפדפן.
' cmd /c מסיר את זוג המרכאות החיצוני, ולכן העטיפה הכפולה נחוצה כדי שגם הנתיב
' עם הרווחים ("PDF REMOVER") וגם ההפניה ללוג יישארו שלמים.
pyExe = baseDir & "\venv\Scripts\python.exe"
cmdLine = """" & pyExe & """ app.py --no-browser >> ""logs\server.log"" 2>&1"
exitCode = sh.Run("cmd /c """ & cmdLine & """", 0, True)
WScript.Quit exitCode
