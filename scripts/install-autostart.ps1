# רושם Scheduled Task שמפעיל את PDF Redactor אוטומטית בכל כניסה למשתמש
# (כולל אחרי restart או כיבוי והדלקה).
#
# תואם לדפוס שכבר קיים במחשב (MoneySaver-Tzarim, Boti-MzkiraChamuda):
# הפעלה כמשתמש הנוכחי, ללא הרשאות מנהל, חלון מוסתר, והתאוששות מקריסה.
# שם המשימה ייחודי ואינו נוגע במשימות של הפרויקטים האחרים.

$ErrorActionPreference = 'Stop'

$TaskName   = 'PdfRedactor'
$projectDir = Split-Path -Parent $PSScriptRoot
$vbs        = Join-Path $projectDir 'run-hidden.vbs'

if (-not (Test-Path $vbs)) { throw "לא נמצא $vbs" }
if (-not (Test-Path (Join-Path $projectDir 'venv\Scripts\python.exe'))) {
    throw "לא נמצאה סביבת venv. הרץ תחילה את run.bat פעם אחת."
}

Write-Host "[1/3] מסיר רישום קודם של '$TaskName' (אם קיים)..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Write-Host "[2/3] רושם משימה חדשה..."

$action = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument "`"$vbs`"" -WorkingDirectory $projectDir

# הפעלה בכניסת המשתמש הנוכחי בלבד, עם השהיה של 20 שניות
# כדי לא להתחרות על משאבים עם MoneySaver ו-BOTI שעולים באותו זמן.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$env:USERNAME"
$trigger.Delay = 'PT20S'

$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -Hidden `
    -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 99 `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
# ב-PowerShell 5.1 אין פרמטר -MultipleInstancesPolicy; נקבע ישירות.
# IgnoreNew (2): אם השרת כבר רץ, הפעלה נוספת לא תיצור מופע כפול.
$settings.MultipleInstances = 2

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'PDF Redactor - הסרת מידע רגיש מ-PDF. שרת מקומי על 127.0.0.1:8477.' | Out-Null

Write-Host "[3/3] מפעיל..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8

# אימות שהשרת אכן עלה
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8477/health' -UseBasicParsing -TimeoutSec 5
    Write-Host ""
    Write-Host "[OK] PDF Redactor פועל: http://127.0.0.1:8477" -ForegroundColor Green
    Write-Host "     יעלה אוטומטית בכל הפעלה של המחשב."
} catch {
    Write-Host ""
    Write-Host "[שגיאה] השרת לא הגיב. בדוק את logs\server.log" -ForegroundColor Red
    exit 1
}

# אימות שלא פגענו בתוכנות האחרות
Write-Host ""
Write-Host "בדיקת שאר התוכנות:"
foreach ($p in @(@{n='MoneySaver'; port=3000}, @{n='BOTI'; port=3001}, @{n='MoneySaver-ML'; port=8300})) {
    $up = Get-NetTCPConnection -State Listen -LocalPort $p.port -ErrorAction SilentlyContinue
    $mark = if ($up) { 'פועל' } else { 'לא פועל' }
    Write-Host ("  {0,-16} פורט {1,-5} {2}" -f $p.n, $p.port, $mark)
}
