"""截图 + 按 DPI 缩放精确定位 stack 窗口并裁剪（需先拿 fg 台前锁）。

用法: python _shot.py <app_pid>
"""
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import win_focus                                  # noqa: E402

app_pid = int(sys.argv[1])
LOGS = ROOT / "logs"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = ctypes.c_uint


def main() -> None:
    img = ImageGrab.grab(all_screens=True)
    img.save(LOGS / "_auth_card_shot.png")
    print("shot:", img.size)

    stack = None
    for w in win_focus.list_windows():
        if w.pid == app_pid and w.title == "pythonw":
            stack = w
    if stack is None:
        print("stack window not found for pid", app_pid)
        return
    x0, y0, x1, y1 = stack.rect
    dpi = user32.GetDpiForWindow(wintypes.HWND(stack.hwnd)) or 96
    scale = dpi / 96.0
    print(f"stack hwnd={stack.hwnd} logical={stack.rect} dpi={dpi} scale={scale}")
    px = (int(x0 * scale), int(y0 * scale), int(x1 * scale), int(y1 * scale))
    print("physical rect:", px)
    pad = 6
    crop = img.crop((max(0, px[0] - pad), max(0, px[1] - pad),
                     min(img.width, px[2] + pad), min(img.height, px[3] + pad)))
    crop.save(LOGS / "_auth_card_crop.png")
    print("crop:", crop.size)


if __name__ == "__main__":
    main()
