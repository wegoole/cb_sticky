# 启动持久测试弹窗（不阻塞、无窗口）
$ErrorActionPreference = "Stop"
$persist = Join-Path $PSScriptRoot "_persist_popup.ps1"
$logFile = Join-Path (Split-Path -Parent $PSScriptRoot) "logs" "_persist_popup.json"
Start-Process pwsh -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","`"$persist`"" -WindowStyle Hidden
Start-Sleep -Seconds 3
if (Test-Path $logFile) { Get-Content $logFile }
