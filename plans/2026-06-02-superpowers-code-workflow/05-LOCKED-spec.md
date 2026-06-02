# 锁定版实现 Spec — 过夜并行执行（superpowers 流程）

> **状态**: **v3 LOCKED — GO**（opus-4.8 子代理评估迭代 ×3 完成：R1 修 4 CRITICAL+多项、
>   R2 修 SW-1 假绿高危+B2/B3 预判+7 项细节、R3 最终门裁决 GO 无阻塞）。可发车并行实现。
> **作用**: 单一事实来源。所有实现子代理 + 完成度评估子代理以本文件为准。
> **铁律**: **绝对不可删减功能项**。遇阻按最佳实践解决，不得降级/跳过功能。
>          所有新行为 flag 默认 OFF（出厂字节级不变）。每项闭环 = 代码 + 单测绿
>          + tsc/lint 绿（涉前端）+ commit。真机 E2E 由后续 windows-mcp 阶段统一做。
> **基线**: master @ `bb06edc`（实际 HEAD；含 superpowers Layer 1A+①②③ 已 push + 一个
>   gitignore chore commit）。⚠️ **FEAT-A1 的 `backend/main.py` 接线当前是未 commit 的
>   工作区改动**（`git status` 显示 `M backend/main.py`）——子代理**不得** checkout 干净分支
>   或 reset，否则丢失 A1 接线。实现者在现有工作区基础上继续。
> **范围（用户锁定）**: A1-A5 + C1-C3 + B1/B2/B3，共 11 个功能项，一个不能少。

---

## 0. 全局约束（所有子代理必读）

1. **不删功能**：每个 FEAT 的"完成定义"必须全部满足；做不动就按最佳实践攻克，不跳过。
2. **flag 默认 OFF**：新功能的 `features.*` / verifier flag 出厂默认关；dev userdata
   （gitignored）开来验证。出厂行为字节级不变。
3. **闭环验收**：单测必须真绿（`pytest -q` 通过）；前端改动 `npx tsc --noEmit` 必须 0 错误。
4. **小步 commit**：每个 FEAT 独立 commit，message 注明 FEAT 编号 + 验收证据。
5. **不碰**：`.env`/secrets/`backend/userdata/*`(gitignored)/`rm -rf`/`push --force`。
6. **并行隔离**：见 §3 文件归属表——同一文件只允许一个 agent 写，防冲突。
7. **真机 E2E 不在本阶段**：实现阶段只做代码+单测+tsc；真机留 windows-mcp 阶段。

### 0.8 通用命令 / 路径（所有子代理必用）
- **backend venv**：`backend/.venv/Scripts/python.exe`（Windows，是 `Scripts` 不是 `bin`）。
- **跑后端测试**：`backend/.venv/Scripts/python.exe -m pytest backend/tests/test_xxx.py -v`。
- **py_compile**：`backend/.venv/Scripts/python.exe -m py_compile backend/main.py`。
- **前端 tsc**：`cd tauri-app && npx tsc --noEmit`（0 错误才算绿）。
- **前端 vitest**：`cd tauri-app && npx vitest run <path>`（config `tauri-app/vitest.config.ts`；
  范例参考 `tauri-app/src/code-panel/AutoResumeBanner.test.tsx` / `SessionGridView.test.tsx`——
  **无 `InputBar.slash.test.tsx`**，别找它）。
- **pytest-repeat 经核实未安装** → B1 连跑 20 次直接用 PowerShell `for($i=0;$i -lt 20;$i++){ ...python -m pytest... }`，**不要试 `--count`**。
- **绝不** `git checkout` / `git reset`（main.py 有未 commit 的 A1 接线，会丢）。

---

## 1. 功能项清单（LOCKED — 11 项）

### FEAT-A1 — 意图记忆接线（决策1）【已起草，待补单测+核验】
- **目标**：preference_memory 的 `kind="intent"` 接入主流程，实现"问模型这类纯提问
  记成 ask、后续同类免澄清直接答；派活记成 task"。
- **现状**：`backend/main.py` 已起草（未 commit）：
  - record：agent loop 后按本轮有无 tool_call → `record(text,"task"/"ask","intent")`（_FinEv，fire-and-forget，仅 in_code_mode+pref_mem+非 sentinel）。
  - match：turn 起始 `match(text,"intent")` 命中 → 注入 system hint（ask="直接回答别动手" / task="进工作流"）。
