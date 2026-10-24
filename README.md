# cb_sticky —— 托盘常驻的 CLI 强提醒 / Tray-resident CLI Notifier

「贴上就撕不下来」的 CLI 监听器：平时只有一个**托盘图标**，一旦 CLI **主任务完成**、
**弹窗要授权**或 **API 报错**，立刻在屏幕**右上角**贴一张**常驻卡片**——没有倒计时、
不自动消失、移开鼠标也不溜，**只能按 × 消除**。专治「系统气泡通知 7 秒后没了 /
移开鼠标正好错过」。

支持四种启动器：**cb**（CodeBuddy CLI，Windows Terminal 交互）、**ccb**（cb 弹窗驱动
headless 模式）、**cc / ccc**（Claude Code 交互 / 弹窗驱动）。

- **平台**：Windows only（窗口定位用 Win32 ctypes；托盘本身跨平台，但抢前台不是）
- **依赖**：hook 端**纯标准库零依赖**；GUI 端 PyQt6；psutil 可选（没有也能跑）
- **Python**：3.8+，任何 3.x 都行（hook 端）；GUI 端建议 3.9+

---

## 触发口径

| 事件 | hook | 语义 |
|---|---|---|
| 主任务完成 | `Stop` | 主循环答完一轮。**子任务完成走 `SubagentStop`，这里故意不接**——这正是「不是一个子任务完成」的区别 |
| API 错误 | `Stop` | 回合失败收场（429/400/500…）时 Stop **照常触发**，错误信息在 `last_assistant_message` 开头（形如 `400 model [x] service info not found (...)`）。hook 按 4xx/5xx 前缀升级成 err 卡片 |
| 需要授权 | `Notification` | `notification_type` 为 `permission_prompt`（授权弹窗）。其余 Notification 类型（auth_success / idle_prompt / agent_completed…）不弹卡 |

## 架构

```
cb Stop hook ─┐
              ├→ tools/sticky_hook.py → logs/spool/*.json → sticky_app.py（300ms 捞一次）
cb Notification hook（permission）┘
cc / ccc（~/.claude/settings.json）与 ccb / cb（codebuddy 配置根）的 hook 同上
```

- **hook 端只干一件事**：判事件类型 → 原子落盘（`.tmp` + `os.replace`）。
  几十毫秒返回，任何失败安静 exit 0，绝不拖住 CLI。
- **spool 目录当中转**：app 没开事件不丢——重启后按时间顺序补弹（受每屏卡片数上限约束）。
  这也是为什么不用 HTTP POST：没有端口占用、没有防火墙问题、多开会话各自写各的文件。
- **app 端**：`QSystemTrayIcon` 常驻托盘 + 右上角无倒计时卡片栈。单实例守卫
  （`QSharedMemory`），开机自启和手动双击不会开出两个。有待消卡片时托盘图标闪琥珀色。
- **点卡片本体** = 尝试把对应终端窗口拉到前台（`win_focus.py`，支持
  Windows Terminal / conhost / IDE 终端）；失败只改提示文字，卡片还在。WT 场景的解析见下节。
- **崩溃防护**：所有 Qt 槽函数自己兜异常——PyQt6 槽里没接住的异常会
  `qFatal → abort()` 无声闪退。

## 安装 / 使用

```bash
# 1. 装 hook（写 codebuddy / claude 三处 settings，自动备份，幂等；
#    ccb 与 cb 共用 codebuddy 配置根，cc 与 ccc 共用 .claude，装完全覆盖）
py -3 tools/install_hooks.py --dry-run   # 先看会写什么
py -3 tools/install_hooks.py

# 2. 让托盘程序活着（二选一）
./run_sticky.cmd                                  # 手动起
py -3 tools/install_hooks.py --install-startup    # 开机自启

# 卸载
py -3 tools/install_hooks.py --remove
py -3 tools/install_hooks.py --remove-startup
```

cb 的 hooks 是热加载，装完通常即时生效；没生效就重开一个终端跑 cb。

