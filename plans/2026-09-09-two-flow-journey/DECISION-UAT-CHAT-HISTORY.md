# 主对话历史 UAT 缺陷处置（2026-09-09）

worktree：`worktrees/chat-history-ui`（分支 `worktree-chat-history-ui`，基线 main `45e23979`）

## 1. 问题（真人验收原话）

> 「我分不清哪句话是我说的，哪句话是 AI 回答的，刷新的话，有时候只剩我的问题了，
> AI 的回答和 tool 使用记录都不见了」

两件事：

- **P1 历史不完整**：刷新 / 重启后，回合里的助手回答与工具记录看不到。
- **P2 角色分不清**：`你 / 助手 / 工具` 只是一行 12px 的灰色小标签，气泡样式完全相同。

## 2. 根因（用真实 UAT 库证明）

只读证据：`.local-test-evidence/2026-09-09/native-uat-7e64dab0/primary-ui-wmqy3l5j/userdata/`
（`data/state.db`、`data/simple-harness-sdk/execution-v6.sqlite3`、`data/human_memory_v7.db`），
重启日志 `primary-ui-mn9j3u6m/native.log`（第 11 行确认重启复用了同一份 `wmqy3l5j/userdata`）。

用真实库跑真实读模型（`PrimaryReadModel`，真实 `settled_run_reader` = SDK `SqliteContextPort` +
`read_run_terminal_record`）得到的结论：

1. **数据没丢**。11 轮全部 `SETTLED`；`workflow_checkpoints(react.context.v1)` 里 11 个 run 的
   上下文都在；`human_memory_evidence` 里 `assistant_message` 26 条、`tool_result` 15 条、
   `runtime_event` 11 条（每个 run 的 `uuid5("primary-runtime:"+sdk_run_id)` 观测证据齐全，
   `visibility_dependencies` 都是合法 Mapping）。
2. **后端投影也没丢**。逐轮调用 `_messages()` 得到：
   1..9、11 轮均为 `['user','assistant','tool','assistant']`（第 4 轮 14 条），第 10 轮
   `['user','assistant']`。也就是说 `assistant` / `tool` 在接口层是能返回的。
3. **`messages` / `messages_archive` legacy 表是空的（0 行）**，与主对话视图无关——主对话走
   `primary.messages.page`，不走 legacy chat_v2 投影。`companion_projection_history_closed_identity_unready`
   这条启动日志是等签名 bind 的正常态，不是根因（`main.py` 注释已写明）。
4. **真正的缺陷在「分页 + 呈现」这一层**：
   - 后端 `primary_read_model.page()` 按**消息条数**截断（`limit=20`，前端固定要 20）。
     11 轮 × 约 4 条 ≈ 44 条，于是**边界那一轮被劈成两页**。用真库实测：
     第 1 页最前面两条是第 6 轮的 `tool` + `assistant`（没有对应的 `user`），
     第 2 页最后两条是第 6 轮的 `user` + `assistant`（没有 `tool` 和最终回答）。
   - 前端「查看更早消息」调的是 `controller.refresh(nextCursor)`，那是**替换**当前页。
     用户点一下，刚看到的助手回答与工具记录整页被换走。

   两者叠加的实际观感就是：**同一轮里我的问题在，AI 的回答和 tool 记录不见了**。

结论一句话：**assistant / tool 消息是持久的、后端也返回，缺陷在「按条数截断把一轮劈成两页」
＋「展开更早时替换而非累积」，再加上三种角色视觉上没有区分。**

## 3. 改动

### 后端 `backend/deskpet/memory/primary_read_model.py::page()`

- 分页改为**轮次对齐**：只在轮边界收尾。装不下的整轮**整体留给下一页**，游标停在该轮
  最新一条之上（`(enqueue_sequence, 最新 index + 1)`），下一页从这一轮开头读起。
- 只有当**单独一轮就超过 `limit`** 时才截断（否则会出现空页 + 游标原地打转）。
- 页大小仍然 `<= limit`，`<= 50` 硬上限、10 轮扫描窗口、游标语义（`revision` 绑定、
  `primary_cursor_stale`）全部不变，未新增任何 wire 字段。

### 前端 `tauri-app/src/primary/controller.ts`

- 新增 `loadOlder()`：`refresh(cursor, "prepend")`，旧页**拼在已显示历史之前**并按
  `message_ref` 去重；`viewEpoch` 在 prepend 时不自增（避免已展开的详情被重挂载丢掉）。
- 新增有界上限 `PRIMARY_HISTORY_MAX_MESSAGES = 200`：累计到上限就不再给出更早入口，
  并显式提示「本次已展开到历史上限；刷新状态后可从最新一页重新展开。」——展开是累积的，
  但依然有界，符合 UI-CONTRACT「Older pages/details are explicit and bounded」。
