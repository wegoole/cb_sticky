# cb_sticky —— 机制设计 · 四启动器兼容性实证

> 本文回答两个问题：
> 1. 这套「点击卡片跳回对应终端」的提醒机制，底层靠什么成立；
> 2. 它能不能用于 **cc / ccc（Claude Code）** 与 **ccb（cb 弹窗驱动）**——结论：**能，
>    hook 层原样复用，窗口定位层加了 hwnd 精确制导**。

---

## 一、事件来源：CLI 官方 hooks，不轮询、不注入、不读屏

整个链路的第一块基石是 **CLI 自己的 hook 体系**。cb（CodeBuddy）与 cc（Claude Code）
在一回合结束 / 弹授权框时会以 **stdin 喂 JSON 载荷**的方式调用用户配置的命令：

| 事件 | hook | 载荷关键字段 |
|---|---|---|
| 主任务完成 | `Stop` | `session_id` / `cwd` / `transcript_path` / `last_assistant_message` / `stop_hook_active` |
| 需要授权 | `Notification` | `notification_type: "permission_prompt"` / `message` / `title` |
| API 错误 | `Stop`（失败回合也照常触发） | 错误信息在 `last_assistant_message` **开头**，形如 `429 ...` / `400 model [x] service info not found (...)` |

两个关键实证结论：

- **错误没有独立的 hook**。cb / cc 的 hook 事件类型里没有"错误"这一类；回合失败时
  `Stop` 照常触发，只能从 `last_assistant_message` 的 4xx/5xx 前缀识别
  （`sticky_hook.py: error_of()`）。所以 err 卡片是"借" Stop 的通道实现的。
- `Notification` 的用户级消息类型全集为：`permission_prompt` / `idle_prompt` /
  `auth_success` / `elicitation_dialog` / `agent_needs_input` / `agent_completed`，
  其中**只有 `permission_prompt` 弹授权卡**。

## 二、中转：spool 目录，不用 HTTP

hook 是**同步**执行的，CLI 会等它跑完，所以 hook 端必须几十毫秒返回、任何失败安静退出。
因此 hook 只干一件事：判类型 → **原子落盘**（`.tmp` + `os.replace`）到
`logs/spool/*.json`；GUI 程序每 300ms 轮询一次。

为什么不用 HTTP POST：

- 没有端口占用、没有防火墙弹窗；
- 多开会话各写各的文件，零竞争；
- app 没开时事件不丢——重启后按时间顺序补弹。

## 三、窗口定位：四条路（本项目的真正核心）

「点卡片 → 找到那个终端窗口」在 **Windows Terminal + ConPTY** 场景下是个硬问题，因为：

- cb / cc 跑在 WT 里走 ConPTY，**shell（pwsh）的父进程早退，祖先进程链必然断**——
  "hook 记 pid → 往上找祖先 → 拿窗口"在 WT 里一律失效；
- 但**每个 WT 标签页是独立顶层 HWND**（窗口类 `CASCADIA_HOSTING_WINDOW_`，同属
  `WindowsTerminal.exe`），focus 单个 HWND 即激活对应标签页。

所以定位走四条路，按可靠性排序：

0. **hook 时记录的 HWND**（精确制导）——hook 执行**瞬间**祖先链还活着
   （ccb/ccc 弹窗场景：node/claude.exe 的父进程 pwsh 持有真窗口），此时解析出的
   HWND 直接落进 spool 事件；点击时优先 focus 它。WT 标签页
   （`CASCADIA_HOSTING_WINDOW_`）被主动抑制记 0——focus 整个 WT 窗口不等于
   focus 对应标签页；
1. **祖先进程链**（`win_focus.pick_host`）——非 WT 场景（conhost / IDE 内置终端）靠它；
2. **会话标题精确匹配**（WT 场景主力）——cb 启动时把 `aiTitle` 写进**会话 jsonl 第 3 行**
   （`type: "ai-title"`），同时用 OSC 序列把同一文本（带 braille spinner 前缀
   U+2800–U+28FF）设成 WT 标签标题。于是：
   **事件 `session_id` → jsonl 里的 `aiTitle` → 匹配所有顶层窗口标题**，精确命中对应标签页；