hook 命令里的解释器按探测链选取（`tools/install_hooks.py: pick_python()`）：
环境变量 `CB_STICKY_PYTHON` → 跑安装器本人的解释器 → PATH 上的 `python` → `py` 启动器。
hook 本身只用标准库，换任何一个 3.x 都行；GUI 端需要 PyQt6。

## 点卡片怎么找到「那个」CLI 窗口（WT 场景）

cb / cc 跑在 Windows Terminal 里走 ConPTY：shell（pwsh）的父进程早退了，
**祖先进程链必然断**，「祖先 → 窗口」在 WT 里一律找不到。四条路按可靠性排序：

0. **hook 时记录的 HWND**（精确制导，ccb / ccc 弹窗专用）——hook 执行**瞬间**
   祖先链还活着（node / claude.exe 的父进程 pwsh 持有真窗口），此时解析出的
   HWND 直接落进 spool 事件；点击时 app 优先 focus 它。WT 标签页
   （`CASCADIA_HOSTING_WINDOW_`）会被**主动抑制记 0**——focus 整个 WT 窗口
   不等于 focus 对应标签页，那种场景走下面的路；
1. **祖先进程链**（`win_focus.pick_host`）——非 WT 场景（conhost / IDE 终端）靠它；
2. **会话标题精确匹配**（WT 场景主力）——cb 启动时把 `aiTitle` 写进会话 jsonl
   第 3 行（`type: "ai-title"`），同时用 OSC 序列把同一文本（braille spinner
   前缀）设成 WT 标签标题。hook 事件带 `session_id` → `win_focus.session_ai_title()`
   读 jsonl → 匹配窗口标题。**每个 WT 标签页是独立顶层 HWND**（同属
   WindowsTerminal.exe），focus 单个 HWND 即激活对应标签页；
3. **cb 候选轮换**——以上都失败时收集所有标题像 CLI 的窗口（spinner 前缀 /
   `CodeBuddy CLI` / `Claude Code` / `ccb ·` / `ccc ·` 前缀，四种启动器都认）：
   唯一直接跳；多个则每次点击轮换一个，提示语写明「第 k/n 个（再次点击切换）」。

cc / ccc（Claude Code）的会话 jsonl 不落标题，任务名降级显示项目名；
ccb 的会话 jsonl 有时有 `ai-title`（异步生成，不保证）。

## 卡片风格

| 风格 | 触发 | 样式 |
|---|---|---|
| `done` | 主任务完成 | 深色底 + 左侧绿色 accent 条 |
| `auth` | 授权弹窗 | **警示黄**：左侧 66px 黄黑斜纹带（右缘羽化），整卡向右淡出；标题黄色 |
| `err` | API 错误 | 整卡**左侧鲜红、向右渐隐**；标题：429 → 「好烧，哥们好烧👍」，其余 → 「{错误号} 超！级！大！爆！」 |

所有卡片：右上角锚定、无倒计时、只能按 × 消除、点本体跳对应终端。

## 手动喂事件调试（注意引号陷阱）

    # 载荷里千万别写反斜杠路径——hook 是 stdin 收 JSON，
    # 而 Git Bash / 某些 harness 会把 \\ 折叠成 \，JSON 静默解析失败、
    # hook 判「不关心」直接退出（表现就是 spool 空空、卡片不弹）。
    # 用正斜杠 cwd 即可：
    echo '{"hook_event_name":"Stop","cwd":"C:/Users/you","session_id":"<sid>","last_assistant_message":"429 ..."}' |
        python tools/sticky_hook.py --tool cb --dry-run

想抓 CLI 真实发出的完整载荷：`tools/_dump_hook.py`（把 stdin 原样落
`logs/_hook_payloads.log`），配一个临时 `CODEBUDDY_CONFIG_DIR` 指向只含
该 hook 的 settings.json，再用坏模型名 / 坏 Key 跑 headless CLI 触发错误回合。

## 托盘菜单

