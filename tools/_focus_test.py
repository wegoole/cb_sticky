"""一次性真机验证：点卡片跳转链路（focus WT 标签窗口 + 还原前台）。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import win_focus                                  # noqa: E402

LOGS = ROOT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
LOG = open(LOGS / "_focus_test.txt", "w", encoding="utf-8")


def log(*args):
    print(*args, file=LOG, flush=True)


def title_of(hwnd):
    for w in win_focus.list_windows():
        if w.hwnd == hwnd:
            return w.title[:50]
    return "(unknown)"


before = win_focus.foreground_hwnd()
log("before:", before, title_of(before))

cands = win_focus.cb_candidate_windows()
target = cands[0] if cands else None
if target is None:
    log("no cb candidate window found")
    LOG.close()
    sys.exit(1)
log("target:", target.hwnd, target.title[:50])

ok = win_focus.focus_window(target.hwnd)
time.sleep(0.8)
after = win_focus.foreground_hwnd()
log("focus_window ->", ok)
log("after :", after, title_of(after))
log("RESULT:", "HIT" if after == target.hwnd else "MISS")

if before and before != after:
    win_focus.focus_window(before)
    time.sleep(0.3)
    log("restored ->", win_focus.foreground_hwnd() == before)
LOG.close()
