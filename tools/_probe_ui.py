"""一次性探针：直接喂事件给 StickyApp，打印卡片可见性与几何形状。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.chdir(str(ROOT))

from PyQt6.QtCore import QTimer          # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import sticky_app as sa                   # noqa: E402

app = QApplication(sys.argv)
st = sa.StickyApp(app)

try:
    st._push_event({"kind": "done", "tool": "cb", "cwd": str(ROOT),
                    "pid": 0, "message": ""})
    st._push_event({"kind": "auth", "tool": "cc", "cwd": str(Path.home()),
                    "pid": 0, "message": "测试授权"})
    print("push ok")
except Exception:
    import traceback
    traceback.print_exc()

print("cards:", len(st.stack.cards))
print("stack visible:", st.stack.isVisible(), "geom:", st.stack.geometry().getRect())
scr = st.stack.screen()
print("screen:", scr.name() if scr else None,
      "geo:", scr.geometry().getRect() if scr else None,
      "avail:", scr.availableGeometry().getRect() if scr else None)


def snap():
    print("--- after 1.5s ---")
    print("cards:", len(st.stack.cards),
          "stack visible:", st.stack.isVisible(),
          "geom:", st.stack.geometry().getRect())
    for c in st.stack.cards:
        print("  card:", c.isVisible(), c.geometry().getRect(),
              "winid:", int(c.winId()) if c.winId() else 0)
    app.quit()


QTimer.singleShot(1500, snap)
app.exec()