- **测：主任务完成卡片 / 测：等待授权卡片** —— 不真去跑 CLI 也能看效果；
- **清空所有卡片**；
- **退出**。

## 文件

| 文件 | 作用 |
|---|---|
| `tools/sticky_hook.py` | hook 入口（纯标准库）：Stop→done（失败回合→err，错误码取 `last_assistant_message` 的 4xx/5xx 前缀）、Notification(permission)→auth，原子写 spool；落盘时抓 `hwnd`（hook 瞬间链还活着；WT 场景抑制为 0） |
| `tools/sticky_app.py` | 托盘 + 右上角常驻卡片主程序（PyQt6）；三种卡片风格 done / auth / err；点击跳转优先用事件里的 hwnd |
| `tools/_dump_hook.py` | 调试用：把 CLI 发来的 hook 载荷原样落 `logs/_hook_payloads.log`（抓真实事件体） |
| `tools/_probe_ui.py` | UI 自检探针：绕过 hook 直接喂事件，打印卡片可见性 / 几何形状（排查「卡片不弹」用） |
| `tools/install_hooks.py` | 装/卸两条 hook（**三处 settings：cb-cli / cb-home / cc-home**，自动备份，幂等）+ 开机自启 |
| `tools/_popup_test.ps1` + `tools/_hook_driver.py` | 弹窗 HWND 抓取回归：复刻 ccc 进程链（sticky_hook → claude.exe → pwsh 弹窗），验证事件 hwnd == 弹窗 handle |
| `tools/_focus_hwnd_test.py` / `_focus_hwnd_test2.py` | 前台跳转自证：focus_window 后核对 foreground == 目标 hwnd |
| `tools/_tk_popup.py` / `tools/_persist_popup.ps1` | 持久测试弹窗（标题仿 ccc 真窗口），供「点卡片精准跳转」人眼验证 |
| `win_focus.py` | 窗口枚举 / 终端 pid 解析 / 抢前台（移植自一个经过多轮实战的本地桌面程序，自包含副本；WT 场景另加 `session_ai_title()` / `cb_candidate_windows()`） |
| `tools/_diag_windows.py` | 一次性诊断：CLI 进程 → 祖先链 → 窗口枚举 → resolve 结果 |
| `tools/_focus_test.py` | 真机验证 focus_window（会抢前台，先确认当前没有别的抢前台任务在跑） |
| `run_sticky.cmd` | 手动启动（pythonw，无控制台窗口；**文件保持 ASCII**，cmd.exe 按 GBK 读 .cmd，中文注释会变乱码） |
| `logs/spool/` | 事件中转（运行时生成；app 停止期间可整个删掉重置） |
| `logs/crash.log` | 启动失败的栈 + 被吞异常的留痕（排查「卡片不弹」先看这里；平时为空） |

以 `_` 开头的 `tools/_*.py` / `_*.ps1` 都是开发调试与回归工具，运行时不被依赖。

## 调参

都在 `tools/sticky_app.py` 顶部：`MAX_CARDS`（同屏最多几张，超出丢最旧）、
`POLL_MS`（捞 spool 间隔）、`CARD_WIDTH` / `MARGIN`、两个 accent 色
（完成=绿 `#3ecf8e`、授权=琥珀 `#f5a623`）。

## 已知边界

- **同屏最多 `MAX_CARDS` 张**：长时间离开 + CLI 连续多轮任务时，最旧的卡片会被
  挤掉（托盘图标闪 + tooltip 显示存量，提示回去看）。这是「不自动消失」和
  「不糊满屏幕」之间的取舍；要绝对不丢，把 `MAX_CARDS` 调大。
- **多窗口并行时的精确定位**：WT 里靠会话标题（aiTitle）匹配；aiTitle 为空或
  会话文件被清时退化为候选轮换，多点一次即达，不会跳到无关窗口。
  **ccb / ccc 弹窗**（真顶层窗口）走 hwnd 精确制导，不受此限；
  **cc 交互多开**（标题都是 `Claude Code (pwsh7)`，且 `/rename` 后标题变会话名、
  枚举认不出）是唯一降级场景：单窗直接跳、多窗轮换。
