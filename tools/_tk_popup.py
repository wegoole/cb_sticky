"""持久可见测试弹窗（tkinter）：标题仿 ccc 真实窗口，验证点击卡片精准跳转。

Windows 上 ``winfo_id()`` 即顶层窗口 HWND。句柄写 logs/_persist_popup.json。
"""
import json
import time
from pathlib import Path

import tkinter as tk

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "_persist_popup.json"

root = tk.Tk()
root.title("ccc · test session · popup")
root.geometry("560x400")
label = tk.Label(root, text="测试弹窗：点 cb_sticky 卡片应精确跳到这里",
                 font=("Microsoft YaHei", 12))
label.pack(expand=True)
root.update()

OUT.write_text(json.dumps({
    "hwnd": int(root.winfo_id()),
    "pid": None,
    "title": root.title(),
    "spawned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
}, ensure_ascii=False, indent=2), encoding="utf-8")

root.mainloop()
