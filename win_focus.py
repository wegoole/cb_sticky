"""把「哪个窗口在跑 cb」变成可点击的跳转目标。

移植自一个经过多轮实战的桌面程序（Windows Terminal / conhost / IDE 终端都验过），
本例是自包含副本：**不 import 任何外部项目**，拷走这份文件即可独立工作。

回答两件事：

1. **这次 cb 是在哪个窗口里跑的？** —— 三条路，按可靠性排序：

   a. **祖先进程链**：Windows Terminal 里窗口属于
      ``WindowsTerminal.exe``，经典 cmd 里属于 ``conhost.exe``（不是 cmd 本身），
      所以按「祖先进程 → 它拥有的可见顶层窗口」匹配。**本机 cb 跑在 WT 里，
      走 ConPTY，shell 的父进程早退了，这条链必然断**——于是有 b；
   b. **会话标题**（WT 场景主力）：cb 启动时把 ``aiTitle`` 写进会话 jsonl
      （``projects/<cwd-slug>/<session_id>.jsonl`` 第 3 行，``type: ai-title``），
      同时用 OSC 序列把同一文本（braille spinner 前缀）设成 WT 标签标题。
      hook 事件带 ``session_id`` → 读 jsonl 拿 aiTitle → 匹配窗口标题；
   c. **cb 候选轮换**：以上都失败时，收集所有标题像 cb 的窗口
      （spinner 前缀 / ``CodeBuddy CLI`` / ``Claude Code`` / ``ccb ·`` / ``ccc ·``
      前缀，覆盖 cb / cc / ccb / ccc 四种启动器），唯一就直接跳，多个由上层逐个轮换。

另有一条**精确制导**通道（ccb / ccc 弹窗专用，见 ``sticky_hook.capture_hwnd``）：
hook 执行瞬间祖先进程链还活着，此时解析出的 HWND 直接落进事件；点击时
app 优先 focus 这个 HWND。WT 标签页（``CASCADIA_HOSTING_WINDOW_``）会被
主动抑制——focus 整个 WT 窗口不等于 focus 对应标签页，那种场景仍走
会话标题 / 候选轮换。

2. **用户点了卡片，怎么把那个窗口拉到前台？** —— ``SetForegroundWindow``
   在前台不是自己时会被系统拒绝，失败后用 ``AttachThreadInput`` 借前台线程的
   输入队列。

全部 ctypes 调 user32，不依赖 pywin32；非 Windows 平台 import 也不炸
（函数直接返回空）。窗口挑选的纯逻辑在 :func:`pick_host`，方便单测。
"""
from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # 64 位下 HWND 是指针；不声明 argtypes 会被默认按 32 位 int 截断
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
else:                                            # pragma: no cover - 非 Windows 仅用于导入
    _user32 = None
    _kernel32 = None

#: ``ShowWindow`` 的「从最小化恢复」
SW_RESTORE = 9


@dataclass(frozen=True)
class WinInfo:
    """一个可见的顶层窗口。"""

    hwnd: int
    pid: int
    title: str
    cls: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    @property
    def area(self) -> int:
        x0, y0, x1, y1 = self.rect
        return max(0, x1 - x0) * max(0, y1 - y0)


def list_windows() -> list[WinInfo]:
    """枚举当前所有**可见且有标题**的顶层窗口；失败返回空列表。"""
    if not _IS_WINDOWS:
        return []
    out: list[WinInfo] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _visit(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            length = _user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls_buf = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls_buf, 256)
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            out.append(WinInfo(int(hwnd), int(pid.value), title, cls_buf.value,
                               (rect.left, rect.top, rect.right, rect.bottom)))
        except Exception:                          # noqa: BLE001 - 枚举期间窗口随时会消失
            return True
        return True

    try:
        _user32.EnumWindows(_visit, 0)
    except Exception:                              # noqa: BLE001
        return []
    return out