- **备用触发通道（未实现）**：会话 jsonl（`~/.codebuddy-cli/projects/**/*.jsonl`）
  现已用于「读 aiTitle 找窗口」；万一哪天 hook 整体失效，还可以升级成 watcher
  模式。当前 hook 都是用户级实测可用的事件类型，不需要。

## License

MIT，见 [LICENSE](LICENSE)。机制设计与 cc/ccc 兼容性实证详见
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

# English

## cb_sticky — Tray-resident CLI Notifier

A "sticks like glue" watcher for coding CLIs. Normally it is just a **tray icon**;
the moment the CLI **finishes its main task**, **pops a permission dialog**, or
**errors out**, a **persistent card** appears in the **top-right corner** — no
countdown, no auto-dismiss, does not flee when you move the mouse away, and
**can only be closed with ×**. Made for people who keep missing 7-second toast
notifications.

Four launchers are supported: **cb** (CodeBuddy CLI in Windows Terminal),
**ccb** (cb driven by a popup in headless mode), and **cc / ccc**
(Claude Code interactive / popup-driven).

- **Platform**: Windows only (window focusing uses Win32 via ctypes; the tray
  itself is cross-platform, but grabbing the foreground is not)
- **Dependencies**: the hook side is **pure standard library**; the GUI side
  needs PyQt6; psutil is optional
- **Python**: any 3.x for the hook side; 3.9+ recommended for the GUI

## What triggers a card

| Event | Hook | Meaning |
|---|---|---|
| Main task done | `Stop` | The main loop finished a turn. **Subagent completion fires `SubagentStop`, deliberately not handled** — that is exactly the "not just a subtask" distinction |
| API error | `Stop` | Failed turns (429/400/500…) still fire `Stop`, with the error at the start of `last_assistant_message` (e.g. `400 model [x] service info not found (...)`). The hook upgrades 4xx/5xx prefixes to an err card |
| Permission needed | `Notification` | `notification_type == "permission_prompt"`. Other notification types (auth_success / idle_prompt / agent_completed…) are ignored |

## Architecture

```
cb Stop hook ─┐
              ├→ tools/sticky_hook.py → logs/spool/*.json → sticky_app.py (polls every 300ms)
cb Notification hook (permission)┘
cc / ccc (~/.claude/settings.json) and ccb / cb (codebuddy config root) use the same hooks
```

- **The hook does exactly one thing**: classify the event → atomic write
  (`.tmp` + `os.replace`). Returns in tens of milliseconds, exits 0 quietly on
  any failure, never blocks the CLI.
- **The spool directory is the buffer**: events are not lost while the app is
  closed — after a restart they are replayed in time order (bounded by the
  max-cards-per-screen cap). This is also why there is no HTTP POST: no port
  usage, no firewall prompts, and every session writes its own files.
- **The app**: a `QSystemTrayIcon` resident tray icon plus a top-right stack of
  countdown-less cards. A single-instance guard (`QSharedMemory`) keeps autostart
  and double-click from spawning two. The tray icon flashes amber while cards
  are pending.
- **Clicking a card** tries to bring the matching terminal window to the
  foreground (`win_focus.py`; supports Windows Terminal / conhost / IDE
  terminals). On failure only the hint text changes — the card stays.
- **Crash armor**: every Qt slot catches its own exceptions — an uncaught
  exception inside a PyQt6 slot means `qFatal → abort()` with a silent exit.

## Install & use

```bash
# 1. Install the hooks (writes three settings files: codebuddy / claude,
#    auto-backup, idempotent)
py -3 tools/install_hooks.py --dry-run   # preview what would be written
py -3 tools/install_hooks.py

# 2. Keep the tray app alive (pick one)
./run_sticky.cmd                                  # manual start
py -3 tools/install_hooks.py --install-startup    # autostart

# Uninstall
py -3 tools/install_hooks.py --remove
py -3 tools/install_hooks.py --remove-startup
```

