"""SessionStart 探针：把收到的 hook 载荷原样追加到 logs/_ss_probe.log。

临时挂在 ~/.claude/settings.json 上，验证 print 模式（claude -p，ccc 的驱动方式）
到底执不执行 hook。验证完记得卸。
"""
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "_ss_probe.log"
raw = ""
try:
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
except Exception:                                     # noqa: BLE001
    raw = ""
with LOG.open("a", encoding="utf-8") as fh:
    fh.write(raw[:2000] + "\n")
