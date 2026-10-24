"""复刻卡片点击：用 _Focuser 的同一代码路径 focus 事件里的 hwnd。

fg run 下执行（会抢前台）。结果写 logs/_focus_hwnd_test.json。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import win_focus                                    # noqa: E402

HWND = int(sys.argv[1]) if len(sys.argv) > 1 else 0

# 与 sticky_app._Focuser 相同的构造与调用方式
sys.path.insert(0, str(ROOT / "tools"))
import importlib.util
spec = importlib.util.spec_from_file_location(
    "sticky_app", ROOT / "tools" / "sticky_app.py")

result = {}
result["hwnd_arg"] = HWND
result["is_window"] = win_focus.is_window(HWND)

# 直接走 _Focuser（不 import PyQt6 主程序，只借类逻辑太绕——
# 这里等价复刻：hwnd 优先 → is_window → focus_window）
ok = False
if HWND and win_focus.is_window(HWND):
    ok = win_focus.focus_window(HWND)
result["focus_window"] = ok
result["foreground_after"] = win_focus.foreground_hwnd()
result["foreground_is_target"] = (result["foreground_after"] == HWND)

out = ROOT / "logs" / "_focus_hwnd_test.json"
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
