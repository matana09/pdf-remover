# מפעיל מחדש את PDF Redactor בלבד.
# MoneySaver, BOTI ו-WebtopBot אינם מושפעים — ראה scripts\stop-pdfredactor.ps1.

$ErrorActionPreference = 'SilentlyContinue'
$projectDir = Split-Path -Parent $PSScriptRoot
$TaskName = 'PdfRedactor'

Write-Host '============================================'
Write-Host '  PDF Redactor - הפעלה מחדש'
Write-Host '============================================'
Write-Host ''

Write-Host '[1/3] עוצר את PDF Redactor בלבד...'
schtasks /End /TN $TaskName 2>&1 | Out-Null
& (Join-Path $PSScriptRoot 'stop-pdfredactor.ps1')

Write-Host ''
Write-Host '[2/3] מפעיל מחדש...'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Start-ScheduledTask -TaskName $TaskName
} else {
    Write-Host '  המשימה המתוזמנת אינה רשומה — מפעיל ישירות.'
    Write-Host '  (להפעלה אוטומטית בעליית המחשב הרץ install-autostart.bat)'
    Start-Process wscript.exe -ArgumentList "`"$projectDir\run-hidden.vbs`"" -WorkingDirectory $projectDir
}

Write-Host '[3/3] ממתין לשרת...'
$ok = $false
foreach ($i in 1..15) {
    try {
        $null = Invoke-WebRequest 'http://127.0.0.1:8477/health' -UseBasicParsing -TimeoutSec 2
        $ok = $true; break
    } catch { Start-Sleep -Seconds 1 }
}

Write-Host ''
if ($ok) {
    Write-Host '[OK] PDF Redactor פועל: http://127.0.0.1:8477' -ForegroundColor Green
} else {
    Write-Host '[שגיאה] השרת לא הגיב. בדוק logs\server.log' -ForegroundColor Red
}

Write-Host ''
Write-Host 'מצב שאר התוכנות (לא אמור להשתנות):'
& (Join-Path $PSScriptRoot 'check-others.ps1')