- **完成定义**：
  1. 上述 main.py 接线保留并正确（py_compile 绿）。
  2. **抽两个纯函数到 main.py（便于单测）**：
     - `_build_intent_hint(label: str) -> str | None`（注入侧：ask→"直接回答别动手" hint，
       task→"进工作流" hint，其它→None）；main.py 注入处改调它。
     - `_intent_label_from_turn(had_tool_call: bool) -> str`（record 侧：True→"task"，False→"ask"）；
       main.py record 处（现 5342 内联三元）改调它。
  3. **两纯函数必须 module 顶层 def**（非闭包内），可被 `from main import _build_intent_hint,
     _intent_label_from_turn` 直接 import（否则单测 import 不到）。
  4. **新增 `backend/tests/test_intent_memory_wiring.py`**，硬断言：
     - `_intent_label_from_turn(True)=="task"`、`_intent_label_from_turn(False)=="ask"`。
     - `_build_intent_hint("ask")` / `("task")` 返回含关键词的 hint，`("x")`→None。
     - 集成：fake preference_memory，match 命中 ask → 验证 `_msgs` 中**确实插入了** ask hint
       system 消息，且插在 system 块之后（对齐 main.py 现有插入位置 while-loop 逻辑）。
     - **flag-off 守恒**：`preference_memory is None`（flag off）→ `_msgs` **零注入、字节不变**
       （守"出厂字节级不变"铁律）。
  4. 误记防护：`match` 内部已按 threshold=0.86 过滤（preference_memory.py:147），
     **返回非 None 即注入，注入侧不再二次判分**；record 去重已由 test_preference_memory 覆盖。
- **文件归属**：`backend/main.py`（A1+A2+A4 共享 → **me 顺序做**，子代理不碰）、
  新增 `backend/tests/test_intent_memory_wiring.py`（me）。

### FEAT-A2 — `/prefs` slash 命令（查看/清除偏好记忆）
- **目标**：用户可查看 + 清除偏好记忆，防误记（决策1/2 风险标注的"可查看/可清除"）。
- **完成定义**：
  1. `backend/deskpet/commands/__init__.py`：
     - dispatch 签名加 keyword-only `session_pref_memory: Any = None`（不破坏现有调用）。
     - `if name == "prefs": return _handle_prefs(args, session_pref_memory)`。
     - `_handle_prefs`：无 args→list（`list_entries()`）；`clear`→全清；`clear intent|plan`→按 kind 清；
       `session_pref_memory is None`（flag off）→ `{"type":"error","message":"偏好记忆未启用 (features.preference_memory)"}`。
  2. **锁定返回契约（防跨层漂移，项目坑#5）**：
     - list → `{"type":"prefs_list","entries":[{"text","label","kind","ts"}],"count":N}`
     - clear → `{"type":"prefs_cleared","removed":N,"kind":<str|null>}`
  3. main.py 调用处（现 `dispatch_slash_command(...)` ~3692）加 `session_pref_memory=service_context.get("preference_memory")`。
  4. `_handle_help` 的 builtins 列表加 `{"name":"prefs","..."}` / `{"name":"prefs clear",...}`。
  5. **前端从零实现（CRITICAL：ws.ts 当前无 `slash_command_result` case，现有 /help /goal 结果
     也被静默丢弃）**：
     - `ws.ts` 新增 `case "slash_command_result"`：按 `payload.result.type` 分支，**8 种全覆盖**
       （`help` / `goal_set` / `goal_status` / `goal_cleared` / `skill_result` / **`prefs_list`** /
       **`prefs_cleared`** / `error`）push 成一条可读 message（新增 `role:"slash_result"` 或复用
       assistant bubble + 格式化文本）。**default 分支也要 push 一条 raw JSON 兜底，禁止 type 落空静默丢。**
     - `MessageBubble.tsx` 能渲染该 role（prefs_list 逐条 "{kind}/{label}: {text}" + "共 N 条"；
       prefs_cleared "已清除 N 条(kind=...)"）。
     - **完成判据**：F5 重连后输入 `/prefs` 面板肉眼可见偏好条目列表；`/prefs clear` 可见清除条数。
       后端单测绿 ≠ 完成。
  6. **单测**：(a) 后端 `backend/tests/test_slash_prefs.py` — list/clear/clear-by-kind/flag-off(None) 四态;
     (b) **前端 vitest（仿 InputBar.slash.test.tsx 若有）断言 8 种 result.type 每种都 push 了非空 message**
     ——把"分支漏写"从 windows-mcp 阶段提前到代码阶段拦截（tsc 抓不到缺分支的静默丢）。
