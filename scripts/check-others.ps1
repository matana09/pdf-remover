# מציג את מצב התוכנות האחרות במחשב — לאימות שפעולה על PDF Redactor לא פגעה בהן.

$others = @(
    @{ Name = 'MoneySaver';    Port = 3000 },
    @{ Name = 'BOTI';          Port = 3001 },
    @{ Name = 'MoneySaver-ML'; Port = 8300 },
    @{ Name = 'PDF Redactor';  Port = 8477 }
)

foreach ($o in $others) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $o.Port -ErrorAction SilentlyContinue
    if ($conn) {
        $procId = ($conn | Select-Object -First 1).OwningProcess
        Write-Host ("  {0,-16} פורט {1,-6} פועל  (PID {2})" -f $o.Name, $o.Port, $procId) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-16} פורט {1,-6} לא פועל" -f $o.Name, $o.Port) -ForegroundColor DarkGray
    }
}