cb hot-reloads hooks, so installation usually takes effect immediately; if not,
open a new terminal and run cb again.

The interpreter used in hook commands is picked by a probe chain
(`tools/install_hooks.py: pick_python()`): env var `CB_STICKY_PYTHON` → the
interpreter running the installer → `python` on PATH → the `py` launcher. The
hook itself is standard-library only, so any 3.x works; the GUI needs PyQt6.

## How a click finds "that" CLI window (Windows Terminal case)

cb / cc run inside Windows Terminal over ConPTY: the shell (pwsh) parent exits
early, so the **ancestor chain is always broken** — "ancestor → window" never
works in WT. Four routes, in order of reliability:

0. **HWND captured at hook time** (precision guidance, ccb / ccc popups) — at
   the instant the hook runs the ancestor chain is still alive (the pwsh parent
   of node / claude.exe owns the real window), so the resolved HWND is written
   straight into the spool event; on click the app focuses it first. WT tab
   pages (`CASCADIA_HOSTING_WINDOW_`) are **deliberately suppressed to 0** —
   focusing the whole WT window is not focusing the right tab, so those cases
   fall through to the routes below.
1. **Ancestor chain** (`win_focus.pick_host`) — for non-WT scenarios
   (conhost / IDE terminals).
2. **Session title exact match** (the WT workhorse) — when cb starts it writes
   `aiTitle` into line 3 of the session jsonl (`type: "ai-title"`) and sets the
   same text (with a braille spinner prefix) as the WT tab title via OSC. So:
   event `session_id` → `win_focus.session_ai_title()` reads the jsonl →
   match against window titles. **Every WT tab is its own top-level HWND** (all
   owned by WindowsTerminal.exe), so focusing one HWND activates that tab.
3. **Candidate rotation** — when all else fails, collect all windows whose
   titles look like a CLI (spinner prefix / `CodeBuddy CLI` / `Claude Code` /
   `ccb ·` / `ccc ·`; all four launchers are recognized): a single match jumps
   directly; multiple matches rotate one per click, with the hint text showing
   "k of n (click again to switch)".

cc / ccc session jsonl files do not record titles, so the task name degrades to
the project directory name; ccb session jsonl files sometimes contain
`ai-title` (generated asynchronously, not guaranteed).

## Card styles

| Style | Trigger | Look |
|---|---|---|
| `done` | main task finished | dark background + green accent bar on the left |
| `auth` | permission dialog | **warning yellow**: 66px black-yellow hatch band on the left (feathered right edge), card fades out to the right; yellow title |
| `err` | API error | whole card **bright red on the left, fading right**; titles: 429 → a wry "burning" quip, others → "{code} SUPER KABOOM!" |

All cards: anchored top-right, no countdown, closable only with ×, click the
body to jump to the matching terminal.

## Feeding events manually for debugging (mind the quoting trap)

    # Never put backslash paths in the payload — the hook reads JSON from
    # stdin, and Git Bash / some harnesses fold \\ into \, which makes the
    # JSON silently fail to parse and the hook exits as "not interested"
    # (symptom: empty spool, no cards). Use forward slashes:
    echo '{"hook_event_name":"Stop","cwd":"C:/Users/you","session_id":"<sid>","last_assistant_message":"429 ..."}' |
        python tools/sticky_hook.py --tool cb --dry-run

To capture the real payload a CLI sends: `tools/_dump_hook.py` (dumps stdin
verbatim to `logs/_hook_payloads.log`) — point a temporary
`CODEBUDDY_CONFIG_DIR` at a settings.json containing only that hook, then run a
headless CLI with a bad model name / bad key to trigger a failed turn.

## Tray menu

- **Test: task-done card / Test: waiting-for-permission card** — see the effect
  without actually running the CLI
- **Dismiss all cards**
- **Quit**

## Files

