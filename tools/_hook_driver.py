"""弹窗 HWND 抓取验证的中间驱动：把一份 Stop 载荷喂给 sticky_hook.py。

被 _popup_test.ps1 以「改名 claude.exe 的 python」身份拉起来，模拟 ccc 的
进程链：sticky_hook(python) → claude.exe(本进程) → pwsh(WinForms 弹窗)。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "_popup_hwnd_test.json"

payload = json.dumps({
    "hook_event_name": "Stop",
    "cwd": str(Path.home()).replace("\\", "/"),
    "session_id": "popup-hwnd-test-1",
    "last_assistant_message": "Hi",
}, ensure_ascii=False)

proc = subprocess.run(
    [sys.executable, str(ROOT / "tools" / "sticky_hook.py"),
     "--tool", "cc", "--dry-run"],
    input=payload, capture_output=True, text=True, cwd=str(ROOT),
    encoding="utf-8",
)
OUT.write_text(proc.stdout or "", encoding="utf-8")