- **文件归属**：`backend/deskpet/commands/__init__.py`(me)、`backend/main.py`(dispatch 调用处—me 顺序)、
  `tauri-app/src/code-panel/ws.ts` + `MessageBubble.tsx`(me 顺序)、新增 test(me)。

### FEAT-A3 — verify_gate 出厂默认评估 + 实现可选翻 shadow
- **目标**：评估出厂默认 off→shadow 的利弊；**实现为配置可翻**，但**出厂默认是否翻由
  用户早上拍板**（本阶段不擅自改 config.py 出厂默认值）。
- **完成定义**：
  1. 在本 spec §6 写清利弊（receipt 开销 / shadow 永不拦 / invariant 要 emit_receipts）。
  2. **invariant 已有覆盖**：`test_tool_last_mile_config.py::test_t1_3/t1_7/t1_8/t1_11`
     （VG-INVARIANT-0 非法 mode / VG-INVARIANT-1 mode!=off→emit_receipts / run_build+off 自动转
     shadow / ephemeral model 校验）。**A3 只需跑这些确认绿，不新建测试文件**（避免重复）。
     仅当发现未覆盖分支（如 `mode=shadow + emit_receipts=false` 是否报错）才补一条**到现有文件**。
  3. **出厂默认一个字节都不改**：`config.py:249-250` 的 `emit_receipts=False` /
     `verify_gate_mode="off"` 保持原值。翻不翻由用户早上拍板。
- **文件归属**：`backend/config.py`（**只读核对，不改默认值**）、必要时改 `test_tool_last_mile_config.py`。

### FEAT-A4 — plan 消息持久化（rehydration 不丢 awaiting plan）
- **目标**：现 F5/HMR rehydration 从 SessionDB 重载会丢掉前端临时的 awaiting plan
  消息（plan-confirm 按钮消失）。修复：awaiting plan 状态可在面板重载后恢复。
- **三层契约**（CRITICAL：当前三层都会丢 plan——messages 表无 kind 列、`session_messages_load`
  (main.py:3627) 硬过滤 `role not in (user,assistant,tool)` 会丢弃 plan 行、rehydration
  (ws.ts:447) 不重建 plan card 字段。**缺一层都假绿**）：
  1. **后端持久化（推荐 sidecar 表，避免污染检索）**：新建 `session_plans(session_id PK,
     rationale, steps_json, awaiting, ts)`（session_db.py）。plan 门 awaiting 时 upsert 一条。
     **不得**走 `append_message`——那会触发 `_on_message_written` hook(session_db.py:305) →
     VectorWorker embed + FTS5 trigger，plan JSON 污染语义检索（MISSING-4）。
  2. **load 带回**：`session_messages_load`(main.py:3603) 在返回 messages 的同时，查
     `session_plans` 并在 payload 里附 `plan:{rationale,steps,awaiting}`（或单开
     `session_plan_response`）。不要试图把 plan 塞进被白名单(3627)过滤的 messages 流。
  3. **前端 rehydration 重建**：`ws.ts` 的 `session_messages_response`(447) 处理新增的 plan
     字段 → 重建 `{role:"plan", plan_rationale, plan_steps, plan_awaiting_confirm:true, plan_sid}`
     （对齐 `chat_v2_plan` handler ws.ts:265-277 的字段），使 SessionGridView [执行]/[取消] 栏回来。
  4. **清除时机（三处，漏 timeout 即 bug）**：`plan_confirm` go/cancel(main.py:3741) +
     `plan_confirm_gate_timeout`(main.py ~4991，wait_for 超时分支) + `chat_v2_plan_cancelled`
     都要清 `session_plans` 的 awaiting 标记。
