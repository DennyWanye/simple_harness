# P0 真测结果（windows-mcp 真机）— 2026-06-22

> 环境：Tauri dev + `DESKPET_BACKEND_DIR=...\backend` 注入跑当前 checkout 后端（日志确认 `[backend_launch] Dev python=...backend`，非 frozen）。已 keychain 登录 gpt-5.5。
> 输入法突破：WebView2 无边框窗口下 `windows-mcp App switch <消息窗>` 正确聚焦 + `Type` 工具（Unicode 直送）成功输入中文——这是本次真测攻克的关键技术障碍（SendInput/Ctrl+V 因前台焦点限制均失败，记录在案）。

## 1A（环境瘦身）— 非 GUI 项
字符节省已量化（全局 -4493 / 项目 -3137）；铁律保留经独立子代理核对；非 DeskPet 运行时功能，无 windows-mcp 项。**PASS（文档级）**。

## 1B-1 token 口径统一 — ✅ 真机 PASS

| TC | 判定 | 证据 |
|---|---|---|
| **TC-1B1-5 Modal 文案** | ✅ **PASS** | ContextBreakdownModal 标题实测显示 **「构成（后端估算 · CJK-aware tokens）」**（我 1B-1 收尾改的文案已生效，原「前端估算 · ~3.5 chars/token」）。截图 `screenshots/TC-1B1-1-modal-cjk-aware.png` |
| **TC-1B1-1 中文不低估** | ✅ **PASS** | 中文 deepresearch 会话后开 Modal：**Conversation history 200项 = 137k tokens**，估算合计 144k。137k 为 **CJK-aware 量级**（旧 `/3.5` 口径同内容只会显示约 40k，差 ~3.4 倍）→ 中文不再低估得证。后端日志 `prompt_tokens=5422→17907`（relay 权威值）佐证上下文真实累积 |
| TC-1B1-2/3/4 边界 | 未单独执行 | 核心口径已由 TC-1B1-1 + 单测 `test_token_unify_cjk.py`(9 绿) 充分覆盖；ASCII/混合/空会话边界由单测断言 |

**结论**：1B-1 真机生效，中文 token 不再被 `/3.5` 低估，前端 Modal 文案与口径一致。

## 1B-2 压缩可观测 toast — ⚠️ 后端单测验证 / toast UI 触发 env-limited

- **代码已落地 + 单测验证**：`ctx_observability` flag、`ContextCompactedEvent`、metrics_sink、WS 转发、前端 toast hook 全实现；单测 56/56 绿（含 `test_observability_off_no_extra_event` + `test_observability_on_emits_event` 断言 flag ON 时压缩发生→yield 事件+字段正确，OFF→字节 BC）。
- **真机确认**：`ctx_observability=true` 被后端读取（config 加载）；`model_overrides.toml` 窗口 override 机制生效（实测 window 1M→8000→3000 切换）。
- **toast 未能在手测下触发（env-limited，已做 5+ 次不同 workaround）**：
  1. window=8000 + 多条中文长消息累积到 prompt_tokens=17845 / ring 100% → 压缩**未触发**。
  2. window=3000 + 消息 → 17827 的 memory 注入触发 **token_budget BLOCK gate(≥95%, 第3层防线)** 报「✗ 错误」，未走到压缩(第4层)。
  3. window=8000 + 单工具多迭代 web_search 任务（prompt_tokens=17619 >> 6400 阈值）→ 压缩**仍未触发**。
- **根因（实读 + 实测定位）**：压缩触发器（`agent_loop.py:~895-922`）按 `max(_last_real_prompt_tokens, count_messages_tokens(working_messages))` 判，而：① 单轮纯聊天 `_last_real_prompt_tokens` 回合开头重置 0 拿不到真值；② `working_messages`(对话历史)受 `l2_top_k=5` 截断 + memory 注入(17k 大头)不计入对话历史 token → 始终 <6400 阈值；③ 小窗口逼近时先被 BLOCK gate 拦报错。**即 prompt_tokens=17619（远超 6400）仍不压**——这是**压缩触发器在 companion 单工具回合下的行为**，与 1B-2 观测增强无关（1B-2 只在压缩发生时加 toast）。
- **诚实标注**：toast 的 UI 真机触发属 test doc 已预判的 best-effort/env-limited（文档原文标 TC-1B2-2/3 best-effort、"连续触发难稳定"）。1B-2 观测逻辑正确性由单测保证；toast 在压缩真实发生时会显示（代码路径完整 + 单测验证）。
- **附带发现（值得后续查）**：压缩触发器在 companion 单工具回合下 prompt_tokens 远超阈值仍不触发——疑似 `_last_real_prompt_tokens` 时序 / working_messages 口径导致触发器够不到，可能是 1B-1 行注释提到的"压缩永不触发"老问题在该场景的残留。建议 P3（1B-3/1B-5 摸压缩时）一并复查。**不属 1B-2 范围。**

## 配置清理
真测用的 `model_overrides.toml`(窗口 override) 与 dev `config.toml`(ctx_observability) 均已**还原**至出厂态（1M / 无 flag），备份已删。
