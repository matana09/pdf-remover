# עוצר אך ורק את התהליכים של PDF Redactor.
#
# הריגה כירורגית לפי נתיב מלא. חיוני: MONEY SAVER מריץ python.exe משלו
# (ml/service.py על פורט 8300), ו-BOTI/WEBTOP מריצים node/wscript.
# פילטר לפי שם תהליך בלבד ("python.exe" / "wscript.exe") היה מפיל אותם.
#
# הכלל: מזהים לפי נתיב התיקייה של הפרויקט, שהוא ייחודי.

$ErrorActionPreference = 'SilentlyContinue'
$projectDir = Split-Path -Parent $PSScriptRoot
$killed = @()

Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('python.exe', 'wscript.exe', 'cmd.exe') -and
    $_.CommandLine -and (
        # ה-VBS של הפרויקט הזה בלבד
        $_.CommandLine -like "*$projectDir\run-hidden.vbs*" -or
        # תהליך python שהופעל מתוך ה-venv של הפרויקט הזה בלבד
        $_.CommandLine -like "*$projectDir\venv\Scripts\python.exe*app.py*"
    )
} | ForEach-Object {
    # שכבת ביטחון אחרונה: לעולם לא נוגעים בתהליך ששייך לפרויקט אחר
    if ($_.CommandLine -match 'MONEY SAVER|WEBTOP|\\boti\\|BOTI') {
        Write-Host "  דילוג (שייך לפרויקט אחר): PID $($_.ProcessId)"
        return
    }
    $killed += "PID $($_.ProcessId) [$($_.Name)]"
    Stop-Process -Id $_.ProcessId -Force
}

if ($killed.Count -eq 0) {
    Write-Host "  לא נמצאו תהליכים של PDF Redactor."
} else {
    Write-Host "  נעצרו: $($killed -join ', ')"
}

# אימות שהפורט השתחרר. Get-NetTCPConnection הוא cmdlet מסוג CDXML ואינו מכבד
# את $ErrorActionPreference — נדרש -ErrorAction מפורש כדי שלא ידפיס שגיאה כשאין תוצאה.
Start-Sleep -Milliseconds 800
$still = Get-NetTCPConnection -State Listen -LocalPort 8477 -ErrorAction SilentlyContinue
if ($still) { Write-Host "  אזהרה: פורט 8477 עדיין תפוס (PID $($still.OwningProcess))." }
