# 修复 Spec：桌宠语音对话不同步到消息框

> 状态：**已实施 + 真机 A/B 闭环 PASS ✅**（含 control_ws 快照二次根因修复 — 见文末「实施记录」）
> 作者：Claude（spec-first）· 日期：2026-06-03
> 关联：真机复现报告 `plans/manual-results-2026-06-03-voice-msgpanel/`
> 评审：architect 子代理逐行证实 P0（文字广播能到消息框，语音同构必达）+ P1（消息框 hide/show 同窗口、ws 不断），无致命问题；下列 v2 改动已采纳其 4 点建议。

---

## 1. 问题陈述

用户在**桌宠主窗口**用语音和桌宠聊天，这些对话**不出现在「消息·主线程」消息框**里 —— 既不实时显示，**重新打开消息框也补不出来**。

## 2. 真机证据（已复现，VOICE-MSGPANEL-01）

| 维度 | 证据 |
|---|---|
| 语音真实发生 | 真人对桌宠说「你可以问我做什么」，桌宠出声回复（[07-pet-aftervoice.png](../manual-results-2026-06-03-voice-msgpanel/screenshots/07-pet-aftervoice.png)）|
| 落库 ✅ | SessionDB `default` 新增 id=2151 user「你可以问我做什么」+ id=2152 assistant「当然可以呀…」(22:26) |
| 消息框 ❌ | 重开后仍停在 id=2148，语音两轮不出现（[11-msgpanel-full.png](../manual-results-2026-06-03-voice-msgpanel/screenshots/11-msgpanel-full.png) vs 基线 [01](../manual-results-2026-06-03-voice-msgpanel/screenshots/01-baseline-before.png) 完全一致）|

## 3. 根因（两层，但一处可解）

