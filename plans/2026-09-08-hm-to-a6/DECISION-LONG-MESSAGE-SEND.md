# 决策备忘：长消息静默丢弃（Incident G）

日期：2026-09-08
范围：主对话（primary）发送链路 —— 前端 composer / controller，后端 `queue.enqueue` 准入。
关联：`plans/2026-09-08-hm-to-a6/00-PLAN.md` 第 17 轮（HM-TO-A6 native run）。本备忘不修改任何 A6 计划文件。

---

## 一、现象与证据

HM-TO-A6 native run 第 17 轮的用户消息（中文、无换行、以「；」分隔条款，**18 393 UTF-8 字节 / 7 199 字符**）：

- composer 接受了全文（macOS 辅助功能读回 textarea 值为完整长度）；
- 点击「发送」后，后端**没有**新增 `foreground_turn_heads` 行，也没有相应后端日志；
- UI 没有出现任何可读的失败说明，输入框内容保留；
- 同样方式发送的 128–200 字符消息每次都成功。

### 复现（后端，决定性证据）

以该轮原文直接调用 Host 准入入口：

```
handle_human_memory_command({"type": "human_memory_request",
  "operation": "queue.enqueue", "request_id": "d1",
  "request": {"text": <t17 原文>, "delivery_key": "dk-1"}}, ...)
```

修复前返回：

```
{'ok': False, 'error': {'code': 'task_scope_protocol_rejected'}}
```

按长度二分（100 / 1 000 / 2 000 / 3 000 / 4 000 / 5 000 字符全部通过，7 199 字符失败），
异常栈直指：

```
File backend/deskpet/memory/human_memory_service.py:283, in QueueTurnRequest.__post_init__
    identifier(self.text, "text", 16_384)
File backend/deskpet/task_scope/protocol.py:82, in identifier
    raise TaskScopeProtocolError(f"{name}_too_large")
TaskScopeProtocolError: text_too_large
```

结论：消息**确实到达了后端**，在 DTO 构造阶段就被拒绝，因此既没有 `foreground_turns` 落库，也没有任何 enqueue 日志——「后端没有日志」不代表「消息没到后端」。

---

## 二、根因

三个缺陷叠加，才形成「静默丢弃」这一观感：

1. **上限本身不合理，且以字节计。**
   `backend/deskpet/memory/human_memory_service.py:283` 复用了通用标识符上限
   `identifier(self.text, "text", 16_384)`。`backend/deskpet/task_scope/protocol.py:82`
   按 **UTF-8 字节**判定，中文一字 3 字节，16 KiB 只有约 5 400 个汉字。
   一段正常的中文长指令必然越界。turn 的正文是用户散文，不是标识符，不该走标识符上限。

2. **拒绝原因被抹平。**
   `identifier` 抛出的具体原因是 `text_too_large`，但
   `backend/deskpet/task_scope/protocol.py:21` 的 `TaskScopeProtocolError.code`
   是类级常量 `task_scope_protocol_rejected`；
   `backend/deskpet/memory/human_memory_api.py:135` 只投影 `getattr(exc, "code", ...)`，
   于是客户端只拿到一个与长度毫无关系的通用码，无法说明「多长、上限多少」。

3. **前端完全没有长度约束。**
   `tauri-app/src/code-panel/InputBar.tsx` 与
   `tauri-app/src/primary/controller.ts` 的 `submit` 对文本长度不做任何检查，
   composer 接受任意长度并把整条发上线路；返回的通用错误码即使显示出来
   （`InputBar.tsx` 的 `role="alert"` 行，11px 红字），对用户也等同于噪声。

排除的其它假设（均已查证不成立）：
`onChange` 的 slash 解析（`InputBar.tsx:480–520`，长文本不以 `/` 开头，直接走 else 分支）、
`ControlChannel.send`（`tauri-app/src/ws/ControlChannel.ts:166`，无分片无长度判断，
55 KB 帧远低于任何限制）、后端 WebSocket 帧上限（FastAPI/Starlette 的
`ws.receive_text()` 未设 `max_size`，`parse_canonical_json` 只作用于
`PRIVILEGED_CONTROL_KINDS`，不含 `human_memory_request`）、
`PrimaryRequests` 的 15 秒超时（拒绝是同步返回的，未触发）。

---

## 三、修复

### 上限取值：65 536 UTF-8 字节（64 KiB），并保留硬上限

为什么保留上限：整条 turn 会被规范化哈希、作为一条 sanitized evidence 落库并重放
（`build_foreground_turn_evidence`，`human_memory_service.py`），无界输入会把哈希、
存储与重放成本一起放大，fail-closed 的边界应当保留。