- **实现细节锁定（R2，照此做防假绿）**：
  - **建表落点（关键，否则表不存在被静默吞）**：追加到 `memory_v2_schema.py` 的 `_DDL`，
    复用幂等入口 `ensure_memory_v2_tables`。**不新增 migration**（当前 schema v17，bump 会引发
    一堆硬编码测试断言改动）。SQL：
    ```sql
    CREATE TABLE IF NOT EXISTS session_plans (
        session_id TEXT PRIMARY KEY, rationale TEXT NOT NULL DEFAULT '',
        steps_json TEXT NOT NULL DEFAULT '[]', awaiting INTEGER NOT NULL DEFAULT 0,
        ts REAL NOT NULL);
    ```
    PK=session_id 够用（plan 门 `_PLAN_CONFIRM_WAITERS[_sid]` 是 per-session 单 future，
    同时只一个 awaiting plan；upsert 天然覆盖重复 plan）。
  - **session_db.py 加 3 方法**（仿 `upsert_code_session`/`_with_retry` 风格）：
    `upsert_session_plan(session_id, rationale, steps:list[dict], awaiting:bool)` —
    **仿 `upsert_code_session`(session_db.py:823)：先 `if not self._initialized: await self.initialize()`，
    再 `await ensure_memory_v2_tables(self._db_path)`**（补 SW-1 关键行，保证全新 DB 上表就绪）+ INSERT…ON CONFLICT；
    `get_session_plan(session_id)->dict|None`（返回前 `json.loads(steps_json)`、`bool(awaiting)`）；
    `clear_session_plan_awaiting(session_id)`（UPDATE awaiting=0，**幂等可重复调**）。
  - **upsert 触发**：main.py ~4960，`_awaiting_confirm` 为真 + send `chat_v2_plan` 后调
    `upsert_session_plan(_sid, _plan.rationale, [{title,detail}...], True)`（try/except 只 log 不阻断）。
  - **load 带回**：main.py:3656 send `session_messages_response` 前查 `get_session_plan(target_sid)`，
    awaiting 时 payload 附 `"plan":{rationale,steps,awaiting}`（**不塞进被 3627 白名单过滤的 messages**）。
  - **前端 rehydration**：ws.ts:531 `set_messages` 后，若 `msg.payload.plan?.awaiting` →
    `push_message(target,{role:"plan",plan_rationale,plan_steps,plan_awaiting_confirm:true,plan_sid:target})`。
  - **清除三处**：plan_confirm go/cancel(main.py:3741,go/cancel 都清) + timeout finally(main.py:4994) +
    chat_v2_plan_cancelled(4996)。幂等，重复无害；最稳=4994 finally 统一清 + 3741 handler 也清。
- **单测**：后端契约测试断言 sidecar upsert → load 重建出 `awaiting=true`；清除后 awaiting=0。
  **必须在「从未调 ensure 的全新 DB」上断言 upsert 自带建表能工作**（不靠 fixture 预建表，否则掩盖 SW-1）。
- **完成判据**：windows-mcp 阶段：开 plan 门→出 plan card→F5→[执行]/[取消] 按钮仍在→点[执行]能继续。
  三层(DB/load/前端)缺一不算完成。
- **文件归属**：`backend/main.py`、`backend/deskpet/memory/session_db.py`、
  `tauri-app/src/code-panel/ws.ts`、`tauri-app/src/stores/sessionsStore.ts`、新增 test。
  ⚠️ 与 A1/A2 共享 → **A1+A2+A4 由同一执行者(me)顺序做**。

### FEAT-A5 — 文档：superpowers code 工作流写进 README/docs
- **目标**：把 Layer 1A persona 纪律 + plan-confirm 硬门 + 偏好记忆 + verify strict +
  `/prefs` 写成用户/开发者可读文档（3+ flag 行为 + 如何开 + 注意事项）。
- **完成定义**：
  1. `README.md` 在现有"Companion + Code 模式升级 v1"段（约 README:417）**之后**新增
     "Code 模式工作流纪律 (superpowers)"段：意图门→澄清→计划→执行→验证 +
     `features.plan_confirm_gate` / `features.preference_memory` / `tools.verifier.verify_gate_mode`
     三 flag 说明 + **出厂默认值（全 OFF / off）** + 如何在 config.toml 开。
  2. **新建 `docs/CODE-WORKFLOW.md`**（单一落点，不要塞进 SKILLS.md 半截）：偏好记忆机制 +
     `/prefs` 用法 + plan 硬门 [执行]/[取消] 交互 + verify strict 行为。
  3. 链接到 `docs/superpowers/plans/2026-06-02-superpowers-code-workflow/` 下的 proposal + E2E 报告
     （**先 `ls` 确认实际路径**：仓库 plans 既有 `plans/` 也有 `docs/superpowers/plans/`，用真实存在的那个）。