- 显式刷新（`refresh()` / `refreshLatest()`）仍然是「替换成最新一页」，语义不变。

### 前端 `tauri-app/src/views/PrimaryChatView.tsx`

- 三种角色样式区分（沿用 `theme/tokens` + `theme/components` 里已有的 `dark` 色板与
  `buttonStyle`，**没有引入任何新依赖**）：
  - `user`：整条右对齐、蓝色半透明底 `rgba(79,147,255,0.16)` + 蓝边、文本右对齐、标签「你」。
  - `assistant`：左对齐、`dark.card` 底 + `dark.cardBorder` 边、标签「助手」。
  - `tool`：占满整行、`dark.inset` 灰底、等宽字体 `tokens.font.mono`，**默认折叠**，
    折叠标题为「工具 · `<name>`」（`toolHeadline()` 从 Host 拼在正文首行的
    `{"call_id":…,"name":…}` 里解析工具名），带 `aria-expanded`，点开可见正文与「读取完整消息」。
  - `artifact` / `reminder` 保持既有语义与灰底。
- 每条消息带 `data-testid="primary-message-<role>"` 与 `data-role`，便于 AX / 测试断言角色。
- 「查看更早消息」改调 `controller.loadOlder()`，并套用统一按钮样式。

未在 `main.py` 注册任何新 service，`backend/context.py::_VALID_SERVICES` 无需改动。

## 4. 测试

- 后端（定向，未整目录跑 `tests/sdk_adapters`）：
  `backend/.venv/bin/python -m pytest tests/memory/test_primary_read_api.py` → **30 passed**，
  其中新增 `test_page_never_splits_one_turn_so_refresh_keeps_answers_and_tools`：
  两个已结算轮（各 user/assistant/tool），`limit=4` 时第 1 页 = 完整的新轮、第 2 页 = 完整的旧轮，
  两页合起来 6 条不重不漏；`limit=2`（单轮就超限）仍然截断出页并给出游标，不会空页死循环。
- 后端回归对照：`tests/memory/test_primary_visibility.py`、`test_primary_short_visibility.py`、
  `test_prospective_notice.py`、`test_primary_runtime_api_integration.py`、
  `test_primary_control_binding.py`。其中
  `test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite` 与
  `test_memory_only_forget_filters_real_history_and_next_outbound_after_reopen[4 参数]`
  **在 main 基线上同样失败**（已在主 checkout 复跑确认），属既有失败，非本次引入。
- 前端：`npx vitest run src/primary src/views/PrimaryChatView.test.tsx
  src/code-panel/InputBar.primary.test.tsx src/components/WorkbenchShell.test.tsx --maxWorkers=1`
  → **124 passed**；新增
  - `controller.test.ts`「展开更早一页时把旧消息拼在前面，而不是替换掉已显示的回合」
  - `PrimaryChatView.test.tsx`「renders user / assistant / tool with distinct roles and a
    collapsible tool record」（右对齐 / 底色不同 / 折叠标题「工具 · read_file」/ 展开后正文可见）
- `npx tsc -b --noEmit` → 通过（无输出）。

## 5. 残余风险

1. **未做真机 UAT**：本轮按约束没有启动原生应用，只用真实 UAT 库跑了后端读模型 + 前端单测。
   前端改了，**需要重建 bundle** 后再做一次真人点击验收（重点看：重启后翻页、工具折叠、
   长轮次 > 20 条时的截断提示）。
2. **单轮超过 `limit` 仍会被截断**：例如实测第 4 轮有 14 条，若前端把 `limit` 降到 10 以下，
   该轮仍会跨页。当前前端固定请求 20，实测 11 轮里只有第 4 轮接近上限，暂不构成问题。
3. **累积上限 200 条**：超过后只能刷新回最新一页再逐页展开，没有"跳到某一轮"的入口。
4. **自动补读会重置展开**：运行中每 2s 的 follow-up `refresh()` 仍是替换语义（契约要求），
   所以在 Run 执行期间展开的旧页会被收回。是否要在有 pending 展开时抑制自动替换，留作 followup。
5. 我用真实库跑的可见性检查用的是**放行的** `history_visibility_checker` 桩；接真实
   `simple_harness_memory` manager 的探针在本机跑不出来（拿到 `human_memory_v7.db.writer.lock`
   后长时间阻塞，已终止）。已另行确认该库里唯一的 suppression 是一条 `scope_kind='memory'`
   的认知记忆 forget，不指向任何 evidence，因此不会命中 `_check_sources` 的按源过滤。
   若后续再出现"只剩用户消息"，优先查 `primary_visibility._check()` 里
   `roots[evidence_id] = None` 这条**静默不可见**分支（它会正好只吃掉 assistant/tool，
   因为只有它们的来源集合里带 `runtime_event` 观测证据）。