def ancestor_pids(pid: int | None, limit: int = 16) -> list[int]:
    """返回 ``pid`` 的祖先进程（**近 → 远**），含自己。缺 psutil 时只返回自己。"""
    if not pid or pid <= 0:
        return []
    try:
        import psutil                                # noqa: PLC0415
    except Exception:                                # noqa: BLE001
        return [int(pid)]
    out = [int(pid)]
    try:
        proc = psutil.Process(pid)
        for _ in range(limit):
            parent = proc.parent()
            if parent is None or parent.pid in out:
                break
            out.append(int(parent.pid))
            proc = parent
    except Exception:                                # noqa: BLE001
        pass
    return out


def pick_host(windows: Sequence[WinInfo], ancestors: Sequence[int], *,
              exclude_pids: Iterable[int] = (), cwd: str | None = None) -> WinInfo | None:
    """在候选窗口里挑「最像终端」的那个。

    规则（按优先级）：

    1. 沿祖先链**由近到远**，第一个拥有可见窗口的进程；
    2. 同一进程有多个窗口时，取面积最大的（排除明显的小浮层）；
    3. 都没命中时，退回「标题里含 cwd 目录名」的窗口。

    ``exclude_pids`` 用来排掉狗皮膏药自己（以及测试里的假 pid）。
    """
    banned = {int(p) for p in exclude_pids if p}
    by_pid: dict[int, list[WinInfo]] = {}
    for win in windows:
        if win.pid in banned:
            continue
        by_pid.setdefault(win.pid, []).append(win)

    for pid in ancestors:
        candidates = [w for w in by_pid.get(int(pid), ()) if w.area >= 4000]
        if candidates:
            return max(candidates, key=lambda w: w.area)

    if cwd:
        name = Path(str(cwd).rstrip("\\/")).name.lower()
        if name:
            matches = [w for w in windows if w.pid not in banned and name in w.title.lower()]
            if matches:
                return max(matches, key=lambda w: w.area)
    return None


def resolve_host_window(pid: int | None = None, cwd: str | None = None,
                        exclude_pids: Iterable[int] = (),
                        *, cwd_fallback: bool = True) -> WinInfo | None:
    """从 CLI 进程 pid（或项目目录）解析出承载它的窗口。

    ``cwd_fallback=False`` 时只走祖先进程链，不做「标题含 cwd 目录名」的兜底
    —— WT 场景下那条兜底可能命中浏览器标签等无关窗口，交给调用方排在
    会话标题匹配之后再试。
    """
    windows = list_windows()
    if not windows:
        return None
    banned = {int(p) for p in exclude_pids if p}
    banned.add(os.getpid())                        # 自己永远不是目标
    info = pick_host(windows, ancestor_pids(pid), exclude_pids=banned)
    if info is not None:
        return info
    if not cwd_fallback:
        return None
    return pick_host(windows, (), exclude_pids=banned, cwd=cwd)


# ============================================================= WT 场景支援
#: braille spinner 区段 —— cb TUI 用 OSC 把「spinner + aiTitle」写进 WT 标签标题
_SPINNER_RANGE = range(0x2800, 0x2900)
_SPINNER_EXTRA = "✻✳✶✢·*"
#: 疑似 cb 终端的标题前缀。四种启动器的窗口都认：
#: cb.ps1 设的 ``CodeBuddy CLI``、cc.ps1 设的 ``Claude Code (pwsh7)``、
#: ccb / ccc 弹窗的 ``ccb · 模式 · 模型`` / ``ccc · 模式 · 模型``
#: （初始标题就是 ``ccb`` / ``ccc``，startswith 一并覆盖）。
_TERMINAL_TITLE_PREFIXES = ("codebuddy cli", "codebuddy",
                            "claude code", "ccb", "ccc")
#: 兼容旧名（外部脚本 / 测试可能还在引）
_CB_TITLE_PREFIXES = _TERMINAL_TITLE_PREFIXES
#: WT 标签页的窗口类名——命中它说明「窗口属于整个 WT 而不是某个会话」，
#: 不能当精确跳转目标（hook 端据此抑制 hwnd 记录，见 sticky_hook.capture_hwnd）
_WT_WINDOW_CLASS = "CASCADIA_HOSTING_WINDOW_"
#: 读会话 jsonl 时最多扫多少字节（ai-title 行在文件头部，128KB 足够）
_JSONL_HEAD_BYTES = 131072