- **文件归属**：`README.md`、新建 `docs/CODE-WORKFLOW.md`（独立 → **可并行子代理独占**）。

### FEAT-C1 — 全套 backend pytest baseline + 修红
- **目标**：建立绿色基线；红的修或确认 flaky（B1 单独处理 flaky）。
- **完成定义**：
  1. `cd backend && pytest -q` 全跑，记录 pass/fail/skip/xfail/collect-error 数 + 失败用例清单到 §进度日志。
  2. **每个 fail 三选一标注**：代码 bug / 环境缺失（如 BGE-M3 模型路径、sqlite-vec）/ flaky。
     **环境缺失类不得静默跳过，须列入进度日志让用户知情**（不是"修绿"，是"标记环境依赖"）。
  3. 代码 bug 类逐个修绿，**每个修复附 before(红)/after(绿) 的 pytest 行**。
     **不允许用 xfail/skip 掩盖未修的失败**（降级，违反铁律）；flaky 交 B1。
  4. 终态：除已知 flaky + 已标注环境缺失外全绿。
- **文件归属**：跨文件（诊断+定向修）；**最后阶段做**（其他 FEAT 改完后），避免与并行改动赛跑。

### FEAT-C2 — 新代码边角覆盖
- **目标**：补 superpowers 新代码的错误/边角路径单测。
- **完成定义**（新增测试文件，**不改产品代码**除非发现真 bug）：
  1. plan 门：timeout→cancel 路径、cancel→return 不执行、waiter 覆盖（重复 plan）。
  2. preference_memory：embed 返回空/抛异常→record/match 安全返 False/None（不崩）；
     max_entries 截断；list/clear 边角。
  3. verify strict：nudge 耗尽→consult_ephemeral→放行；ephemeral=None→保守 fail。
  - 发现真 bug → 按最佳实践修（记进度日志），不掩盖。
- **文件归属**：新增 `backend/tests/test_*_edgecases.py`（新文件 → 可并行）。

### FEAT-C3 — tsc + lint 全绿
- **目标**：前端 `npx tsc --noEmit` 0 错误；新 py 文件 lint（ruff，若项目用）清。
- **完成定义**：tsc 0 错误；新增/改动 py 文件无 lint error（按项目既有 lint 配置）。
- **文件归属**：跨文件检查 + 定向修；**最后阶段做**。

### FEAT-B1 — 修 flaky `test_enqueue_small_batch_flushes_on_interval`
- **⚠️ 前提更新**：该用例（`tests/test_deskpet_vector_worker.py:132-147`）**已于 P4-S16 去
  sleep 化**，改用 `await worker.wait_for_drain(timeout=5.0)`（worker 侧 `_flush_cond` Condition，
  vector_worker.py:339-395）。spec 原说的"sleep 行"已不存在。**B1 = 验证为先**。
- **完成定义**：
  1. **先复现**：`pytest tests/test_deskpet_vector_worker.py::test_enqueue_small_batch_flushes_on_interval`
     连跑 20 次。工具：先确认 `pytest-repeat` 可用（`pytest --count=20 ...`）；否则用
     PowerShell `for($i=0;$i -lt 20;$i++){ ...pytest... }` 循环。
  2. **若 20 次全绿** → 记录"已由 P4-S16 修复，本项确认 closed"，**附 20 次绿的实际 pytest 输出**
     （这是"确认已修"，不是降级跳过——符合铁律）。
  3. **若仍 flaky** → 真因可能在 `worker fixture flush_interval_s=0.3`(test:60) 与 mock embedder
     warmup 时序、或 `_run_loop` 的 `asyncio.wait_for(timeout)`(worker:262) 边界 race，**非 sleep**。
     定位真正非确定点后确定化（注入 clock / Condition / event），再连跑 20 次全绿。
- **文件归属**：`tests/test_deskpet_vector_worker.py` + 可能 `backend/deskpet/memory/vector_worker.py`
  （独立 → 可并行子代理；若改 worker 与 C1 修红重叠则顺序）。

