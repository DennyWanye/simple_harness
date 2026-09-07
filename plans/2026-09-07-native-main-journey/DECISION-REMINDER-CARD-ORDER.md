# 决策备忘：S5c 时间提醒卡片相对助手正文的呈现顺序与口径

> **独立子代理裁决，主代理复核后执行。** 2026-09-07，Host 仓 `simple_harness` main `f74fede4`。只读代码与记录，未改任何源码，未运行原生应用。

## 0. 结论（一句话）

**采用并正式固化现状口径 A：提醒卡片归属于"完成 ACK 的那一轮"，固定排在该轮助手正文之后、下一轮用户消息之前；同轮多条按 `notice_id` 稳定排序；冷重启由同一读模型重建，顺序不变。** 不改运行时代码，只补两个顺序控制测试与一处记录更正；不需要原生复验。

## 1. 现状（含证据）

### 1.1 卡片不是独立 notice 事件，而是历史读模型里的一条 `reminder` 消息

- 前端是"只替换、不本地拼装"的读模型：`primary.messages.page` 返回什么顺序就渲染什么顺序。
  - `tauri-app/src/primary/controller.ts:191` — `this.update({ ..., messages, ... })` 整页替换；无任何客户端排序/插入。
  - `tauri-app/src/primary/controller.ts:172-175` — 收到 `tool_result` / `chat_v2_final` / `permission_response_applied` 后 100ms 触发重读，卡片随重读出现。
  - `tauri-app/src/primary/controller.ts:23-31, 285-286` — `role: "reminder"` 必须带 64 位十六进制 `notice_id`，否则整页拒收（"提醒来源格式无效"）。
  - `tauri-app/src/views/PrimaryChatView.tsx:88` — `snapshot.messages.map(...)` 按页序渲染；`:99-100` 与其他角色同一卡片样式，仅标签为"提醒"。
- 后端由 `ProspectiveNoticeReader` 在每一轮的消息末尾追加：
  - `backend/deskpet/memory/primary_read_model.py:510-518` — `_messages_with_notices` = 该轮 SDK 转录消息 `+ prospective_notice_reader.read(turn=...)`。
  - `backend/deskpet/memory/prospective_notice.py:63-66` — 只取 **该轮 host_run 绑定的 sdk_run** 中 `phase='acknowledged'` 且带 `proof.notice_contract` 的 occurrence；即卡片归属于"模型在哪一轮调用了 `prospective_ack`"，不是"提醒何时到期"。
  - `backend/deskpet/memory/prospective_occurrence.py:331-335` — ACK 时写入 `notice_contract="host.prospective.notice/v1"`，是卡片的唯一来源标记（旧 ACK 无标记 → 无卡片，`test_legacy_ack_does_not_get_a_notice_or_new_receipt`）。

### 1.2 位置：固定在该轮正文之后

- 轮内顺序由整数索引决定，页面按 `(enqueue_sequence, index)` 排序：
  - `primary_read_model.py:366-377` — 用户消息索引 `0`；`:500-509` — SDK 转录（tool / assistant / artifact）索引从 `1` 起递增。
  - `prospective_notice.py:179-183` — 提醒索引 `2**62 + int(notice_id[:15], 16)`，`sorted(result)` 后返回；必然大于任何转录索引 → **排在该轮所有正文之后**。
  - `primary_read_model.py:586-596` — 分页按该 key 逆序切片再 `reversed` 输出，跨页游标同样以 `(s, i)` 表示，卡片位置可稳定翻页。
- 因此可见顺序为：`你(问题) → 工具(prospective_ack 回执 JSON, F02) → 助手(正文) → 提醒(卡片) → 你(下一问题)`。
- **r22 与 r4 两条记录并不矛盾**：r22/r23 说"原提醒在新问题之前"、r4 说"卡片在本轮回答之后"，描述的是同一位置（前一轮末尾）。`NATIVE-R4-R5-REMINDER.md:16` 的"呈现顺序与旧记录不同"是误读，应更正。

### 1.3 时间上：卡片可能先于正文出现，空间上仍在正文之后