3. **候选轮换**——以上都失败时，收集所有标题像 CLI 的窗口（spinner 前缀 /
   `CodeBuddy CLI` / `Claude Code` / `ccb ·` / `ccc ·`，四种启动器都认）：唯一直接跳；
   多个则每次点击轮换一个，提示语写明「第 k/n 个（再次点击切换）」。

> 这条链成立的两个前提都是**实证**过的，不是猜的：WT ConPTY 进程链断、aiTitle 与标签标题同文本。

## 四、渲染与防护

- PyQt6：`QSystemTrayIcon` 常驻托盘 + 无边框常驻卡片栈，三种风格全部 `paintEvent`
  自绘（渐变 / 斜纹 tile / accent 条）；
- 单实例守卫 `QSharedMemory`，开机自启和手动双击不会开出两个；
- **守护进程 `tools/sticky_watchdog.py`**（纯标准库 + psutil，pythonw 常驻）：
  5s 轮询查 app 进程（name + 脚本路径精确匹配），不在就 `DETACHED_PROCESS`
  拉起；60s 内 >5 次重启退避 60s 防崩溃循环；单实例（`logs/watchdog.pid`）。
  开机自启 vbs 走守护入口（watchdog → app）。用户态进程挡不住强杀本身，
  守护的价值是 **~5s 自动复活**；
- **所有 Qt 槽函数自己兜异常**——PyQt6 槽里没接住的异常会 `qFatal → abort()` 无声闪退。

## 五、依赖分层

| 文件 | 依赖 |
|---|---|
| `tools/sticky_hook.py`（hook 入口） | **纯标准库**，零三方依赖 |
| `win_focus.py`（窗口解析） | ctypes（Win32 原生）；psutil 为可选延迟 import |
| `tools/sticky_app.py`（GUI） | PyQt6 |

---

## 六、cc / ccc / ccb 兼容性

以下结论有两级证据：① 对 `claude-code` 发行包内 `claude.exe`（单文件 SEA，约 259 MB）的
**二进制级实证**——直接在可执行文件里定位 hook 载荷的构造代码；② 另一套同样消费 CLI
hooks 的本地程序的**事件日志实战统计**（典型一天：cb 75 次 / ccb 70 次 / cc 10 次，
其中数次 cwd 为弹窗模式独有目录）。

### 6.1 四种启动器画像

| 启动器 | 驱动方式 | CLI 进程 | 窗口形态 |
|---|---|---|---|
| `cb` | 交互 shell 启动 | node + cb 包 | WT 标签页（标题 = OSC 设的 aiTitle） |
| `ccb` | WinForms 弹窗驱动 headless `-p`，每轮起进程 | node + cb 包 | 弹窗是真顶层窗口，标题 `ccb · 模式 · 模型` |
| `cc` | 交互 shell 启动 | `claude.exe` | WT 标签页（标题 = `Claude Code (pwsh7)` 或 OSC terminalTitle） |
| `ccc` | WinForms 弹窗驱动 headless，每轮起进程 | `claude.exe` | 弹窗是真顶层窗口，标题 `ccc · 模式 · 模型` |

### 6.2 hook 触发：四个全部实证可弹卡

| 启动器 | Stop/Notification 触发 | 证据 |
|---|---|---|
| cb | ✅ | cb_sticky 端到端实测（本文档全部机制就是它验证的） |
| ccb | ✅ | 70 个事件（压测 + real-stop + smoke，session id 齐全）——**headless `-p` 模式照常触发 hook** |
| cc | ✅ | 10 个事件，UUID session + transcript_path 完整 |
| ccc | ✅ | 弹窗模式独有目录的 cwd 事件；`claude -p`（print 模式）SessionStart 探针实证 hook 照常执行（早于任何模型调用，不需要 API 成功）；claude.exe 二进制内有 `surface:null, isInteractive:!1` 的 hook 注册路径（headless/SDK 面也注册 hook） |

### 6.3 载荷对比：cc / ccc 与 cb 同构

**Stop**（claude.exe 二进制内构造代码原样）：

```js
{...common, hook_event_name:"Stop", stop_hook_active:s, last_assistant_message:_e, background_tasks, session_crons}
```

**Notification**：

```js
{...common, hook_event_name:"Notification", message:S, title:w, notification_type:D}
```