### FEAT-B2 — `feat/memory-stage2-followup-f1f2` → master（R2 预判：已合，无事可做）
- **R2 merge-tree 预判**：该分支**已是 master 祖先**（`git merge-base --is-ancestor` 真，
  `master..` 计数 0）。merge 会直接 "Already up to date"。
- **完成定义**：
  1. 先 ancestry check：`git rev-list --count master..feat/memory-stage2-followup-f1f2`。
  2. ==0 → 记"已并入 master，B2 closed"，**不执行 merge 命令**（"Already up to date" 非出错）。
  3. 若意外 >0（分支被更新）→ 才按原 merge 纪律（--no-commit 看冲突 / 测试绿才提 / 非平凡 abort）。
- **文件归属**：git（me，确认即可）。

### FEAT-B3 — merge `feat/companion-code-v2` → master（R2 预判：9 commits，零冲突可自动合）
- **R2 merge-tree 预判**：9 commits，`merge-tree --write-tree` exit 0 **零冲突**（与 master 重叠
  5 文件均非冲突 hunk，git 能三方自动合）。引入 `backend/deskpet/agent/team/` 整套 + 改 config.py/doc_tools/memory_tools。
- **完成定义**：
  1. **必须排在波次 2（A1/A2/A4 全 commit）之后**——B3 也碰 main.py/config.py，先让本地改动落盘。
  2. `git merge --no-ff feat/companion-code-v2`（预判零冲突，应直接成功）。
  3. **merge 后立即跑全套 backend pytest**（B3 带 test_spawn_team / test_partition_dispatch 等新测 +
     改了 config.py，须确认与 master 的 config.py 改动合并后 invariant 仍过）。红 → 修或 `git reset --hard`
     回退 merge（**不 push**，留早上）。
  4. **⚠️ 合后硬验收**：`git diff` 复核 `config.py` 的 flag 默认值——B3 改了 config.py，**任何出厂
     flag 默认变动须标红等用户拍板**（守"出厂字节级不变"铁律）。
  5. commit（**不 push**，留早上 review）。
- **文件归属**：git（me，波次 2 后、波次 3 测试前）。

---

## 2. 依赖与执行顺序

```
并行波次 1（独立文件，opus-4.8 子代理并行；R2: 四者零文件交集 → worktree 非必要，
            同 checkout 并行即可。各 agent pytest 用 tmp_path（默认 fixture 已隔离），
            不共享 dev userdata DB）:
   FEAT-A5(docs) ‖ FEAT-C2(new edge tests) ‖ FEAT-B1(flaky 验证) ‖ FEAT-A3(invariant 确认+利弊文档)
顺序波次 2（共享 main.py/前端，me 顺序做）:
   FEAT-A1(核验+抽纯函数+单测) → FEAT-A2(/prefs 后端+前端) → FEAT-A4(plan 持久化三层)
波次 2.5（B3 merge — R2: 零冲突可自动合，但须在 A1/A2/A4 commit 后；me）:
   FEAT-B2(ancestry check=closed) → FEAT-B3(merge + 立即全测 + 复核 config 默认)
收口波次 3（检查类，全部改完后，me）:
   FEAT-C1(pytest baseline 修红) → FEAT-C3(tsc/lint)
```

## 3. 文件归属表（防并行冲突 — 同文件只一个写者）

| 文件 | 归属 |
|---|---|
| `backend/main.py` | A1/A2/A4（**me 顺序**，子代理不碰） |
| `tauri-app/src/code-panel/ws.ts`、`stores/sessionsStore.ts` | A2/A4（me 顺序） |
| `backend/deskpet/commands/__init__.py` | A2（me） |
| `backend/deskpet/memory/session_db.py` | A4（me） |
| `tauri-app/src/code-panel/MessageBubble.tsx`、`SessionGridView.tsx` | A2(slash 结果展示)+A4(plan card 重建)（**me 顺序**） |
| `README.md`、新建 `docs/CODE-WORKFLOW.md` | A5（子代理，独占） |
| `backend/tests/test_*_edgecases.py`（新） | C2（子代理，独占新文件） |
| `tests/test_deskpet_vector_worker.py` + `vector_worker.py` | B1（子代理，独占） |
| `backend/config.py`（只读不改默认）+ 现有 `test_tool_last_mile_config.py` | A3（子代理） |
| `backend/tests/test_intent_memory_wiring.py`、`test_slash_prefs.py` | A1/A2（me，与产品代码同批） |

