"""cb 狗皮膏药的 hook 入口：把两类事件写进 logs/spool/，快进快出。

装好之后由 CodeBuddy / Claude Code 自动调用（Stop / Notification），不需要手动跑。
手动喂一份 payload 调试::

    echo '{"hook_event_name":"Stop","cwd":"E:/x","session_id":"s"}' |
        python tools/sticky_hook.py --tool cb --dry-run

只认三类事件：

* ``Stop`` —— 主循环答完一轮 = **主任务完成**
  （子任务完成走 ``SubagentStop``，这里故意不接，正是要的区别）；
  但若 ``last_assistant_message`` 以 4xx/5xx 开头（API 报错收场，
  实证 429/400 都走这条路），升级成 **err** 卡片；
* ``Notification`` 且 ``notification_type`` 含 ``permission`` —— 弹了授权框。

其余事件（SessionStart / UserPromptSubmit / SubagentStop …）直接 exit 0。

设计约束：

* hook 是**同步**执行的，CLI 会等它跑完 → 总耗时压在几十毫秒；
* 本程序没开也不丢事件：只落盘，app 启动后补弹（受每屏卡片数上限约束）；
* 任何失败安静退出 0，绝不拖住 CLI；
* 不往 stdout 打印任何东西（hook 输出可能被当成反馈喂回模型）；
* 落盘时顺手抓一个 ``hwnd``（hook 瞬间祖先链还活着）：ccb / ccc 弹窗
  场景靠它做到「点击精准跳回那个弹窗」；WT 场景主动记 0（见 capture_hwnd）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 认识的 HTTP 错误码（出现在 last_assistant_message 开头才算错误回合）
_ERROR_CODES = {"400", "401", "403", "404", "408", "409", "413",
                "422", "429", "500", "502", "503", "504"}
_ERR_PREFIX = re.compile(r"^\s*(4\d\d|5\d\d)\b")


def error_of(payload: dict) -> tuple[str, str]:
    """Stop 载荷 → ``(错误码, 首行错误信息)``；不是错误回合返回 ``("", "")``。

    实证（坏模型名实验）：回合失败（API 报错）时 Stop hook
    **照常触发**，错误信息放在 ``last_assistant_message``，形如::

        400 model [nonexistent-model-xyz-999] service info not found (trace/session)

    429（额度耗尽）等同理——错误码在开头。正常完成的回合首条是正常
    助理事迹（如 ``Hi``），不会以 4xx/5xx 开头。
    """
    msg = str(payload.get("last_assistant_message") or "").strip()
    match = _ERR_PREFIX.match(msg)
    if not match:
        return "", ""
    code = match.group(1)
    if code not in _ERROR_CODES:
        return "", ""
    first_line = msg.splitlines()[0].strip() if msg else ""
    return code, first_line[:200]


def find_cli_pid(start_pid: int | None = None) -> int:
    """往上找 CLI 的主进程（hook 自己马上就退了，它会活到终端 / 弹窗关闭）。

    认两种 CLI：node 系的 cb（``codebuddy``）和原生 exe 的
    Claude Code（``claude.exe``，cc / ccc 启动器都是它）。
    找不到 / 没装 psutil 都返回 0 —— 卡片仍会弹，只是不能点回去。
    """
    pid = int(start_pid or os.getpid())
    try:
        import psutil                                     # noqa: PLC0415
    except Exception:                                     # noqa: BLE001
        return 0
    try:
        proc = psutil.Process(pid)
        for _ in range(12):
            parent = proc.parent()
            if parent is None:
                return 0
            name = (parent.name() or "").lower()
            cmd = " ".join(parent.cmdline() or []).lower()
            if name in {"node.exe", "node", "bun.exe", "deno.exe"} and (
                    "codebuddy" in cmd or "claude" in cmd):
                return int(parent.pid)
            if name in {"claude.exe", "claude"} and "claude" in cmd:
                return int(parent.pid)
            proc = parent
    except Exception:                                     # noqa: BLE001
        return 0
    return 0


def capture_hwnd(cli_pid: int, cwd: str) -> int:
    """hook 执行瞬间（祖先链还活着）解析承载窗口的 HWND，写进事件供点击跳转。

    * **ccb / ccb 弹窗**：node / claude.exe 的父进程 pwsh 持有真窗口
      （WinForms，标题 ``ccb · 模式 · 模型``），一击命中；
    * **WT 交互**（cb / cc）：链上第一个有窗口的进程是 WindowsTerminal，
      窗口类是 ``CASCADIA_HOSTING_WINDOW_``——那是「整个 WT」而不是
      「这个会话的标签页」，focus 它可能跳错标签页，**主动抑制返回 0**，
      让 app 端走会话标题匹配 / 候选轮换；
    * 任何失败返回 0，不影响卡片弹出。
    """
    if not cli_pid:
        return 0
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import win_focus                                  # noqa: PLC0415
        info = win_focus.resolve_host_window(cli_pid, cwd,
                                             exclude_pids={os.getpid()},
                                             cwd_fallback=False)
        if info is None:
            return 0
        if win_focus._WT_WINDOW_CLASS in str(info.cls or ""):
            return 0                                     # WT 标签页：不是精确目标
        return int(info.hwnd)
    except Exception:                                     # noqa: BLE001
        return 0


def kind_of(payload: dict) -> str:
    """事件 → ``done`` / ``auth`` / ``''``（'' 表示不关心）。"""
    event = str(payload.get("hook_event_name") or "")
    if event == "Stop":
        return "done"
    if event == "Notification":
        ntype = str(payload.get("notification_type") or "").lower()
        if "permission" in ntype:
            return "auth"
        text = (str(payload.get("message") or "") + " " + ntype).lower()
        if "permission" in text or "授权" in text:
            return "auth"
    return ""


def publish(event: dict, spool_dir: Path) -> None:
    """原子落盘：先写 .tmp 再 os.replace，app 只会读到完整 JSON。"""
    spool_dir.mkdir(parents=True, exist_ok=True)
    final = spool_dir / f"{time.time_ns()}_{os.getpid()}.json"
    tmp = final.with_suffix(".tmp")
    tmp.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, final)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="cb 狗皮膏药 hook 入口")
    ap.add_argument("--tool", default="cb", help="工具名，只用于显示")
    ap.add_argument("--project", type=Path, default=ROOT, help="项目根（logs/spool 落点）")
    ap.add_argument("--dry-run", action="store_true", help="只打印事件体，不落盘")
    args = ap.parse_args(argv)

    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except Exception:                                     # noqa: BLE001
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except ValueError:
        payload = {}

    kind = kind_of(payload)
    if not kind:
        return 0

    code, err = "", ""
    if kind == "done":
        # Stop 但回合是失败收场的（API 报错）→ 升级成 err 卡片
        code, err = error_of(payload)
        if code:
            kind = "err"

    cli_pid = find_cli_pid()
    event = {
        "ts": time.time(),
        "kind": kind,
        "tool": str(args.tool or "cb")[:16],
        "cwd": str(payload.get("cwd") or ""),
        "pid": cli_pid,
        "session": str(payload.get("session_id") or ""),
        # 点击跳转的精确制导：hook 时链还活着抓的窗口 HWND（WT 场景为 0，
        # app 端自动走会话标题 / 候选轮换）。抓不到不影响卡片弹出。
        "hwnd": capture_hwnd(cli_pid, str(payload.get("cwd") or "")),
        "message": (err or str(payload.get("message") or ""))[:200],
        "code": code,
        "err": err,
    }
    if args.dry_run:
        print(json.dumps(event, ensure_ascii=False))
        return 0
    publish(event, Path(args.project) / "logs" / "spool")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                                     # noqa: BLE001 - hook 永不失败
        raise SystemExit(0)