| 维度 | cb | cc / ccc | 复用结论 |
|---|---|---|---|
| Stop 载荷 | `last_assistant_message` / `stop_hook_active` | **同样带**（claude.exe 内 22 处 `last_assistant_message`） | ✅ 完成卡 + **错误卡识别逻辑原样可用** |
| Notification 载荷 | `message` / `title` / `notification_type` | 同构 | ✅ 授权卡原样可用 |
| notification_type 全集 | 6 种 | **6 种完全一致** | ✅ 过滤规则不用改 |
| 公共字段 | `cwd` / `session_id` / `transcript_path` | 同（统一构造函数输出） | ✅ |
| SubagentStop | 故意不接 | 载荷多带 `agent_id` / `agent_type`，同样不接 | ✅ |
| hooks 配置格式 | `settings.json` 内 `Stop` / `Notification` 数组 | **格式相同**，cc/ccc 的配置根在 `~/.claude/settings.json` | ✅ 安装器加个 target 即可 |

### 6.4 窗口定位：分场景，唯一需要适配的层

| 启动器 | 点击时祖先链 | 标题可枚举性 | 精确到会话？ |
|---|---|---|---|
| cb（WT） | 断（ConPTY） | aiTitle ↔ WT 标签标题 | ✅ **已工作**（aiTitle 精确匹配） |
| cc（WT） | 断 | 静态 `Claude Code (pwsh7)` 或 terminalTitle 链 | ❌ 标题无会话标识 → 单窗直接跳 / 多窗轮换 |
| ccb（弹窗） | **断**（每轮新起 node，发完即退） | `ccb · 模式 · 模型` 可枚举 | ✅ **hook 时记 HWND，精确制导** |
| ccc（弹窗） | **断**（每轮新起 claude.exe） | `ccc · 模式 · 模型` 可枚举 | ✅ 同 ccb |

cc/ccc 的会话 jsonl **不落标题**（条目类型里没有 title 类），ccb 的会话 jsonl **有时有**
`type:"ai-title"`（第 3 行，异步生成、不保证）。所以「事件 → 任务名」的映射在 cc/ccc 上
断了，在 ccb 上也不可靠。

**适配已实现**（三处改完 + 全链路验证）：

1. `win_focus._TERMINAL_TITLE_PREFIXES` 覆盖四种窗口（`codebuddy cli` / `codebuddy` /
   `claude code` / `ccb` / `ccc`），候选枚举认全部四种启动器；
2. 卡片任务名降级：没有 aiTitle 时显示 cwd 项目名（cc/ccc）；ccb 有 ai-title 时照常读；
3. **hook 端落盘时多写一个 `hwnd` 字段**——hook 执行瞬间祖先链还活着
   （python → node/claude.exe → pwsh 弹窗），`resolve_host_window(pid, cwd,
   cwd_fallback=False)` 拿到的 hwnd 直接进 spool 事件；点击时优先 focus 这个 hwnd，
   窗口没了再退化候选轮换。**WT 标签页（`CASCADIA_HOSTING_WINDOW_`）主动抑制记 0**——
   focus 整个 WT 窗口可能跳错标签页，那种场景走标题匹配 / 轮换。这一招把 ccb/ccc 从
   「轮换」升级成「精确」；cc（WT）拿不到，保持轮换。

实证（`tools/_popup_test.ps1` + `tools/_hook_driver.py` 复刻 ccc 进程链）：弹窗 handle
与事件 hwnd 完全一致、pid 锁定正确；`tools/_focus_hwnd_test2.py` 在前台锁保护下
foreground 从 WT 窗口变为目标弹窗。安装器 `install_hooks.py` 支持三处 settings
（cb-cli / cb-home / cc-home，已有 hook 保留、自动备份、幂等）。

### 6.5 一句话总结

> **cc / ccc / ccb 都能探测到，且完成/授权/错误三种卡片连同识别逻辑原样复用**（载荷同构是
> 二进制 + 日志双重实证，headless 模式也触发 hook）。窗口定位：cb 走 aiTitle 精确匹配，
> ccb/ccc 走 hwnd 精确制导，cc 交互多开是唯一降级场景（单窗直接跳、多窗轮换）。