- 未 SETTLED 的轮只返回用户消息（`primary_read_model.py:378-381`），但 `prospective_notice.py:103-116` 允许 terminal 尚不存在时（`turn_state != SETTLED`）投影卡片。`test_actual_ack_answer_only_has_stable_notice_and_public_detail`（`backend/tests/memory/test_prospective_notice.py:144-158`，`pending_probe`）证明：ACK 落库后、终答前的页面就是 `[你, 提醒]`；终答后变为 `[你, 工具, 助手, 提醒]`。
- 这不是"插到正文前"，而是正文尚未进入历史；口径上应表述为"卡片位置锚定在该轮末尾，正文补齐后仍在其后"。

### 1.4 冷重启恢复

- 无内存态：卡片每次都从 `prospective_occurrences` + 公开 inbox 现状重新推导（`prospective_notice.py:162-178`：非 `matched`/已 suppressed/`content_hash` 变化即静默退场；`test_late_forget_removes_notice_from_page_and_exact_detail`、`test_legal_public_reschedule_retires_notice_without_breaking_history`）。
- `message_ref` 由 `(primary, turn_id, index, hash(role,text,source))` 生成（`primary_read_model.py:549-567`），索引取自 `notice_id` 哈希而非同轮序号 → 某条卡片退场不会改变其他卡片的 `message_ref`。r5/r23 冷重启"原位保留、无重复"即此机制的结果。

### 1.5 模型侧口径

- 模型看到的是 Host 写入的 system 消息 `pending_prospective_occurrences`（`backend/deskpet/sdk_adapters/context_authority.py:896-925`，含 `action`、`occurred_at`、`occurrence_key`、`overdue`），并通过 `prospective_ack` 工具（`backend/deskpet/sdk_adapters/prospective_ack.py:16-32`，`permission_category='prospective_ack'`，auto 模式零提示）确认。
- 卡片文本是 `action_text`（"提醒用户检查银杏测试记录"），与模型正文解耦：测试断言正文可以只答 "47"，卡片仍独立存在；r4 中模型另外在正文里补了一句"刚才那条一次性提醒已确认"，属模型自由发挥，不影响卡片。

## 2. 备选口径比较

| 维度 | **A 固定在该轮正文之后（现状）** | B 固定在该轮正文之前（用户问题与正文之间） | C 独立提醒区域，按到期时间排序 | D 按到期时间插到历史时间线（不归属轮次） |
|---|---|---|---|---|
| 用户可理解性 | 好："我问 → 它答 → 顺带告诉我有条提醒到了"，符合"回执随回答"的阅读习惯；卡片紧邻正文中可能出现的"已确认"措辞 | 中：卡片出现在自己刚发的问题下面、回答上面，像是"打断"；且运行中正文尚未出现时与 A 无区别，正文到达后反而把卡片顶上去，视觉跳动 | 中：需要新面板/入口，用户要学会看两处；历史里没有卡片会削弱"它到期时确实告诉我了"的感知 | 差：提醒在 17:36 到期但用户 17:40 才提问，卡片会插在两条历史之间"凭空出现"，没有触发它的轮次 |
| 与 09-07 决定一致性 | auto 零提示：无新交互；遗忘只针对记忆：卡片是记忆派生物，晚到遗忘后卡片退场而原始对话保留（已有测试），完全符合 CLAUDE.md 决定 2 的"记忆派生内容"解释 | 同 A | 独立区域必然引入"已读/清除"交互或无限累积，前者与"不打扰"的 auto 取向相悖，后者不可用 | 同 A，但需要新的时间来源 |
| 多条同轮到期稳定性 | 确定性：按 `notice_id` 哈希排序，任一条退场不影响其余 `message_ref`；缺点是顺序对用户无语义（不是到期先后） | 同 A 的稳定性，但需要给正文索引整体预留区间 | 可按 `occurred_at` 排序，语义最好，但排序键来自公开 inbox，退场/改期会重排 | 同 C |
| 重启恢复可审计性 | 最好：卡片 = 该轮 host_run ↔ sdk_run ↔ ACK 记录 ↔ terminal identity 的闭环投影（`prospective_notice.py:90-148`），Dirac 独审已按此链 ACCEPT | 同 A（链不变，只改索引） | 需要新的读操作与独立的来源检查/可见性批次，审计链要另建 | 需要把到期时间与轮次序列混排，游标语义复杂 |
| 实现规模 | **0 行运行时改动**；补 1 个 pytest + 1 个 vitest + 记录更正 | 改 `primary_read_model.py` 索引分配（用户 0 / 提醒区间 / 转录区间）+ `prospective_notice.py:179` + detail 解码边界 + 全部相关测试 + 原生复验（可见变化） | 新后端操作（`primary.reminders.page` 或等价）+ 新 UI 面板 + 来源可见性接线 + 冷启动恢复 + 测试 + 原生复验 | 与 C 相当，另需时间线合并逻辑 |

