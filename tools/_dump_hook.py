"""实验用：把 cb 发来的 hook 载荷原样落盘（排查错误事件用）。

    cat payload.json | python _dump_hook.py
"""
import sys
import time
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "_hook_payloads.log"


def main() -> int:
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except Exception:                              # noqa: BLE001
        raw = ""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"===== {time.strftime('%F %T')} argv={sys.argv[1:]} =====\n")
            fh.write(raw.strip()[:4000] + "\n")
    except Exception:                              # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
