# 弹窗场景 HWND 抓取验证（复刻 ccc 进程链）。
# 起一个真 WinForms 弹窗（标题 "ccc · 验证 · test"）→ 它 spawn 一个改名
# claude.exe 的 python（中间进程）→ 中间进程跑 _hook_driver.py 把 Stop 载荷
# 喂给 sticky_hook.py。链路与 ccc 完全同构：
#   sticky_hook(python) → claude.exe → pwsh(本弹窗)
# 预期：dry-run 事件里的 hwnd == 本弹窗的 $form.Handle。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "ccc · 验证 · test"
$form.Size = New-Object System.Drawing.Size(500, 360)
$form.StartPosition = "CenterScreen"
$label = New-Object System.Windows.Forms.Label
$label.Text = "HWND capture test - will close itself"
$label.AutoSize = $true
$form.Controls.Add($label)
$form.Show()
$form.Activate()

# 中间进程：把本机 python.exe 复制成 claude.exe（find_cli_pid 认这个名字）。
# 解释器自动探测：PATH 里的 python → py launcher，避免写死安装路径。
$py = $null
$cmd = Get-Command python -ErrorAction SilentlyContinue
if ($cmd) { $py = $cmd.Source }
else {
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { $py = & py -3 -c "import sys; print(sys.executable)" }
}
if (-not $py -or -not (Test-Path $py)) {
    Write-Error "no python interpreter found on PATH"
    exit 1
}
$mid = Join-Path $env:TEMP "claude.exe"
Copy-Item $py $mid -Force

$driver = Join-Path $PSScriptRoot "_hook_driver.py"
$p = Start-Process -FilePath $mid -ArgumentList "`"$driver`"" -PassThru -Wait
$p.WaitForExit()

Start-Sleep -Milliseconds 300

$meta = @{
    form_handle = [int64]$form.Handle
    form_pid    = $PID
    mid_pid     = $p.Id
    form_title  = $form.Text
} | ConvertTo-Json
$meta | Set-Content (Join-Path $logDir "_popup_hwnd_test.meta.json") -Encoding utf8

$form.Close()
Remove-Item $mid -Force -ErrorAction SilentlyContinue
Write-Output "done"