为什么是 64 KiB：约 21 800 个汉字，覆盖 A6 第 17 轮这类真实长指令并留出余量（本次 18 393 字节仅占 28%），
同时仍是一个明确、有界、可测的常量。未采用「直接取消上限」；
也未沿用 16 KiB —— 它对中文用户等于 5 400 字，是本次事故的直接成因。

确认 64 KiB 不与既有约束冲突：`analysis_proposal.py:258` 的
`_MAX_QUOTE_BYTES = 16_384` 约束的是**单条 exact_quote 片段**（`derive_span` 里比较的是
`end - start`），不是 `/text` 源文本长度，源文本更长不影响引用路径；
provider 侧本就在 `provider_invocations.py:211` 处按 6 000 字符截断；
主对话读模型按 1 024 字符分页（`primary_read_model.py:567`），长 turn 可正常翻页读取。

### 后端

- `backend/deskpet/memory/human_memory_service.py`
  新增具名常量 `FOREGROUND_TURN_TEXT_MAX_BYTES = 65_536` 与稳定错误码常量
  `FOREGROUND_TURN_TEXT_TOO_LARGE = "human_memory_turn_text_too_large"`。
- `QueueTurnRequest.__post_init__` 先做超长判定并抛
  `HumanMemoryHostServiceError(FOREGROUND_TURN_TEXT_TOO_LARGE)`，再交给 `identifier`
  做「非空 / 无 NUL」的结构检查。顺序很关键：先判长度，才能避免被
  `TaskScopeProtocolError` 的类级码吞掉具体原因。
- 未改动 `protocol.py`：把 `identifier` 的错误码改成按 `name` 派生会影响整个
  TaskScope 协议的公共错误投影，超出本次范围。

### 前端

- 新增 `tauri-app/src/primary/turnText.ts`：
  `PRIMARY_TURN_TEXT_MAX_BYTES`（与后端常量互为镜像，注释里双向标注「同改」）、
  `turnTextByteLength`（按 UTF-8 字节，不是 UTF-16 长度）、
  `turnTextRejection`（超限时返回中文说明，含当前字节数、约合字数与上限；不超限返回 `""`）。
- `tauri-app/src/primary/controller.ts` `submit`：上线路之前先拒绝超长草稿，抛
  `PrimaryRequestError(中文说明)`；`catch` 中把后端稳定码
  `human_memory_turn_text_too_large` 也翻译成同一句话，防止两端上限漂移时又退回裸码。
- `tauri-app/src/code-panel/InputBar.tsx`：primary 模式下实时计算
  `oversizeDraft`，与 `attachmentError` 合并为 `composerError`
  （`data-testid="composer-error"`）。用户在**还在写/粘贴时**就能看到超限提示，
  点发送时同样阻断并保留草稿——不截断、不静默。
- `tauri-app/src/primary/UI-CONTRACT.md` 记录该边界为前端契约。

一致的产品口径：**要么接受，要么给出带具体上限与当前长度的可读错误；静默丢弃不可接受。**

---

## 四、测试

新增（后端）`backend/tests/memory/test_primary_turn_admission.py`：

- `test_long_chinese_turn_is_admitted` —— 构造 18 393 字节中文（与事故消息同尺寸），
  断言 `ok`，且 `foreground_turns.turn_json` 里的 `payload.text` 与原文逐字相等；
- `test_turn_above_the_bound_is_rejected_with_a_stable_code` —— 超限一点点时，
  payload 恰为 `{"ok": False, "error": {"code": "human_memory_turn_text_too_large"}}`，
  且 `foreground_turns` / `human_memory_evidence` 均为 0 行（拒绝不留半条记录）；
- `test_turn_exactly_at_the_bound_is_admitted` —— 恰好 65 536 字节通过（边界闭区间）。

新增（前端）：

- `tauri-app/src/primary/turnText.test.ts`（3 例）—— 字节 vs 码元、边界与边界+1、
  提示语包含「未发送」「草稿已保留」；
- `tauri-app/src/primary/controller.test.ts` 新增 2 例 —— 超过 18 393 字节的长 turn
  确实以完整原文出现在 `queue.enqueue` 线路帧上并被 ACK；超限时**零**线路帧、
  错误信息含上限数字、且不留下 pendingDelivery（随后一条短消息能正常发出）；
  后端稳定码被翻译成中文句子；
