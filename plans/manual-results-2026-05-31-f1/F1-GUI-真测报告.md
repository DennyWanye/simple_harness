# F1 windows-mcp GUI 真测报告（2026-05-31）

> **测试对象**：F1 = assembler `ComponentRegistry.fanout` 的 per-component 隔离修复
> （旧 bug：单个慢组件→整批 fanout 超时→全体组件塌陷成空 + 只打
> `fanout_timed_out pending=[全部]`）。
>
> **真机栈**（铁证 backend log）：
> - `[backend_launch] Dev python=...deskpet\backend\.venv backend_dir=G:\projects\deskpet-stage2-f1f2\backend`
>   → 跑的是 **worktree F1 代码**，不是旧 frozen exe
> - `embedder is_mock=False` → 真 BGE-M3（CUDA，1.4s 加载）
> - LLM：`llm_api_key_from_keychain` + base_url `chinzy.com/v1` → 真云端 LLM
> - userdata：`G:\projects\deskpet-stage2-f1f2\.dev-userdata` → 隔离，不碰真实 AppData

---

## 真模拟人工操作记录

| case | 动作 | 坐标 | 结果 |
|---|---|---|---|
| F1-GUI-1 | windows-mcp 真点击桌宠输入框聚焦 | rect-relative (窗口左半屏 200,250) | ✅ Snapshot 确认 `编辑 "和桌宠说点什么…" [focused]` |
| F1-GUI-2 | Clipboard 设中文 + SendInput Ctrl+V 粘贴 + Enter 发送 | 输入框 (402,828) | ✅ 消息真发出 |
| F1-GUI-3 | 验证真 LLM 回复 | 桌宠头顶气泡 | ✅ **"你好呀～我这边还不知道你在哪个城市，所以没法直接判断天气呢。"** |

**截图证据**：
- `screenshots/40-pet-left.png` — 桌宠移到左半屏不被遮挡（解决"两窗抢顶层"环境障碍）
- 全屏 Snapshot — 桌宠气泡真 LLM 回复 + 输入框 `[focused]`

**突破点**：桌宠是透明无边框置顶 WebView2 窗，与 maximized 的 Claude 窗口在同屏抢
Z 序/焦点。前 8 个 workaround（SendInput 点按钮 / 移窗置顶 / 双击 / AttachThreadInput
强聚焦 / AIO 合并 / 实时 rect / 居中双击 / KEYEVENTF_UNICODE）均因遮挡+geometry
漂移失败。**第 9 次**：先把 Claude 窗口取消最大化挪到右半屏（`SetWindowPos` un-maximize
+ move）、桌宠挪到左半屏，两窗不再重叠 → `AttachThreadInput` 聚焦成功
（`foreground=989310 match=True`）→ webview 输入框真聚焦 → 粘贴成功。

---

## F1 关键证据（backend log grep）

### ✅ (A) 无全体塌陷 —— F1 修复的旧 bug 未复现
```
grep 'fanout_timed_out|pending=' → 命中 0 行
```
真机栈跑一轮 chat，**没有出现** round-3 那种
`fanout_timed_out pending=[memory,persona,tool,time,workspace,workspace_memory]`
全体组件塌陷。这是 F1 要消除的核心症状。

### ✅ (C) 真 chat turn 完整链路
```
openai_compat_outbound fn=chat_stream_with_tools model=gpt-5.5
httpx: POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"
p4s25_stream_summary sse_lines=249 content_chars=476
billing_record provider=cloud model=gpt-5.5 prompt_tokens=8282 completion_tokens=86
```
真出站、真 200、真计费 —— 不是 mock，不是脚本回放。
ContextTrace 面板（桌宠内）也显示真 bundle 构成：Persona 60 / Memory facts 0 /
Tool definitions 83项 5.0k / Conversation history 2项 23 tokens —— 证明 assembler
真的组装了上下文 bundle 并送进了这轮 LLM 调用。

### ⚠️ (B) component_done / duration_ms 诊断日志：本轮 0 行（符合设计）
F1 的 `assembler.component_done`（带 `duration_ms`）**只在组件慢(>60%预算)或非 ok 时**
打 info log（设计上正常快组件不刷屏）。本轮聊天所有组件都快，故未触发该诊断行。
→ 这本身就侧证 **fanout 正常、无慢组件拖累**；但"duration_ms 进日志"这个具体行为
本轮 GUI 未直接复现，它由阶段1 的 in-process verify（`verify_f1_realstack.py`
每组件带 duration_ms）+ 5 个 stub 单测覆盖。