## 3. 唯一推荐：A（固化现状）

### 3.1 正式口径（写入产品/验收措辞）

1. 提醒卡片是 **Host 投影的独立消息**（`role=reminder`），不是模型正文的一部分；模型正文是否提及提醒不作要求、不作断言。
2. 卡片 **归属于模型完成 `prospective_ack` 的那一轮**，固定位于该轮助手正文之后、下一轮用户消息之前；运行中正文尚未落库时卡片可先出现，正文补齐后仍在其后。
3. 同轮多条提醒按 `notice_id` 稳定排序，不按到期时间；到期时间已在模型上下文与 ACK 回执中，可审计但不改变呈现顺序。
4. 冷重启后不重投递、不追加到末尾，仍在原轮末尾（r5/r23 已验）；提醒来源记忆被遗忘、改期或内容修订后卡片退场，原始对话轮保留。

### 3.2 实施要点（主代理执行）

| 项 | 文件 | 内容 |
|---|---|---|
| 后端顺序控制（pytest） | `backend/tests/memory/test_prospective_notice.py` | 在现有 `world` fixture 上新增 1 个用例：断言页面 items 角色序列为 `["user", "tool", "assistant", "reminder"]`（或至少 `assistant` 索引 < `reminder` 索引，且 `reminder` 是该轮最后一项）；再断言冷重开（`env.w.manager.close(); env.w.open()`）后 items 顺序与 `message_ref` 逐项相等。若 fixture 易扩展，再加一个"同 Run 两次 ACK"变体，断言两张卡片顺序在两次读取间一致且 `message_ref` 不变（当前无任何顺序断言，grep 证实） |
| 前端顺序控制（vitest） | `tauri-app/src/views/PrimaryChatView.test.tsx` | 在 `:178` 用例旁新增：fixture 给 `[user, assistant, reminder, user]`，断言 DOM 中标签顺序为 `你/助手/提醒/你`，锁定"前端不重排" |
| 记录更正 | `plans/2026-09-07-native-main-journey/NATIVE-R4-R5-REMINDER.md:16` | 改为："提醒卡片位于本轮助手回答之后、下一问题之前；与 r22/r23 记录（"原提醒在新问题之前"）为同一位置，口径见 DECISION-REMINDER-CARD-ORDER.md" |
| 汇总回写 | `plans/2026-09-06-typed-use-primary/REMAINING.md` 09-07 追加段 | 一句："提醒卡片顺序口径已裁决固化（A），无运行时改动" |
| 原生复验 | 不需要 | 无运行时行为变化；下次原生提醒旅程顺带截图核对即可 |

不触碰：`prospective_notice.py:179` 的索引公式、`primary_read_model.py` 的分页 key、`REMINDER_CAPABILITY` 提示词、`_pending_occurrence_message`。

### 3.3 不推荐其它的一句话理由

- **B（正文之前）**：卡片记录的是"本轮内完成的 ACK"，放在正文前会颠倒因果，且正文落库时产生视觉跳动，还要重排索引区间并原生复验，收益为负。
- **C（独立区域按到期排序）**：必须引入已读/清除交互或无限累积，与 auto"不打扰"取向冲突，并要重建一套来源可见性与审计链，规模最大而 r4/r5 已证明历史内卡片可用。
- **D（按到期时间插时间线）**：会让提醒出现在没有触发轮次的位置，用户无法理解"谁把它放这儿的"，审计链也断。

## 4. 备查：留待后续、不在本裁决内

- F02（工具回执原始 JSON 渲染）：`prospective_ack` 的回执卡片仍以"工具"角色出现在正文前，折叠后整体阅读会更接近"问 → 答 → 提醒"。
- 同轮多条提醒若未来需要按到期先后展示，只改 `prospective_notice.py:179` 的索引构造（例如 `2**62 + int(occurred_at*1000) << 20 | hash 位`），需同步 detail 解码与一个 pytest；届时再评估，不并入本次。
