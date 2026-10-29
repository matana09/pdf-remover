# מבטל את ההפעלה האוטומטית של PDF Redactor ועוצר אותו.
# אינו נוגע במשימות המתוזמנות של MoneySaver, BOTI או WebtopBot.

$ErrorActionPreference = 'SilentlyContinue'
$TaskName = 'PdfRedactor'

Write-Host 'מבטל את ההפעלה האוטומטית של PDF Redactor...'
Write-Host ''

schtasks /End /TN $TaskName 2>&1 | Out-Null
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
& (Join-Path $PSScriptRoot 'stop-pdfredactor.ps1')

Write-Host ''
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host '[שגיאה] המשימה עדיין רשומה.' -ForegroundColor Red
} else {
    Write-Host '[OK] בוצע. אפשר עדיין להפעיל ידנית עם run.bat.' -ForegroundColor Green
}

Write-Host ''
Write-Host 'מצב שאר התוכנות (לא אמור להשתנות):'
& (Join-Path $PSScriptRoot 'check-others.ps1')