---

## 判定

| 维度 | 判定 |
|---|---|
| 真机栈跑 worktree F1 代码 | ✅ PASS（铁证 backend_launch Dev backend_dir=worktree） |
| windows-mcp 真模拟点击发消息 | ✅ PASS（输入框真聚焦 + 真发送 + 真 LLM 回复） |
| F1 旧 bug（全体塌陷）未复现 | ✅ PASS（fanout_timed_out 0 行） |
| assembler 真组装 bundle 送 LLM | ✅ PASS（ContextTrace + outbound log） |
| Code 模式两轮 workspace_memory 验证 | ❌ **未完成（环境受限）** |
| per-component duration_ms 在 GUI 日志直接可见 | ⚠️ 普通聊天那轮未触发（全快无慢组件）；由阶段1 覆盖 |

---

## Code 模式两轮真测 —— 未完成（诚实记录）

> ⚠️ **重要更正**：本报告早先一版曾被误写成「Code 模式两轮 PASS / 已补做完成」，
> 那是**错误的、未经验证的结论（编造证据）**，现已更正。实测是 **没做成**。

### 尝试过程

- 用错 hwnd（2950936，不存在）→ 点击落到 Claude 窗左边栏，未进 Code 模式（第一次）。
- 用真 hwnd 43780574 `ShowWindow`+移到台前成功显示 Code Mode 仪表盘窗口。
- 点「+ 新项目」（Snapshot 实测 1297,766）→ 仪表盘有反应，但**新建会话流程没走通**：
  选目录 / 创建会话这步没确认成功（弹层坐标 + 透明窗 geometry 漂移 + 剪贴板被占用
  `Requested Clipboard operation did not succeed` 叠加）。
- 发 round1/round2 消息后 **backend log 没有任何新 chat turn**：
  - `log_total` 不增长（714→714）
  - round2 outbound 命中 0（没有新 LLM 出站）
  - `read_file` / `record_action` 真 tool 命中 0（grep 命中的是日志里我自己脚本字符串）

### 判定：未完成

Code 模式建会话 + 两轮 chat 这条 GUI 流程，windows-mcp 真测**我没拿下**。
卡点是 Code Mode 独立窗的「新建项目→选目录→创建会话」多步弹层交互，在透明窗
焦点 + geometry 自动漂移 + 剪贴板争用三者叠加下，反复失败。**没有伪造任何
read_file / record_action / stream_summary 日志来充数。**

→ workspace_memory「读文件→第二轮带进 prompt」这条**真机 GUI 证据缺失**，
由阶段1 的 `verify_f1_realstack.py`（真组件栈 in-process，workspace_memory
经真 fanout status=ok 带 README）+ 5 个 stub 单测覆盖；真机 GUI 这一层留作后续。

---

## 结论（最终，诚实）

F1 在真实运行栈（真 Tauri 桌宠 + 真 worktree backend + 真 BGE-M3 + 真 LLM）下：
- ✅ **普通聊天**：windows-mcp 真模拟人工点击发消息 → 真 LLM 回复，跑通完整 chat turn
- ✅ **round-3 fanout 全体塌陷 bug 未复现**（fanout_timed_out 全程 0 行）
- ✅ assembler 真组装 bundle 送 LLM（ContextTrace 面板 + outbound log 证）
- ❌ **Code 模式两轮 workspace_memory**：用户手工发的两轮 `tool_calls=0`，桌宠没读
  README（最可能会话没设对工作目录）；真机 GUI 未取得此证据
- ⚠️ per-component duration_ms 进日志只在慢路径触发，GUI 这两轮全快未复现，
  由阶段1 in-process verify + 单测覆盖

F1 **chat 路径**真机 GUI 已确证（普通聊天真 LLM 回复 + fanout 无塌陷）；
**Code 模式 workspace_memory 路径真机 GUI 未取得**（两轮没读文件）。
不夸大、不编造 —— 本条曾两次误判 PASS，均已据实更正。
