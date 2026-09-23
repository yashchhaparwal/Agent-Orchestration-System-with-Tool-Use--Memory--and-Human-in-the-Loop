# Stop the app processes started by dev_up.ps1 (Docker services keep running; `make down` stops those).
#   powershell -ExecutionPolicy Bypass -File scripts/dev_down.ps1
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pidFile = Join-Path $root ".run\pids.json"
if (Test-Path $pidFile) {
    $pids = Get-Content $pidFile | ConvertFrom-Json
    foreach ($prop in $pids.PSObject.Properties) {
        Stop-Process -Id $prop.Value -Force -ErrorAction SilentlyContinue
        Write-Host "  stopped $($prop.Name) (pid $($prop.Value))"
    }
    Remove-Item $pidFile
}
foreach ($port in 7001, 7002, 7003, 7004, 7005, 8000, 8501) {
    $owners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($o in $owners) { Stop-Process -Id $o -Force -ErrorAction SilentlyContinue }
}
Get-CimInstance Win32_Process | Where-Object { $_.Name -ne 'powershell.exe' -and $_.CommandLine -match 'orchestrator\.worker (worker|beat)' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "app processes stopped; Docker services still up (make down to stop them)"