**并行约束**：C2 的边角测试**只能 import 已存在的稳定符号**（`deskpet.agent.preference_memory.PreferenceMemory`、
plan 门/verify_gate 现有逻辑）；**禁止** import A1 新抽的 `_build_intent_hint`/`_intent_label_from_turn`
或 A4 新 sidecar schema——那些归 A1/A4 自带单测。否则 C2 先跑会 ImportError（C2 在并行波次 1、
A1/A4 在顺序波次 2）。

## 4. 每个 FEAT 的"完成定义"汇总（完成度评估子代理用此核对）

见各 FEAT 的"完成定义"小节。评估子代理须逐条核对 1..N 全满足 + 对应单测真绿
（要求实际跑 `pytest` 看输出，不接受"应该绿"）。

## 5. 真机测试阶段（实现全完成后）

1. 写 `06-manual-test-cases.md`：**只含人工操作**（鼠标点击坐标/键盘输入/肉眼判定），
   覆盖全部 11 FEAT 的用户可见行为（plan 按钮、/prefs、意图学习、verify 拦截提示等）。
2. opus-4.8 子代理评估迭代该手测文档 1 次（补漏、明确步骤/期望）。
3. **windows-mcp 真机测试**：按手测文档逐 case 真模拟人工点击/输入 + 截图 + 判定。
   （遵守 CLAUDE.md HARD CONSTRAINT：不得用脚本/协议层/import 替代。）

## 6. FEAT-A3 决策材料（verify 出厂默认）

- 现状：出厂 `verify_gate_mode="off"` / `emit_receipts=false`（config.py 默认）。
- 翻出厂 `shadow` 的代价：① invariant 要求同时 `emit_receipts=true` → 每次工具 dispatch
  都 emit + 持久化 receipt（小 IO/CPU 开销）② 每个未匹配 claim 记 WARN 日志。
- 翻出厂 `shadow` 的收益：生产环境观测真实 fake-completion 发生率，为将来出厂 strict 提供数据。
- shadow **永不拦截**（passed 总 True）→ 无用户可见破坏。
- **评估结论（供用户拍板，本阶段不落地）**：倾向出厂 shadow（稳、可观测）；strict 仅 dev/高级用户开。
  **实现阶段 `config.py:249-250` 的 `emit_receipts=False` / `verify_gate_mode="off"` 一个字节都不改。**

---

## 进度日志（实现期追加，倒序）

### 波次 1（opus-4.8 子代理并行）— 全部完成 ✅
- **FEAT-A5 文档**：README 新增"Code 模式工作流纪律 (superpowers)"段(README:475)+ 新建
  `docs/CODE-WORKFLOW.md`(7 节)。7 个链接均验证存在(用 `plans/...` 真实路径)。出厂默认对照核实未改。
- **FEAT-C2 边角测试**：新建 `backend/tests/test_superpowers_edgecases.py`，**24 passed**。覆盖
  preference_memory(embed 空/异常/截断/list-clear 边角)、verify strict(nudge 耗尽/ephemeral 三态/N1)、
  plan 门(not-code-mode/短消息 早返无副作用)。只 import 稳定符号。**无产品 bug**。
- **FEAT-B1 flaky**：`test_enqueue_small_batch_flushes_on_interval` **20/20 全绿** → confirmed closed
  (P4-S16 已用 wait_for_drain 去 sleep 化)。未改文件。
- **FEAT-A3 invariant**：t1_3/t1_7/t1_8 绿；**补 `test_t1_7b_shadow_requires_receipts`**(shadow+
  emit_receipts=false→raise VG-INVARIANT-1)绿。config.py:249-250 出厂默认字节级未改。
  ⚠️ **挖出预存 flaky `test_t1_11`**：`load_config` 的 (path,mtime) 进程缓存在同一 mtime tick 内
  重写 config 命中旧缓存跳过 invariant 重校验 → 偶发 "DID NOT RAISE"。**归 C1/B 修红范围，已 spawn_task**，
  不在 A3 动（A3 不改产品代码）。
- 波次 1 合并验证：`pytest test_superpowers_edgecases + test_preference_memory + test_verify_gate_code_patterns`
  = **40 passed**。

_（波次 2 A1/A2/A4 + 波次 2.5 B2/B3 + 波次 3 C1/C3 待 me 顺序做）_
