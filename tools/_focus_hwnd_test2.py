"""focus hwnd 验证 v2：带基线、延迟与重试的 foreground 读取。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import win_focus                                    # noqa: E402

HWND = int(sys.argv[1]) if len(sys.argv) > 1 else 0
res = {"hwnd": HWND, "is_window": win_focus.is_window(HWND)}
res["fg_before"] = win_focus.foreground_hwnd()
res["focus_window"] = win_focus.focus_window(HWND)
for i in range(10):
    time.sleep(0.2)
    fg = win_focus.foreground_hwnd()
    res[f"fg_after_{(i + 1) * 200}ms"] = fg
    if fg == HWND:
        break
res["final_is_target"] = (win_focus.foreground_hwnd() == HWND)
out = ROOT / "logs" / "_focus_hwnd_test.json"
out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False))
