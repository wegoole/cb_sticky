"""一次性诊断：cb node 进程 → 祖先链 → 窗口枚举 → resolve 结果全列出来。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psutil                                     # noqa: E402
import win_focus                                  # noqa: E402

print("=== cb/claude node processes ===")
targets = []
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        name = (p.info["name"] or "").lower()
        cmd = " ".join(p.info["cmdline"] or []).lower()
        if name in {"node.exe", "node", "bun.exe", "deno.exe"} and (
                "codebuddy" in cmd or "claude" in cmd):
            targets.append(p)
            print(f"  node pid={p.pid}  cmd={cmd[:110]}")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

print("\n=== ancestor chains ===")
for p in targets:
    chain = []
    cur = p
    for _ in range(10):
        try:
            chain.append(f"{cur.pid}:{cur.name()}")
            cur = cur.parent()
            if cur is None:
                chain.append("(none)")
                break
        except Exception as exc:                   # noqa: BLE001
            chain.append(f"(err {exc!r})")
            break
    print("  " + " -> ".join(chain))

print("\n=== visible top-level windows ===")
for w in win_focus.list_windows():
    print(f"  pid={w.pid:>7} area={w.area:>9} cls={w.cls[:24]:<24} title={w.title[:64]}")

print("\n=== resolve_host_window(node_pid) ===")
for p in targets:
    info = win_focus.resolve_host_window(p.pid, str(Path.home()),
                                         exclude_pids=(os.getpid(),))
    if info:
        print(f"  node {p.pid} -> hwnd={info.hwnd} pid={info.pid} "
              f"title={info.title[:50]!r}")
    else:
        print(f"  node {p.pid} -> None (找不到)")

print("\n=== ancestor_pids of each node ===")
for p in targets:
    print(f"  {p.pid}: {win_focus.ancestor_pids(p.pid)}")
