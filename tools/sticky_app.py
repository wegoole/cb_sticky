"""cb 狗皮膏药主程序：托盘常驻 + 右上角「只能按 × 消除」的强提醒卡片。

链路::

    cb Stop hook ─┐
                  ├→ tools/sticky_hook.py → logs/spool/*.json → 本程序 300ms 捞一次
    cb Notification hook（permission_prompt）┘
    cc / ccc / ccb 的 hook 同上（配置根不同，事件 schema 同构）

设计要点：

* 贴在**右上角**，锚定屏幕、无倒计时、不自动消失，只能按 ×；
* 点卡片本体 = 尝试把对应终端窗口拉到前台（失败只改提示，卡片还在）；
* 三种风格：``done`` 绿条 / ``auth`` 黄黑警示纹（左强右弱）/
  ``err`` 鲜红渐隐（429 特化标题「好烧，哥们好烧👍」，其余「{错误号} 超！级！大！爆！」）；
* 同屏最多 :data:`MAX_CARDS` 张，超出丢最旧；托盘图标会闪，提示还有存量。

residency（「贴在隐藏栏」）：平时只有一个托盘图标，没有任何窗口；
卡片清空后 stack 自动隐藏。单实例守卫（QSharedMemory）保证开机自启 +
手动双击不会开出两个。

**崩溃防护**（实战教训）：PyQt6 槽函数里没接住的异常会直接
``qFatal → abort()``（无声闪退）。所以这里每个槽都自己兜异常。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPointF, QRectF, QSharedMemory, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QIcon, QCursor,
                         QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF)
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMenu,
                             QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget)

import win_focus

SPOOL_DIR = ROOT / "logs" / "spool"
MAX_CARDS = 6                  # 同屏最多几张；超出丢最旧（托盘会闪提示存量）
POLL_MS = 300
CARD_WIDTH = 340
MARGIN = 18
FLASH_MS = 500
MAX_DRAIN_PER_TICK = 50        # 补弹时一轮最多处理多少，防刚启动被淹没

ACCENT_DONE = "#3ecf8e"        # 主任务完成 = 绿
ACCENT_AUTH = "#f5a623"        # 等待授权 = 琥珀（auth 卡本体走黄黑警示纹）
ACCENT_ERR = "#ff3b3b"         # API 错误 = 红

#: auth 卡左侧黄黑警示带的宽度（px），文字从它右边开始
TAPE_WIDTH = 66
#: 黄黑斜纹瓷砖周期（px）；黄条占一半
HAZARD_STEP = 28


def _hazard_tile() -> QPixmap:
    """可无缝平铺的 45° 黄黑斜纹瓷砖（警示带），全局缓存一份。"""
    cached = getattr(_hazard_tile, "_tile", None)
    if cached is not None:
        return cached
    px = QPixmap(HAZARD_STEP, HAZARD_STEP)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(255, 212, 0, 235))     # 警示黄
    painter.setPen(Qt.PenStyle.NoPen)
    # 黄色区域 = (x + y) mod 28 < 14，即瓷砖里两个对角三角
    painter.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(14, 0), QPointF(0, 14)]))
    painter.drawPolygon(QPolygonF([QPointF(28, 14), QPointF(14, 28),
                                   QPointF(28, 28)]))
    painter.end()
    _hazard_tile._tile = px                        # noqa: SLF001 - 函数级缓存
    return px


def _log_exc(where: str, exc: BaseException) -> None:
    """被吞掉的异常也留一行到 logs/crash.log。

    实战教训：静默失败比崩溃难查一百倍——卡片不弹这种「什么都没发生」的
    故障，只有落盘才能定位。
    """
    try:
        (ROOT / "logs").mkdir(parents=True, exist_ok=True)
        with (ROOT / "logs" / "crash.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%F %T')}] {where}: {exc!r}\n")
    except Exception:                              # noqa: BLE001
        pass


# ==================================================================== 卡片
class StickyCard(QFrame):
    """右上角常驻卡片：没有倒计时，只能按 × 消除。

    三种美术风格（``style``）：

    * ``done`` —— 深色底 + 左侧绿色 accent 条；
    * ``auth`` —— 警示黄：左侧黄黑斜纹带（右缘羽化），整卡向右淡出；
    * ``err``  —— 整卡左侧鲜红、向右渐隐（429 有特化标题）。
    """

    closed = pyqtSignal(object)        # 参数是卡片自己，方便上层移除
    activated = pyqtSignal(object)

    #: 各风格的（标题色, 副标题色, 提示色）
    _TEXT = {
        "done": ("#ffffff", "#b9bec9", "#7f8794"),
        "auth": ("#ffd400", "#f2f3f5", "#a8a29a"),
        "err":  ("#ffffff", "#ffdada", "#e8a0a0"),
    }

    def __init__(self, title: str, subtitle: str, *, accent: str, hint: str,
                 style: str = "done", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._style = style if style in self._TEXT else "done"
        self._accent = accent
        self.setObjectName("stickyCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(CARD_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        title_col, sub_col, hint_col = self._TEXT[self._style]
        self.setStyleSheet(
            "#stickyCard { background: transparent; border-radius: 12px; }"
            f"QLabel {{ background: transparent; color: {sub_col}; }}"
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 10, 0)
        root.setSpacing(0)

        # auth 卡左边是 66px 黄黑警示带，文字避开它；其余风格 5px accent 条
        left = 78 if self._style == "auth" else 17
        body = QVBoxLayout()
        body.setContentsMargins(left, 10, 4, 10)
        body.setSpacing(2)
        self._title = QLabel(title, self)
        font = QFont("Microsoft YaHei", 10)
        font.setBold(True)
        self._title.setFont(font)
        self._title.setStyleSheet(f"color: {title_col};")
        self._subtitle = QLabel(subtitle, self)
        self._subtitle.setFont(QFont("Microsoft YaHei", 9))
        self._subtitle.setStyleSheet(f"color: {sub_col};")
        self._subtitle.setWordWrap(True)
        self._hint = QLabel(hint, self)
        self._hint.setFont(QFont("Microsoft YaHei", 8))
        self._hint.setStyleSheet(f"color: {hint_col};")
        body.addWidget(self._title)
        body.addWidget(self._subtitle)
        body.addWidget(self._hint)
        root.addLayout(body, 1)

        self._close = QPushButton("×", self)
        self._close.setFixedSize(18, 18)
        self._close.setCursor(Qt.CursorShape.ArrowCursor)
        self._close.setStyleSheet(
            "QPushButton { border: none; background: transparent; color: #8b93a1;"
            " font-size: 14px; }"
            "QPushButton:hover { color: #ffffff; }")
        self._close.clicked.connect(lambda: self.closed.emit(self))
        root.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)

    # ---------------------------------------------------------------- #
    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:          # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setClipPath(path)
        self._paint_background(painter, path)
        # 细描边：纯深色卡片贴在深色桌面上时能看出边界
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        painter.end()

    def _paint_background(self, painter: QPainter, path: QPainterPath) -> None:
        """按风格画卡片底：done=深色+绿条 / auth=黄黑警示 / err=鲜红渐隐。"""
        width = self.width()
        if self._style == "err":
            # 整卡左侧鲜红，向右渐隐
            grad = QLinearGradient(0, 0, width, 0)
            grad.setColorAt(0.00, QColor(255, 46, 46, 246))
            grad.setColorAt(0.38, QColor(232, 32, 32, 216))
            grad.setColorAt(0.72, QColor(158, 18, 18, 126))
            grad.setColorAt(1.00, QColor(112, 12, 12, 72))
            painter.fillPath(path, grad)
            return
        # done / auth 共用深色底
        painter.fillPath(path, QColor(30, 32, 38, 242))
        if self._style == "done":
            painter.fillRect(QRectF(0, 0, 5, self.height()), QColor(self._accent))
            return
        # auth：左侧黄黑警示带（右缘羽化）+ 整卡向右的警示黄微光
        painter.fillRect(QRectF(0, 0, TAPE_WIDTH, self.height()),
                         QBrush(_hazard_tile()))
        feather = QLinearGradient(TAPE_WIDTH - 14, 0, TAPE_WIDTH, 0)
        feather.setColorAt(0.0, QColor(30, 32, 38, 0))
        feather.setColorAt(1.0, QColor(30, 32, 38, 255))
        painter.fillRect(QRectF(TAPE_WIDTH - 14, 0, 14, self.height()), feather)
        glow = QLinearGradient(TAPE_WIDTH, 0, width, 0)
        glow.setColorAt(0.0, QColor(255, 212, 0, 42))
        glow.setColorAt(1.0, QColor(255, 212, 0, 0))
        painter.fillPath(path, glow)


class StickyStack(QWidget):
    """一列卡片，新的在上；自动贴着**屏幕右上角**。"""

    def __init__(self, *, max_cards: int = MAX_CARDS, margin: int = MARGIN,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._max = max(1, int(max_cards))
        self._margin = int(margin)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

    # ---------------------------------------------------------------- #
    @property
    def cards(self) -> list[StickyCard]:
        out = []
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, StickyCard):
                out.append(widget)
        return out

    def push(self, title: str, subtitle: str, *, accent: str, hint: str,
             on_activate=None, style: str = "done") -> StickyCard:
        card = StickyCard(title, subtitle, accent=accent, hint=hint,
                          style=style, parent=self)
        card.closed.connect(self._dismiss)
        card.activated.connect(lambda c: self._activate(c, on_activate))
        self._layout.insertWidget(0, card)
        self._trim()
        self._relayout()
        return card

    def clear(self) -> None:
        for card in self.cards:
            self._dismiss(card)

    # ---------------------------------------------------------------- #
    def _activate(self, card: StickyCard, on_activate) -> None:
        """点卡片 = 尝试跳终端；**永远不因为点击而消失**（只能按 ×）。"""
        try:
            result = on_activate() if on_activate else False
        except Exception as exc:                   # noqa: BLE001 - 跳转失败不该崩
            _log_exc("activate", exc)
            result = False
        if isinstance(result, str) and result:
            card.set_hint(result)                  # 调用方给了完整提示（含轮换信息）
        else:
            card.set_hint("已切到终端 · 只能按 × 消除" if result
                          else "窗口找不到了 · 只能按 × 消除")

    def _dismiss(self, card: StickyCard) -> None:
        try:
            self._layout.removeWidget(card)
        except Exception:                          # noqa: BLE001
            pass
        card.hide()
        card.deleteLater()
        QTimer.singleShot(0, self._after_dismiss)

    def _after_dismiss(self) -> None:
        if not self.cards:
            self.hide()
            return
        self._relayout()

    def _trim(self) -> None:
        for card in self.cards[self._max:]:
            self._dismiss(card)

    def _relayout(self) -> None:
        self.adjustSize()
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            return
        avail = screen.availableGeometry()
        x = avail.right() + 1 - self.width() - self._margin
        y = avail.top() + self._margin
        self.move(max(avail.left(), x), y)
        self.show()
        self.raise_()


# ======================================================== 点击跳转（带轮换）
class _Focuser:
    """点卡片 → 把对应 cb 终端拉到前台。

    解析顺序（WT 场景见 :mod:`win_focus` 文档头）：

    0. **hook 时记录的 HWND**（ccb / ccc 弹窗 / conhost 的精确制导）——
       hook 执行瞬间祖先链还活着，抓到的窗口 HWND 直接落进事件；
       窗口已关就继续往下走；
    1. 祖先进程链（非 WT 场景：conhost 等）；
    2. 会话标题：读 session jsonl 的 ``aiTitle``，匹配 WT 标签标题；
    3. cb 候选窗口唯一 → 直接跳；多个 → **每次点击轮换一个**，
       提示里写明「第 k/n 个，再次点击切换」。

    返回 True=成功（用默认提示），str=完整提示，False=找不到窗口。
    所有异常自己兜住并落 crash.log（槽函数异常会 qFatal）。
    """

    def __init__(self, pid: int, cwd: str, session: str,
                 hwnd: int = 0) -> None:
        self._pid = int(pid or 0)
        self._cwd = str(cwd or "")
        self._session = str(session or "")
        self._hwnd = int(hwnd or 0)
        self._cycle = 0

    def __call__(self):
        try:
            return self._run()
        except Exception as exc:                   # noqa: BLE001
            _log_exc("focus", exc)
            return "窗口找不到了 · 只能按 × 消除"

    def _run(self):
        banned = {os.getpid()}
        # 0) hook 时抓到的 HWND：ccb/ccc 弹窗与 conhost 的精确目标
        if self._hwnd and win_focus.is_window(self._hwnd) \
                and win_focus.focus_window(self._hwnd):
            return True
        # 1) 祖先进程链（WT 里这条链是断的，但 conhost 场景靠它）
        info = win_focus.resolve_host_window(self._pid, self._cwd,
                                             exclude_pids=banned, cwd_fallback=False)
        if info and win_focus.focus_window(info.hwnd):
            return True
        # 2) 会话标题精确匹配（WT 场景主力）
        cands = win_focus.cb_candidate_windows(exclude_pids=banned)
        title = win_focus.session_ai_title(self._session, self._cwd)
        if title:
            exact = [w for w in cands if title in w.title]
            if len(exact) == 1 and win_focus.focus_window(exact[0].hwnd):
                return True
            if exact:
                cands = exact
        # 3) 候选唯一 / 多个轮换
        if not cands:
            # 4) 老回退：标题含 cwd 目录名（可能误中浏览器，排最后）
            info = win_focus.resolve_host_window(self._pid, self._cwd,
                                                 exclude_pids=banned, cwd_fallback=True)
            if info and win_focus.focus_window(info.hwnd):
                return True
            return "窗口找不到了 · 只能按 × 消除"
        if len(cands) == 1:
            return True if win_focus.focus_window(cands[0].hwnd) \
                else "窗口找不到了 · 只能按 × 消除"
        idx = self._cycle % len(cands)
        self._cycle += 1
        if not win_focus.focus_window(cands[idx].hwnd):
            return "窗口找不到了 · 只能按 × 消除"
        return (f"已切到终端 · 第 {idx + 1}/{len(cands)} 个 cb 窗口"
                f"（再次点击切换）")


# ==================================================================== 托盘
def make_icon(flash: bool = False) -> QIcon:
    """程序化画图标：蓝底白字 cb；有待消卡片时闪琥珀色。"""
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#f5a623") if flash else QColor("#4f8cff"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setPen(QColor("#ffffff"))
    font = QFont("Arial", 26)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "cb")
    painter.end()
    return QIcon(px)


class StickyApp:
    """托盘 + 卡片 + spool 轮询的总装。所有槽函数自己兜异常（防 qFatal）。"""

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.app.setQuitOnLastWindowClosed(False)
        self.stack = StickyStack()

        self._flash_on = False
        self._flash_timer = QTimer()
        self._flash_timer.timeout.connect(self._tick_flash)

        self.tray = QSystemTrayIcon(make_icon())
        self.tray.setToolTip("cb 狗皮膏药 · 监听中")
        menu = QMenu()
        menu.addAction("测：主任务完成卡片", self._test_done)
        menu.addAction("测：等待授权卡片", self._test_auth)
        menu.addAction("测：错误卡片(429)", self._test_err)
        menu.addAction("清空所有卡片", self.stack.clear)
        menu.addAction("退出", self.app.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

        self._poll = QTimer()
        self._poll.timeout.connect(self._drain_spool)
        self._poll.start(POLL_MS)

    # ---------------------------------------------------------------- 事件
    def _push_event(self, ev: dict) -> None:
        kind = str(ev.get("kind") or "")
        cwd = str(ev.get("cwd") or "")
        project = Path(cwd).name if cwd else "未知目录"
        pid = int(ev.get("pid") or 0)
        session = str(ev.get("session") or "")
        hwnd = int(ev.get("hwnd") or 0)
        tool = str(ev.get("tool") or "cb")
        # 任务名：读会话 jsonl 的 aiTitle——多个 cb 会话的 cwd 往往相同，
        # 只有它能区分「谁是谁」。读不到就退回 tool 名（几张卡都长一样）。
        task = ""
        try:
            task = win_focus.session_ai_title(session, cwd)
        except Exception as exc:                   # noqa: BLE001
            _log_exc("push/aiTitle", exc)
        head = f"{task or tool} · {project}"
        if kind == "done":
            self.stack.push(
                "主任务完成", head,
                accent=ACCENT_DONE, hint="点击回到终端 · 只能按 × 消除",
                on_activate=_Focuser(pid, cwd, session, hwnd))
        elif kind == "auth":
            msg = str(ev.get("message") or "").strip() or "需要你的授权"
            self.stack.push(
                "授权请求", f"{head}\n{msg}",
                accent=ACCENT_AUTH, hint="点击回到终端 · 只能按 × 消除",
                style="auth", on_activate=_Focuser(pid, cwd, session, hwnd))
        elif kind == "err":
            code = str(ev.get("code") or "ERR")
            err = str(ev.get("err") or "").strip()
            # 429（额度耗尽）特化标题；其余错误号一律「超！级！大！爆！」
            title = "好烧，哥们好烧👍" if code == "429" else f"{code} 超！级！大！爆！"
            self.stack.push(
                title, f"{head}\n{err}" if err else head,
                accent=ACCENT_ERR, hint="点击回到终端 · 只能按 × 消除",
                style="err", on_activate=_Focuser(pid, cwd, session, hwnd))

    def _drain_spool(self) -> None:
        try:
            files = sorted(SPOOL_DIR.glob("*.json"))
        except OSError:
            return
        for path in files[:MAX_DRAIN_PER_TICK]:
            try:
                ev = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(ev, dict):
                    raise ValueError("spool 文件不是 JSON 对象")
            except (OSError, ValueError):
                # 解析不了的隔离成 .bad，不反复刷同一个坏文件
                try:
                    path.rename(path.with_suffix(".bad"))
                except OSError:
                    pass
                continue
            try:
                self._push_event(ev)
            except Exception as exc:               # noqa: BLE001
                _log_exc("drain/push", exc)
            try:
                path.unlink()
            except OSError:
                pass
        self._refresh_tray()

    # ---------------------------------------------------------------- 托盘
    def _refresh_tray(self) -> None:
        pending = len(self.stack.cards)
        self.tray.setToolTip(
            f"cb 狗皮膏药 · 监听中（{pending} 条待消）" if pending
            else "cb 狗皮膏药 · 监听中")
        if pending and not self._flash_timer.isActive():
            self._flash_timer.start(FLASH_MS)
        elif not pending and self._flash_timer.isActive():
            self._flash_timer.stop()
            self._flash_on = False
            self.tray.setIcon(make_icon(False))

    def _tick_flash(self) -> None:
        self._flash_on = not self._flash_on
        self.tray.setIcon(make_icon(self._flash_on))

    # ---------------------------------------------------------------- 测试
    def _test_done(self) -> None:
        self._push_event({"kind": "done", "tool": "cb", "cwd": os.getcwd(),
                          "pid": 0, "message": ""})
        self._refresh_tray()

    def _test_auth(self) -> None:
        self._push_event({"kind": "auth", "tool": "cb", "cwd": os.getcwd(),
                          "pid": 0, "message": "需要执行 Bash(taskkill:*)（测试）"})
        self._refresh_tray()

    def _test_err(self) -> None:
        self._push_event({"kind": "err", "tool": "cb", "cwd": os.getcwd(),
                          "pid": 0, "code": "429",
                          "err": "429 Too Many Requests: quota_balance_exhausted（测试）"})
        self._refresh_tray()


def main() -> int:
    app = QApplication(sys.argv)
    # 单实例：已有实例（开机自启 / 手动双击）就直接退，不开第二个
    guard = QSharedMemory("cb_sticky_singleton_v1")
    if guard.attach():
        return 0
    guard.create(1)
    sticky = StickyApp(app)
    sticky._guard = guard                          # noqa: SLF001 - 防 GC 掉共享内存
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:                              # noqa: BLE001 - 启动失败也留痕
        try:
            (ROOT / "logs").mkdir(parents=True, exist_ok=True)
            with (ROOT / "logs" / "crash.log").open("a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%F %T')}] 启动失败\n")
                import traceback
                traceback.print_exc(file=fh)
        except Exception:                          # noqa: BLE001
            pass
        raise SystemExit(1)