- `tauri-app/src/code-panel/InputBar.primary.test.tsx` 新增 2 例 —— 界内长中文草稿正常
  提交并清空；超限时按钮点击不调用 `submit`、不触碰 `controlWS`、草稿保留、
  提示在按发送之前就已可见。

执行结果：

- `pytest backend/tests/memory/test_primary_turn_admission.py` → **8 passed**
- `pytest backend/tests/task_scope backend/tests/memory/test_primary_turn_admission.py backend/tests/memory/test_primary_control_binding.py backend/tests/execution/test_recovery_fence.py` → **89 passed**
- `pytest backend/tests/memory/test_primary_read_api.py test_primary_visibility.py test_primary_short_visibility.py test_current_input_source.py test_trusted_disclosure.py` → **67 passed / 1 failed**。
  唯一失败 `test_primary_visibility.py::test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite`
  **是既有失败**：把 `human_memory_service.py` 临时回退到 HEAD 后单独跑，同样在
  `test_primary_visibility.py:387` 失败（与遗忘/抑制路径有关，与长度无关），随后已把修复复原。
- `vitest run src/primary src/code-panel` → **26 files / 270 passed**
- `vitest run src/primary src/views/PrimaryChatView.test.tsx src/code-panel/InputBar.primary.test.tsx src/code-panel/InputBar.chat.test.tsx src/code-panel/__tests__/InputBar.slash.test.tsx src/ws/ControlChannel.test.ts --maxWorkers=1`（UI-CONTRACT 指定的聚合命令）→ **15 files / 141 passed**
- `tsc -b --noEmit` → 无输出（通过）

以事故原文（`t17.txt`，18 393 字节）直接跑 Host 准入入口，修复后返回
`ok: True`，`turn_ref` / `content_sha256` 齐备，`foreground_turns` 落一行。

---

## 五、自审

- **为什么当时「看不到错误」？** 代码路径上 `InputBar.tsx` 的 `role="alert"` 行确实会渲染
  `task_scope_protocol_rejected` 这个字符串。观察记录为「无错误文本」，可能是 11px 红字未被注意，
  也可能是自动化只读了 textarea 与后端。**无论如何**，一个与长度无关的英文码不构成
  「清晰的错误说明」，本次按「静默丢弃」同等对待并修复：提示改为中文、含数字、且在按发送之前出现。
- **上限是否又会成为下一次事故？** 会在两处同时暴露：常量互相注释「同改」，
  前端 `PRIMARY_TURN_TEXT_MAX_BYTES` 与后端 `FOREGROUND_TURN_TEXT_MAX_BYTES`；
  即使漂移，controller 也会把后端稳定码翻成同一句人话，不会退回裸码。
- **拒绝是否留下残留？** 测试断言超限时 `foreground_turns` 与 `human_memory_evidence` 都是 0 行；
  DTO 在构造期就失败，不进事务。
- **是否影响幂等/重放？** 未触碰 `delivery_key`、`turn_hash`、`canonical_hash` 及既有幂等冲突判定；
  相邻的 `identifier(self.goal, "goal", 16_384)`（同文件）刻意未动——那是 TaskScope 的 goal 字段，
  不在本次事故范围内。
- **未改动他人正在编辑的文件**：`sdk_adapters/context_route.py`、`task_scope_mutation.py`、
  `provider.py`、`primary_context.py`、`current_tool_pages.py` 及 recall 路径均未触碰。
- **未做真机 E2E**：按任务约束不启动/构建桌面应用（18120 端口有实例）。
  真机复测建议：在 primary composer 粘贴 `t17.txt` 原文并发送，应看到新的
  `foreground_turn_heads` 行；再粘贴一段 > 64 KiB 的文本，应在输入时即看到中文超限提示且不产生线路帧。

---

## 六、变更清单

- `backend/deskpet/memory/human_memory_service.py` —— 常量 + `QueueTurnRequest.__post_init__`
- `backend/tests/memory/test_primary_turn_admission.py` —— 3 个新用例
- `tauri-app/src/primary/turnText.ts` —— 新增
- `tauri-app/src/primary/turnText.test.ts` —— 新增
- `tauri-app/src/primary/controller.ts` —— `submit` 前置拒绝 + 稳定码翻译
- `tauri-app/src/primary/controller.test.ts` —— 2 个新用例
- `tauri-app/src/code-panel/InputBar.tsx` —— 实时超限提示 + 发送阻断
- `tauri-app/src/code-panel/InputBar.primary.test.tsx` —— 2 个新用例
- `tauri-app/src/primary/UI-CONTRACT.md` —— 记录该边界
