# WI-OH-4 / CC-5 curation nudge — 真机 E2E 验收结果（2026-06-23）

> windows-mcp 真模拟人工：真坐标点击 + 中文剪贴板粘贴输入 + 截图验证 + backend
> 日志判定。源码 backend（`DESKPET_BACKEND_DIR`）+ 点亮 flag 的 config。

## 背景：本次真测推翻原诊断 + 抓出第 2/3 处死链

原诊断（task_455ba81e）认为 curation 死链是「facts_store 在 lifespan 为 None」。
三证（生产日志 + 函数边界 + config 文件）推翻——真因是**出厂 flag 漏配**。修 flag
后真机又抓出**第 2/3 处死链**（单测全绿但生产死）：

1. **死链 A（flag 漏配）**：`config.toml [memory.v2]` 没 `curation_nudge` → 默认
   False → lifespan if 短路。**修**：出厂点亮 + 显式 skip-log。
2. **死链 B（计数器跨回合归零）**：`main.py:_run_chat` 每回合 `build_agent` 重建
   `_AgentLoop`，per-session 轮次计数器原挂 loop 实例 → 每回合归零 → `every_n=2`
   时 count 恒=1 → nudge **永不触发**。单测复用单 loop 实例测不到。**修**：计数器
   移到 `MemoryCurator` 单例（`bump_turn`）。
3. **死链 C（CC-5 漏传）**：构造 curator 漏传 `allow_learnings` → 恒 False →
   learnings 提示词/类别永不启用。**修**：传 `config.memory.v2.auto_learnings`。

## 验收证据（backend 真日志 `logs/backend.log`）

### #1 启动接线（real backend lifespan）
```
2026-06-23 13:30:47 __main__ oh4_curation_nudge_wired every_n=2 auto_learnings=True
```
（修前同一启动只会 `oh4_curation_skipped reason='flag_off'`——新观测性消灭静默死链）

### #2 真机聊天 → nudge 按 every_n=2 周期触发（counter 跨 per-turn loop 重建存活）
真坐标点击 `消息` 面板输入框 + 剪贴板粘贴中文 + Enter，sid=default 稳定：
```
2026-06-23 13:42:00 deskpet.agent.loop oh4_curation_nudge sid=default turn=2 decisions=0 remembered=0
2026-06-23 13:45:01 deskpet.agent.loop oh4_curation_nudge sid=default turn=4 decisions=0 remembered=0
```
turn=2 **且** turn=4 都触发 = 计数器跨 4 次 `_AgentLoop` 重建累加（修死链 B 的直接铁证；
修前每回合归零 → 永不触发）。`decisions=0` 因 turn1/2 的偏好已被 agent 直接
`memory_write` 捕获（`用户最喜欢的编程语言是 Rust...` / `用户养了一只叫小白的猫...`），
curator 作为兜底正确判定「无新增值得记」——属正常 backstop 行为，非缺陷。

### #4 CC-5（auto_learnings）接线
`oh4_curation_nudge_wired ... auto_learnings=True` 证 `allow_learnings` 真传进 curator
→ learnings 提示词激活。curator 真触发（见上）。本轮 decisions=0（偏好已直接捕获），
learning 类别为 LLM 自决产物；接线（此前漏传 = 暗装）已真机点亮。

## 真测动作流水（节选）
| # | 坐标/动作 | 输入 | 期望 | 结果 |
|---|---|---|---|---|
| T1 | click(330,378)+粘贴+Enter | 我最喜欢的编程语言是 Rust，平时用 Neovim 写代码 | turn 完成 count=1 | memory_write 捕获 ✅ |
| T2 | click+粘贴+Enter | 你今天过得开心吗 | count=2 → fire | `oh4_curation_nudge turn=2` ✅ |
| T3 | click+粘贴+Enter | 嗯我也挺好的，谢谢你的陪伴 | count=3 不触发 | 无 fire ✅ |
| T4 | click+粘贴+Enter | 好呀，那我先去忙啦，晚点再聊 | count=4 → fire | `oh4_curation_nudge turn=4` ✅ |

截图：`screenshots/`（pet 真回复「挺开心的呀…」+ 面板状态）。

## 环境
- 实例：源码 backend（`DESKPET_BACKEND_DIR=backend`）+ 点亮 config（AppData 同步加 flag，
  因 Rust spawner 不透传 `DESKPET_CONFIG`）+ 默认端口 8100/5173 + AppData userdata（复用登录）。
- relay（chinzy.com）本轮 HTTP 200 正常。
- 障碍 & workaround：① pet/消息窗 Tauri 多窗 + 初次 Live2D 不渲染 → Win32 EnumWindows
  定位 + ShowWindow/SetWindowPos 把真 `消息` 窗移到可见再真点击（非协议层注入）；
  ② auto-resume 复活用户旧 CATL deepresearch 占线 → 真点「停止」清场；③ 中文经剪贴板
  粘贴（SendKeys 不支持 IME）。
