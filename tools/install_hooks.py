"""装 / 卸「cb 狗皮膏药」的 hook：Stop（主任务完成）+ Notification（需要授权）。

写三处用户级 settings（与 auth hook 的教训一致：只改一处会有一边不认）：

* ``~/.codebuddy-cli/settings.json`` —— cb.bat 把 CODEBUDDY_CONFIG_DIR 指到这里；
  **ccb 弹窗驱动的 headless cb 也读这里**，所以装了这一处 ccb 的卡片就有了；
* ``~/.codebuddy/settings.json``    —— 另一条入口读这里；
* ``~/.claude/settings.json``       —— Claude Code（cc 交互 / ccc 弹窗共用，
  tool 名记 ``cc``；ccb / cc 交互跑 cb 时仍是 ``cb``）。

所有 hook 都与现有条目共存（已有 hook 一律不动），
只增删带 ``sticky_hook.py`` 标记的那几条；写前自动备份，幂等可反复跑。

用法::

    python tools/install_hooks.py                  # 装（三处全装）
    python tools/install_hooks.py --remove         # 卸
    python tools/install_hooks.py --dry-run        # 只看会写什么
    python tools/install_hooks.py --install-startup    # 开机自启（Startup 目录放 .vbs）
    python tools/install_hooks.py --remove-startup
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "sticky_hook.py"
HOOK_TIMEOUT = 5

#: 要注册的 hook 事件：Stop = 主任务完成；Notification = 授权弹窗等需要注意的事
EVENTS = ("Stop", "Notification")

#: hook 命令用的解释器探测链：环境变量 CB_STICKY_PYTHON → 跑本脚本的解释器
#: → PATH 上的 python → py 启动器。hook 本身只用标准库，任何 3.x 都行。
PREFERRED_PY_ENV = "CB_STICKY_PYTHON"

#: 每处 settings 对应的 tool 显示名（只影响卡片标题，不影响逻辑）。
#: cc 交互与 ccc 弹窗共用 ~/.claude，都记 cc；cb 交互与 ccb 弹窗共用
#: ~/.codebuddy-cli，都记 cb。
TARGETS: dict[str, tuple[Path, str]] = {
    "cb-cli": (Path.home() / ".codebuddy-cli" / "settings.json", "cb"),
    "cb-home": (Path.home() / ".codebuddy" / "settings.json", "cb"),
    "cc-home": (Path.home() / ".claude" / "settings.json", "cc"),
}

STARTUP_VBS = "cb_sticky.vbs"


def pick_python() -> str:
    """按探测链选 hook 解释器；返回正斜杠路径（hook 命令由 shell 执行）。"""
    env = os.environ.get(PREFERRED_PY_ENV, "").strip()
    if env and Path(env).exists():
        return env.replace("\\", "/")
    exe = str(sys.executable or "").strip()
    if exe and Path(exe).exists():
        return exe.replace("\\", "/")
    found = shutil.which("python") or shutil.which("python3")
    if found:
        return found.replace("\\", "/")
    return "python"


def hook_command(tool: str = "cb") -> str:
    script = (ROOT / "tools" / "sticky_hook.py").as_posix()
    return f'"{pick_python()}" "{script}" --tool {tool}'


# ==================================================================== settings
def load_settings(path: Path) -> dict:
    """读 settings.json；带 BOM 也能读，坏档抛 ValueError（调用方决定要不要覆盖）。"""
    raw = Path(path).read_text(encoding="utf-8-sig")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("settings.json 顶层不是对象")
    return data


def save_settings(path: Path, data: dict, *, backup: bool = True) -> Path | None:
    """写回 settings.json；写前备份，返回备份路径（没备份时为 None）。"""
    path = Path(path)
    backup_path = None
    if backup and path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak_{stamp}")
        shutil.copy2(path, backup_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup_path


def _clone(settings: dict) -> dict:
    return json.loads(json.dumps(settings)) if isinstance(settings, dict) else {}


def add_hooks(settings: dict, command: str) -> tuple[dict, int]:
    """把两条 hook 并进 settings；返回 (新 settings, 新增了几条)。

    幂等：已经装过就只更新命令（换了解释器 / 项目搬了也能自愈），不会越装越多。
    """
    data = _clone(settings)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    added = 0
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            groups = []
            hooks[event] = groups
        found = False
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks") or []:
                if isinstance(hook, dict) and MARKER in str(hook.get("command", "")):
                    hook["type"] = "command"
                    hook["command"] = command
                    hook.setdefault("timeout", HOOK_TIMEOUT)
                    found = True
        if not found:
            groups.append({"hooks": [{"type": "command", "command": command,
                                      "timeout": HOOK_TIMEOUT}]})
            added += 1
    return data, added


def remove_hooks(settings: dict) -> tuple[dict, int]:
    """删掉所有我们装的 hook，返回 (新 settings, 删了几条)。空组 / 空事件一起清掉。"""
    data = _clone(settings)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data, 0
    removed = 0
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            inner = group.get("hooks") or []
            kept = [h for h in inner
                    if not (isinstance(h, dict) and MARKER in str(h.get("command", "")))]
            removed += len(inner) - len(kept)
            if kept:
                new_group = dict(group)
                new_group["hooks"] = kept
                kept_groups.append(new_group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return data, removed


# ==================================================================== 开机自启
def startup_dir() -> Path:
    return (Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")


def pick_pythonw() -> str:
    """启动器用的 pythonw（无控制台）。找不到就退回 pyw 启动器。"""
    pyw = Path(sys.executable or "").with_name("pythonw.exe")
    if pyw.exists():
        return pyw.as_posix()
    found = shutil.which("pythonw")
    if found:
        return found.replace("\\", "/")
    return "pyw"


def install_startup() -> Path:
    """往 Startup 目录放一个隐藏窗口的 .vbs（双击/开机都会静默拉起守护进程）。

    自启入口是 **watchdog**（sticky_watchdog.py）而不是 app：守护负责拉起
    并盯住托盘实例，开机 / app 被杀都由这一条链复活。
    watchdog 检测到 app 已在跑就不会重复拉，和手动 run_sticky.cmd 兼容。

    .vbs 里**不写中文**：WSH 按 ANSI/GBK 读文件，中文注释会变乱码
    （.cmd 同理，实测踩过）。
    """
    vbs = startup_dir() / STARTUP_VBS
    cmd = f'"{pick_pythonw()}" "{(ROOT / "tools" / "sticky_watchdog.py").as_posix()}"'
    body = ("' cb_sticky autostart (hidden window, tray icon only)\n"
            'CreateObject("Wscript.Shell").Run "' + cmd.replace('"', '""')
            + '", 0, False\n')
    vbs.write_text(body, encoding="utf-8")
    return vbs


def remove_startup() -> bool:
    vbs = startup_dir() / STARTUP_VBS
    if vbs.exists():
        vbs.unlink()
        return True
    return False


# ==================================================================== main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="装 / 卸 cb 狗皮膏药的 hook")
    ap.add_argument("--remove", action="store_true", help="卸载而不是安装")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    ap.add_argument("--install-startup", action="store_true", help="加开机自启")
    ap.add_argument("--remove-startup", action="store_true", help="取消开机自启")
    args = ap.parse_args(argv)

    if args.install_startup or args.remove_startup:
        if args.dry_run:
            print(f"[dry]  startup: {startup_dir() / STARTUP_VBS}"
                  f" → {'安装' if args.install_startup else '移除'}")
        elif args.install_startup:
            print(f"[ok]   开机自启 → {install_startup()}")
        else:
            print("[ok]   开机自启已移除" if remove_startup()
                  else "[ok]   startup 里本来就没有")
        return 0                                    # 自启是独立动作，不顺手改 hooks

    command = ""
    changed = 0
    for name, (path, tool) in TARGETS.items():
        command = hook_command(tool)
        try:
            settings = load_settings(path) if path.exists() else {}
        except (OSError, ValueError) as exc:
            print(f"[skip] {name:8s} {path} 读不了（{exc}），为免覆盖坏档先不动它")
            continue

        if args.remove:
            data, removed = remove_hooks(settings)
            if not removed:
                print(f"[ok]   {name:8s} {path} 本来就没装")
                continue
            action = f"卸载 {removed} 条"
        else:
            data, added = add_hooks(settings, command)
            action = f"新增 {added} 条" if added else "更新命令"
            if data == settings:
                print(f"[ok]   {name:8s} {path} 已是最新")
                continue

        if args.dry_run:
            print(f"[dry]  {name:8s} {path} → {action}")
            print(json.dumps({e: data.get("hooks", {}).get(e) for e in EVENTS},
                             ensure_ascii=False, indent=2))
            continue

        try:
            backup = save_settings(path, data)
        except OSError as exc:
            print(f"[fail] {name:8s} 写 {path} 失败：{exc}")
            continue
        changed += 1
        note = f"（备份 {backup.name}）" if backup else ""
        print(f"[ok]   {name:8s} {path} → {action}{note}")

    if changed and not args.remove:
        print(f"\nhook 命令: {command}")
        print("cb / cc 的 hooks 都是热加载，通常即时生效；没生效就重开一个终端跑。")
        print("别忘了让守护活着：run_sticky.cmd 起托盘，watchdog 保它不死；")
        print("开机自启（--install-startup）现在走 watchdog 入口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
