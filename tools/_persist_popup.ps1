# 持久测试弹窗：标题仿 ccc 真实窗口，供「点击卡片精准跳转」人眼验证。
# 句柄写入 logs/_persist_popup.json；按 Esc 或点关闭按钮退出。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "ccc · test session · popup"
$form.Size = New-Object System.Drawing.Size(640, 480)
$form.StartPosition = "CenterScreen"
$label = New-Object System.Windows.Forms.Label
$label.Text = "测试弹窗：点 cb_sticky 卡片应精确跳到这里（Esc 关闭）"
$label.AutoSize = $true
$form.Controls.Add($label)
$form.Add_KeyDown({ if ($_.KeyCode -eq "Escape") { $form.Close() } })

@{
    hwnd   = [int64]$form.Handle
    pid    = $PID
    title  = $form.Text
    spawned_at = (Get-Date -Format "o")
} | ConvertTo-Json | Set-Content (Join-Path $logDir "_persist_popup.json") -Encoding utf8

$form.ShowDialog() | Out-Null