def looks_like_cb_title(title: str) -> bool:
    """标题像不像 cb 终端的：spinner 前缀（⠸/⠧…）或四种启动器的标题前缀。"""
    text = (title or "").strip()
    if not text:
        return False
    first = text[0]
    if ord(first) in _SPINNER_RANGE or first in _SPINNER_EXTRA:
        return True
    low = text.lower()
    return any(low.startswith(p) for p in _TERMINAL_TITLE_PREFIXES)


def cb_candidate_windows(exclude_pids: Iterable[int] = ()) -> list[WinInfo]:
    """所有「疑似 cb 终端」的可见窗口（WT 标签页 / conhost 都算）。"""
    banned = {int(p) for p in exclude_pids if p}
    banned.add(os.getpid())
    return [w for w in list_windows()
            if w.pid not in banned and w.area >= 4000 and looks_like_cb_title(w.title)]


def _projects_roots() -> list[Path]:
    """cb 的配置根（会话 jsonl 在 ``<root>/projects/`` 下）。"""
    roots: list[Path] = []
    env = os.environ.get("CODEBUDDY_CONFIG_DIR")
    if env:
        roots.append(Path(env))
    home = Path.home()
    roots.append(home / ".codebuddy-cli")
    roots.append(home / ".codebuddy")
    seen, out = set(), []
    for r in roots:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _cwd_slug(cwd: str) -> str:
    """``c:\\Users\\alice`` → ``c-Users-alice``（cb 的 projects 目录名规则）。"""
    text = str(cwd or "").strip().rstrip("\\/")
    return text.replace(":", "").replace("\\", "-").replace("/", "-")


def session_ai_title(session_id: str, cwd: str | None = None) -> str:
    """读会话 jsonl 里的 ``aiTitle``（cb 同时把它 OSC 写进了 WT 标签标题）。

    找不到 / 读失败一律返回空串——调用方走候选轮换兜底。
    """
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    wanted = f"{sid}.jsonl"
    for root in _projects_roots():
        projects = root / "projects"
        paths: list[Path] = []
        if cwd:
            paths.append(projects / _cwd_slug(cwd) / wanted)
        else:                                       # 没给 cwd 就全项目扫一遍
            try:
                paths.extend(sorted(projects.glob(f"*/{wanted}")))
            except OSError:
                pass
        for path in paths:
            try:
                if not path.is_file():
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    head = fh.read(_JSONL_HEAD_BYTES)
            except OSError:
                continue
            for line in head.splitlines():
                if '"ai-title"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                title = str(obj.get("aiTitle") or "").strip()
                if title:
                    return title
    return ""


def foreground_hwnd() -> int:
    if not _IS_WINDOWS:
        return 0
    try:
        return int(_user32.GetForegroundWindow() or 0)
    except Exception:                              # noqa: BLE001
        return 0


def is_window(hwnd: int) -> bool:
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        return bool(_user32.IsWindow(int(hwnd)))
    except Exception:                              # noqa: BLE001
        return False


def focus_window(hwnd: int) -> bool:
    """把窗口恢复到前台；成功返回 True。

    ``SetForegroundWindow`` 在前台窗口不属于本进程时会被系统静默拒绝，所以失败后
    再借前台线程的输入队列（``AttachThreadInput``）重试一次 —— 这是任务栏点击之外
    最稳的做法。
    """
    if not is_window(hwnd):
        return False
    hwnd = int(hwnd)
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        if _user32.SetForegroundWindow(hwnd):
            return True

        fg = int(_user32.GetForegroundWindow() or 0)
        fg_tid = int(_user32.GetWindowThreadProcessId(fg, None)) if fg else 0
        cur_tid = int(_kernel32.GetCurrentThreadId())
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(_user32.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            _user32.BringWindowToTop(hwnd)
            ok = bool(_user32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                _user32.AttachThreadInput(cur_tid, fg_tid, False)
        return ok or int(_user32.GetForegroundWindow() or 0) == hwnd
    except Exception:                              # noqa: BLE001
        return False