| File | Role |
|---|---|
| `tools/sticky_hook.py` | hook entry (pure stdlib): Stop→done (failed turn→err, code taken from the 4xx/5xx prefix of `last_assistant_message`), Notification(permission)→auth, atomic spool write; captures `hwnd` at write time (chain still alive at hook time; suppressed to 0 for WT) |
| `tools/sticky_app.py` | tray + top-right persistent card main program (PyQt6); done / auth / err styles; click-to-focus prefers the event's hwnd |
| `tools/_dump_hook.py` | debug: dumps the CLI's hook payload verbatim to `logs/_hook_payloads.log` |
| `tools/_probe_ui.py` | UI self-check probe: feeds events bypassing the hook, prints card visibility / geometry (for "card doesn't show up") |
| `tools/install_hooks.py` | install/remove the two hooks (**three settings targets: cb-cli / cb-home / cc-home**, auto-backup, idempotent) + autostart |
| `tools/_popup_test.ps1` + `tools/_hook_driver.py` | popup HWND capture regression: replicates the ccc process chain (sticky_hook → claude.exe → pwsh popup), verifies event hwnd == popup handle |
| `tools/_focus_hwnd_test.py` / `_focus_hwnd_test2.py` | foreground-jump self-check: after focus_window, verify foreground == target hwnd |
| `tools/_tk_popup.py` / `tools/_persist_popup.ps1` | persistent test popups (titles mimic real ccc windows) for eyeballing precise click-to-jump |
| `win_focus.py` | window enumeration / terminal pid resolution / foreground grabbing (self-contained copy ported from a battle-tested local desktop program; adds `session_ai_title()` / `cb_candidate_windows()` for WT) |
| `tools/_diag_windows.py` | one-shot diagnostics: CLI process → ancestor chain → window enumeration → resolve results |
| `tools/_focus_test.py` | live check of focus_window (grabs the foreground; make sure no other foreground task is running first) |
| `run_sticky.cmd` | manual launcher (pythonw, no console window; **keep the file ASCII-only** — cmd.exe reads .cmd as GBK, non-ASCII comments get mangled) |
| `logs/spool/` | event buffer (runtime-generated; safe to delete wholesale while the app is stopped) |
| `logs/crash.log` | startup failure stacks + swallowed-exception traces (check here first for "no cards"; normally empty) |

All `tools/_*.py` / `_*.ps1` files are development/debug/regression tools; the
runtime does not depend on them.

## Tuning

All at the top of `tools/sticky_app.py`: `MAX_CARDS` (max cards on screen,
oldest dropped), `POLL_MS` (spool poll interval), `CARD_WIDTH` / `MARGIN`, and
the two accent colors (done=green `#3ecf8e`, auth=amber `#f5a623`).

## Known limitations

- **At most `MAX_CARDS` cards on screen**: leaving for a long time while the CLI
  runs many turns pushes out the oldest cards (the tray icon flashes and the
  tooltip shows the pending count, prompting you to go look). This is the
  trade-off between "never auto-dismiss" and "don't paper the screen"; raise
  `MAX_CARDS` if you want zero loss.
- **Precise positioning with multiple parallel windows**: WT relies on session
  title (aiTitle) matching; when aiTitle is empty or the session file was
  cleaned it degrades to candidate rotation — one more click gets you there, and
  it never jumps to an unrelated window. **ccb / ccc popups** (real top-level
  windows) use hwnd precision guidance and are exempt; **multiple interactive
  cc windows** (all titled `Claude Code (pwsh7)`, and after `/rename` the title
  becomes the session name which enumeration can't recognize) are the only
  degraded case: single window jumps directly, multiple windows rotate.
- **Backup trigger channel (not implemented)**: session jsonl files
  (`~/.codebuddy-cli/projects/**/*.jsonl`) are already used for "read aiTitle to
  find the window"; if hooks ever break entirely this could be upgraded to a
  watcher. Not needed — the current hooks are verified working event types.

## License

MIT, see [LICENSE](LICENSE). Mechanism design and cc/ccc compatibility evidence:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