**A. 语音不广播**：`VoicePipeline._process_utterance` 全程只 `audio_ws.send_json(...)`（[voice_pipeline.py:221](../../backend/pipeline/voice_pipeline.py#L221) / [:279](../../backend/pipeline/voice_pipeline.py#L279)），point-to-point 回发起录音的窗口；从不调用多窗口广播 `_broadcast_default_chat_peers`。对比文字 `chat_v2` 路径会广播（[main.py:4562](../../backend/main.py#L4562)）。

**B. 消息框重开不 reload**：消息框是 **hide/show 同一个 Tauri 窗口**（handle 全程不变），`codePanelWS` **保持连接不断开**，所以「显示消息面板」只是 show，不触发 `ws.onopen → session_messages_load`（[ws.ts:109](../../tauri-app/src/code-panel/ws.ts#L109)）。

> **关键洞察**：消息框 hide 时它的 ws **仍然连着**。因此只要后端把语音也广播出去（修 A），hide 状态的消息框 ws 也能收到 `chat_v2_*` → push 进 store → show 时即已存在。**单修 A 同时解决实时 + 重开两个层面。** B 不必动。
>
> 评审已证实（architect 子代理，逐行代码）：消息框 ws key=`message-panel-main` 在 `_control_connections` 内 → `_broadcast_default_chat_peers` 按 `payload.session_id=="default"` fan-out 跳过 originator → 消息框 push 到 `SID="default"`（[ws.ts:243](../../tauri-app/src/code-panel/ws.ts#L243)/[290](../../tauri-app/src/code-panel/ws.ts#L290)）；hide 不触发 onclose（[commands.rs](../../tauri-app/src-tauri/src/commands.rs) close=hide-without-destroy）→ ws 保持。

### 3.1 关于文字 2149/2150 重开也缺失（独立问题，**本 spec 范围外**）

真机里消息框连 22:10 的文字 2149/2150 都没补出来。评审判定：**这与语音修复无关**，是 `session_messages_load` 重开补拉的**独立时序问题**（最可能这些消息落库时消息框 ws 尚未建立、广播实时 fan-out 无连接即丢，而 show 走的不是重连 onopen 故未触发补拉）。

> ⚠️ 实施后真机验收要**切割清楚**：本修复保证的是「语音经**实时广播**进消息框（hide 时 ws 活着收到）」，**不依赖**重开补拉。若验收时发现历史文字仍缺，那是这个范围外的旧问题，**不得**据此判定语音修复失败。

## 4. 修复方案

让 `VoicePipeline` 在产生 user transcript 和 assistant 回复时，**复用文字同款的 `_broadcast_default_chat_peers`**，fan-out 成 `chat_v2_user_echo` / `chat_v2_final` 给**除发起窗口外**的所有 control 通道。

发起语音的主窗口已通过自己的 `audio_ws` transcript 显示（[App.tsx:894](../../tauri-app/src/App.tsx#L894)），广播以 `self.control_ws` 作为 `originator` 被 skip → **不重复**；消息框窗口的 control 通道收到广播 → **补上**。

## 5. 改动点（2 个生产文件 + 1 个测试文件）

### 5.1 `backend/pipeline/voice_pipeline.py`

**(a) ctor 注入 broadcast**（向后兼容，默认 None）：
```python
def __init__(self, ..., broadcast=None):  # Callable[[WebSocket, dict], Awaitable] | None
    ...
    self._broadcast = broadcast
```

**(b) `_process_utterance`：发完 user transcript 后**（紧接 [:224](../../backend/pipeline/voice_pipeline.py#L224)）：
```python
if self._broadcast and self.control_ws and self.session_id == "default":
    await self._broadcast(self.control_ws, {
        "type": "chat_v2_user_echo",
        "payload": {"session_id": "default", "text": text},
    })
```

**(c) 发完 assistant transcript 后**（紧接 [:282](../../backend/pipeline/voice_pipeline.py#L282)）：
```python
if self._broadcast and self.control_ws and self.session_id == "default":
    await self._broadcast(self.control_ws, {
        "type": "chat_v2_final",
        "payload": {"session_id": "default", "text": response_text},
    })
```
（best-effort：包 try/except，广播失败不影响 TTS。）

### 5.2 `backend/main.py`（`audio_channel`，[:6113](../../backend/main.py#L6113)）
```python
pipeline = VoicePipeline(
    ...,
    broadcast=_broadcast_default_chat_peers,
)
```
> 校验（评审确认）：`_broadcast_default_chat_peers` 定义在 `main.py:2521` 模块作用域，`audio_channel`（同模块）可直接引用，**无需额外 import**。

## 6. 边界 / 风险分析

| 场景 | 行为 | 是否正确 |
|---|---|---|
| 主窗口录音 | audio_ws 显示 + 广播 skip 主窗口 control_ws；消息框 control 收到 | ✅ 不重复、消息框补上 |
| 消息框自己录音 | 对称：消息框 audio echo（[MessagePanelRoot:127](../../tauri-app/src/message-panel/MessagePanelRoot.tsx#L127)）+ 广播 skip 消息框 control；主窗口 control 收到 | ✅ 不重复 |
| 非 default 会话 | `_broadcast_default_chat_peers` 内部 `payload.session_id != "default"` 直接 return（[main.py:2539](../../backend/main.py#L2539)）；且我们也加了 `== "default"` 守卫 | ✅ 双保险 |
| `broadcast=None`（老测试 / legacy ctor） | 跳过广播，行为与现状一致 | ✅ 向后兼容 |
| 广播 peer 断开 | `_broadcast_default_chat_peers` best-effort swallow | ✅ 无副作用 |

## 7. 测试方案（TDD：先写测试）

### 7.1 单测 `backend/tests/test_voice_pipeline_broadcast.py`（新建）
- `test_default_session_broadcasts_user_and_final`：mock broadcast，跑 `_process_utterance`，断言被调 2 次，payload 为 `chat_v2_user_echo{session_id:default,text:<asr>}` 和 `chat_v2_final{session_id:default,text:<resp>}`，且 originator == pipeline.control_ws。
- `test_non_default_session_does_not_broadcast`：session_id="code-x" → broadcast 不被调。
- `test_broadcast_none_is_safe`：broadcast=None → 不抛异常，TTS 仍执行。
- `test_broadcast_failure_does_not_break_tts`：broadcast 抛异常 → utterance 仍完成。
- **`test_broadcast_skips_originator`（评审新增）**：断言传给 broadcast 的 originator **就是** pipeline 自己的 `control_ws` —— 守住 §6「主窗口不重复显示」这条 spec 明确依赖的语义（主窗口靠 audio transcript 显示，必须被广播 skip）。

### 7.2 真机验收（windows-mcp，复跑本 bug）
1. **（主路径）** 消息框开着 → 桌宠语音说一句 → **实时**出现在消息框（截图）。这是本修复的核心断言。
2. 关消息框 → 桌宠语音 → 重开 → 语音**在**（截图）。**判据**：此处之所以"在"，应是 hide 期间 ws 实时收到广播已 push 进 store（非 show 后补拉）。若 1 通过而本项历史文字仍缺，按 §3.1 视为范围外旧问题，**不判语音修复失败**。
3. 对照组：发一条文字 → 仍正常（不回归）。
4. DB：user+assistant 落库（同现状）。

## 8. 验收标准
- [ ] 7.1 单测全绿；现有 `test_voice_pipeline_*.py` 不回归。
- [ ] `python -m pytest backend/tests/test_voice_pipeline*.py -v` 通过。
- [ ] 真机 7.2 三项截图证据：语音实时进消息框 + 重开仍在 + 文字不回归。
- [ ] 更新 `STATUS/status.md`。

## 9. 范围外（单列，本次不做）
- **P2 — `audio_file_path` 标记缺失**：语音落库时 `append_message` 没填 `audio_file_path`（[voice_pipeline.py:416](../../backend/pipeline/voice_pipeline.py#L416)），导致 DB 无法区分语音/文字来源。修复需要音频落盘机制，范围更大，另起 spec。本次仅广播同步。
- **P3 — `session_messages_load` 重开补拉时序**：消息框 hide/show 同窗口、ws 不重连，故 show **不触发** onopen 的历史补拉；落库时若 ws 未建立的消息（如真机 2149/2150）会两头落空（既没实时广播、又没补拉）。本修复用实时广播绕过它，但这条补拉缺口本身仍在，影响所有"消息框未连期间产生的消息"。建议另起 spec（如：消息框 show 时主动重发一次 `session_messages_load`，或 ws 加 visibility 重连）。

## 10. 实施记录（2026-06-04，真机 A/B 闭环 PASS ✅）

按本 spec 实施（TDD 红→绿），实施中真机暴露 spec 未预见的**二次根因**，已修复：

**二次根因（control_ws 快照失效）**：spec §5.1 用 `self._broadcast(self.control_ws, ...)` + 守卫含 `self.control_ws`。但 `VoicePipeline.control_ws` 是 `audio_channel` 建立连接那一刻从 `_control_connections.get(session_id)` 取的**快照**（[main.py:6096](../../backend/main.py#L6096)）。backend supervisor respawn 后 audio_ws 常先于 control_ws 重连 → 快照=None → 守卫挡掉广播 → 语音仍不进消息框。落盘诊断实锤：`chat_v2` 事件 `control_ws_none=true`。

**二次修复（实时取 originator，对齐文字路径）**：
- `voice_pipeline._broadcast_chat_v2` 守卫去掉 `self.control_ws`（只留 `self._broadcast and self.session_id=="default"`），调用仍传 control_ws 但下游忽略。
- `main.py audio_channel` 注入闭包 `_voice_broadcast`，广播时**实时** `_control_connections.get(session_id)` 作 originator（对齐文字路径 [main.py:4567](../../backend/main.py#L4567) 的实时 `_ws`）。
- 单测 `test_broadcast_skips_originator` → `test_broadcasts_regardless_of_control_ws_snapshot`（守护：control_ws=None 仍广播）。

**验收证据**：
- 单测 **16/16 全绿**（含根因守护条）。
- 诊断日志：`control_ws_none=true`（根因）+ `broadcast_none=false`（修复后仍广播）+ fan-out `control_keys=[message-panel-main, code-panel-main, default]` + `originator_in_values=true`（消息框 ws 收到广播 + 主窗口正确 skip）。
- **真机 A/B**：修复前真人语音停在 id2152 不进消息框（[11 截图](../manual-results-2026-06-03-voice-msgpanel/screenshots/11-msgpanel-full.png)）；修复后真人语音「提醒买菜」(id2157/2158) **实时进消息框**（用户截图确认）。
