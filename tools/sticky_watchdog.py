"""cb_sticky 守护进程：盯住托盘常驻实例（sticky_app.py），被杀 / 崩溃后自动拉起。

- 无窗口常驻（pythonw 跑），只依赖标准库 + psutil，比 app 更轻更底层；
- 每 5s 轮询一次：app 不在就重新拉起（DETACHED_PROCESS，脱离守护自己的进程组，
  守护被杀也不会连带 app）；
- 连续重启有退避：60s 内拉起超过 5 次 → 退避 60s，防「起了就崩」时疯狂拉；
- 单实例：logs/watchdog.pid，开机自启 + 手动双击不会开出两个守护；
- 所有动作（拉起 / 退避 / 启动 / 退出原因）追加写 logs/watchdog.log。

诚实声明：用户态进程挡不住「任务管理器结束进程 / taskkill /F」本身，
本守护的价值是 **app 被杀后 5 秒内自动复活**；守护自己同样可被杀，
开机自启只保「下次开机还在」。要更硬的防护得上服务级（NSSM/WinSW），
暂不做。

用法::

    pythonw tools/sticky_watchdog.py     # 常驻（无窗口）
    python  tools/sticky_watchdog.py     # 带控制台跑，方便看日志
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:                                        # psutil 缺失时守护无意义
    psutil = None

ROOT = Path(__file__).resolve().parent.parent
APP_SCRIPT = (ROOT / "tools" / "sticky_app.py").resolve()
LOG = ROOT / "logs" / "watchdog.log"
PID_FILE = ROOT / "logs" / "watchdog.pid"

POLL_SECONDS = 5          # 轮询间隔
RESTART_WINDOW = 60       # 退避统计窗口（秒）
RESTART_LIMIT = 5         # 窗口内最多拉起几次，超过就退避
BACKOFF_SECONDS = 60      # 退避时长

#: app 进程名（pythonw 优先；py 启动的会是 python.exe）
_APP_NAMES = {"pythonw.exe", "python.exe"}


def log(msg: str) -> None:
    """追加一行到 watchdog.log；日志写失败不影响守护本身。"""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def pick_pythonw() -> str:
    """与 app 同款解释器的无窗口版：同目录 pythonw.exe → PATH → pyw。"""
    exe = Path(sys.executable or "").with_name("pythonw.exe")
    if exe.exists():
        return str(exe)
    found = shutil.which("pythonw")
    if found:
        return found
    return "pyw"


def app_pid() -> int | None:
    """返回 sticky_app 的 pid；不在返回 None。

    精确匹配（教训：psutil 按 cmdline 模糊匹配会命中自己的诊断进程）：
    进程名必须是 pythonw/python.exe，且命令行里含 app 脚本绝对路径。
    """
    if psutil is None:
        return None
    target = str(APP_SCRIPT).lower().replace("\\", "/")
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            if (proc.info["name"] or "").lower() not in _APP_NAMES:
                continue
            cmdline = " ".join(proc.info["cmdline"] or []).lower().replace("\\", "/")
            if target in cmdline:
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def another_watchdog_alive() -> bool:
    """pid 文件里记的守护是否还活着（防开出第二个守护）。"""
    try:
        old = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if old == os.getpid():
        return False
    if psutil is None:
        return False
    try:
        cmdline = " ".join(psutil.Process(old).cmdline() or "").lower()
        return "sticky_watchdog.py" in cmdline
    except psutil.Error:
        return False


def spawn_app() -> None:
    """拉起托盘实例；DETACHED_PROCESS 让它不继承守护的进程组 / 控制台。"""
    subprocess.Popen(
        [pick_pythonw(), str(APP_SCRIPT)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200),
        close_fds=True,
    )


def main() -> int:
    if psutil is None:
        print("psutil 不可用，守护退出（pip install psutil 后重试）", file=sys.stderr)
        return 1
    if another_watchdog_alive():
        print("已有守护在跑，本实例退出", file=sys.stderr)
        return 0
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        log(f"pid 文件写失败（继续守护）: {exc!r}")
    log(f"watchdog start pid={os.getpid()} app={APP_SCRIPT}")
    restarts: list[float] = []          # 近期拉起时刻（用于退避统计）
    backoff_until = 0.0
    try:
        while True:
            time.sleep(POLL_SECONDS)
            now = time.time()
            if now < backoff_until:
                continue
            if app_pid() is not None:
                continue
            restarts = [t for t in restarts if now - t < RESTART_WINDOW]
            if len(restarts) >= RESTART_LIMIT:
                backoff_until = now + BACKOFF_SECONDS
                log(f"app {RESTART_WINDOW}s 内被拉起 {len(restarts)} 次"
                    f"（可能起了就崩），退避 {BACKOFF_SECONDS}s")
                continue
            try:
                spawn_app()
            except OSError as exc:
                log(f"拉起 app 失败: {exc!r}")
                continue
            restarts.append(now)
            log(f"app gone -> restarted（近 {RESTART_WINDOW}s 第 {len(restarts)} 次）")
    except KeyboardInterrupt:
        log("watchdog stop (KeyboardInterrupt)")
    finally:
        try:
            if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
