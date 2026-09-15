param(
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][ValidateSet('reelfactory', 'ollama')][string]$Service
)

$ErrorActionPreference = 'Stop'
$rfListeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
$rfProcesses = @()
foreach ($rfPid in $rfListeners) {
    $rfProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $rfPid"
    $rfMatches = if ($Service -eq 'reelfactory') {
        $rfProcess.Name -match '^python(?:w|\d+(?:\.\d+)?)?\.exe$' -and
        $rfProcess.CommandLine -match '(?:^|\s)-m\s+reelfactory\s+serve(?:\s|$)'
    } else {
        $rfProcess.Name -eq 'ollama.exe' -and $rfProcess.CommandLine -match '\bserve(?:\s|$)'
    }
    if (-not $rfMatches) {
        Write-Host "[X] Port $Port belongs to another program (PID $rfPid). It was left running."
        Write-Host 'Close that program yourself or choose another port with --port.'
        exit 1
    }
    $rfProcesses += $rfProcess
}
# Validate every listener before stopping any of them.
foreach ($rfProcess in $rfProcesses) {
    Write-Host "[..] Restarting $Service (PID $($rfProcess.ProcessId))."
    Stop-Process -Id $rfProcess.ProcessId -ErrorAction Stop
}
if ($rfProcesses.Count) { Start-Sleep -Milliseconds 500 }
exit 0
