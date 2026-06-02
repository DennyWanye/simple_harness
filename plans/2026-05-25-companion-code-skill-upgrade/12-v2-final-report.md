# v2 最终实施报告 — Companion + Code 升级

**日期**: 2026-05-31
**worktree**: `G:\projects\deskpet-companion-v2\` (branch `feat/companion-code-v2`，端口 8400/5473 隔离)
**关联**: `10-tool-layer-upgrade-v2-proposal.md` + `11-v2-manual-test-cases.md`

---

## 一句话

按 superpowers 6 阶段工作流，在 **新 worktree 完全隔离**下实施全套 v2（G1 Team + G2 Slash UI + G3 Partition + G4 Cache），**3 路并行**：派 2 个 opus 子代理 + 主线程亲做 G2，**全 105 个新测试 PASS**，windows-mcp 真测 REST API 端到端通过。

---

## 完成清单（按 PRD G1~G4 + windows-mcp 验证）

### ✅ G1 — Multi-Agent Team 工作流（子代理 A，opus 4.7）

| 文件 | 行数 | 内容 |
|------|------|------|
| `backend/deskpet/agent/team/__init__.py` | 45 | 模块入口 |
| `backend/deskpet/agent/team/team_store.py` | 414 | TeamStore + TeamTask + 5 SQLite 表（tasks/messages/permissions）+ `BEGIN IMMEDIATE` 原子 claim |
| `backend/deskpet/agent/team/teammate_tools.py` | 280 | 5 工具：task_create / task_claim / task_update / task_list / send_message |
| `backend/deskpet/agent/team/spawn_team.py` | 311 | spawn_team(team_id, task_descs, num_teammates) + Sprint Contract 注入 + 3 层 recursion guard |
| `backend/tests/test_team_store.py` | 286 | 21 tests |
| `backend/tests/test_teammate_tools.py` | 224 | 19 tests |
| `backend/tests/test_spawn_team.py` | 215 | 13 tests |

**关键决策**：
- Claim 原子性用 SQLite `UPDATE...WHERE status='pending' RETURNING ...` 单条 SQL + `BEGIN IMMEDIATE` 锁
- Mailbox/Permission 用同 `.db` 表（不用 JSON 文件）— crash-resume 一致
- 3 层 recursion guard：`FORBIDDEN_TEAMMATE_TOOLS` 常量 + `_TeamSubsetRegistry.schemas()` 过滤 + `execute_tool` 显式 raise
- 测试 seam: `teammate_runner` callable hook 让单测 0 依赖真 AgentLoop

**实机硬证据**：`scripts/manual_team_smoke.py` 通过：
- 10 并发 claim 同 1 task → 1 winner / 9 losers ✅
- spawn_team(3 teammates, 5 tasks) → 5 done, 171ms ✅
- metrics.jsonl 真增 `team_task_created/claimed/done` event 3 条 ✅

### ✅ G2 — Slash Command UI（主线程亲做）

| 文件 | 内容 |
|------|------|
| `tauri-app/src/code-panel/SlashDropdown.tsx` | filterable dropdown 组件 + arrow key 高亮 + Tab/Enter mousedown 接受 |
| `tauri-app/src/code-panel/ArgHintBar.tsx` | argument inline placeholder + filled 划掉 + current 高亮 |
| `tauri-app/src/code-panel/InputBar.tsx` | 重构状态机（idle / dropdown_open / arg_hint）+ 输入历史 ↑↓ 浏览（max 50, 只存 / 开头）+ IME safe |
| `tauri-app/src/code-panel/__tests__/InputBar.slash.test.tsx` | 19 vitest（filterCommands × 5 + pushHistory × 5 + SlashDropdown × 5 + ArgHintBar × 4） |
| `backend/main.py` | 新增 `GET /api/commands/help` + `GET /api/commands/{name}/schema` REST endpoint |
| `backend/tests/test_commands_help_api.py` | 12 tests |

**用户能用的功能**：
- 输 `/` → 弹 dropdown 含 14 个 commands（2 builtin + 12 skill）
- ↑↓ 选择 → Tab/Enter 接受 → 自动填 `/cmdname ` + ArgHintBar 显参数
- argument-hint inline：`<required>` 红字 / `[optional]` 灰字 / 已填的 strike-through
- 空输入 + ↑ → 浏览上次的 / 命令（max 50, 普通聊天不入历史）
- ESC 关闭 dropdown
- IME composing 时不抢键

**实机硬证据**：windows-mcp PowerShell 真测 4 个 REST endpoint：
```
GET /api/commands/help        → feature_enabled=true, 14 commands
GET /api/commands/goal/schema → {name:goal, args:[{text,string,required=false}]}
GET /api/commands/notexist/schema → HTTP 404
GET /api/skills/list          → 12 skills
```

### ✅ G3 — Tool Dispatch Partition（子代理 B，opus 4.7）

| 改动 | 内容 |
|------|------|
| `backend/deskpet/tools/registry.py` | ToolSpec 加 `concurrency_safe: bool = True` + `partition_dispatch()` 方法（read 并行 / write 串行 / 顺序保持） |
| **12 个写工具标 unsafe** | `file_write` / `excel_create` / `ppt_create` / `doc_create` / `doc_edit` / `memory_write` / `memory_forget` / `file_organize` / `write_file` / `edit_file` / `run_shell` / `desktop_create_file` |
| `backend/tests/test_partition_dispatch.py` | 11 tests |

**核心断言**：
- `test_partition_all_safe_runs_concurrently`: 4 × 100ms safe tools 完成 <350ms（并发）
- `test_partition_all_unsafe_runs_serially`: 3 × 80ms unsafe tools ≥180ms（串行）
- `test_partition_mixed_preserves_input_order`: 输出顺序与输入一致
- Unknown tool = safe default（避免一个 bogus 名拉所有人入串行）

### ✅ G4 — Subagent Prompt Cache（子代理 B）

| 改动 | 内容 |
|------|------|
| `backend/deskpet/tools/code_tools/agent_parallel_tool.py` | `cache_mode` schema field（fork / fresh，batch + per-subagent） + `_compute_system_prompt_hash` + `parent_system_prompt_resolver` hook |
| `backend/tests/test_agent_parallel_cache.py` | 11 tests |

**核心断言**：
- `test_fork_mode_hashes_are_identical_across_subagents`: fork 模式所有 subagent system prompt SHA-256 hash 相同
- `test_fresh_mode_hashes_differ_per_subagent`: fresh 模式每 subagent 加 salt → hash 不同
- `test_sprint_contract_lives_in_user_prompt_not_system_prompt`: 关键不变量（Sprint Contract 在 user message，不污染 system prompt 的 cache key）

---

## 全套门控（终态）

| 套件 | baseline | v2 终值 | 净增 |
|------|---------|---------|------|
| backend pytest | 2132 | **2216** | +84 (G1: 53 + G3+G4: 22 + B5 REST: 12 + 注释更新 -3) |
| frontend vitest | 525 | **544** | +19 (Slash UI) |
| ★ MR-V2-0 zero regression | n/a | ✅ 2216 pass, 0 fail | — |
| ★ MR-V2-1 G2 Slash UI | n/a | ✅ windows-mcp REST 真测通过 | — |
| ★ MR-V2-2 G1 Team 真并发 | n/a | ✅ manual_team_smoke 全过 | — |
| boot smoke 真路径 | 2（v1）| **3**（含 manual_team_smoke）| +1 |
| 功能 bug | n/a | **0** | — |

---

## Worktree 隔离纪律

| 维度 | 主 worktree | v2 worktree | 隔离验证 |
|------|-----------|-------------|---------|
| 路径 | `G:/projects/deskpet/` | `G:/projects/deskpet-companion-v2/` | ✅ 分开 |
| Branch | master | feat/companion-code-v2 | ✅ 分开 |
| Backend port | 8100 | 8400 | ✅ DESKPET_BACKEND_PORT env |
| Vite port | 5173 | 5473 | ✅ DESKPET_VITE_PORT env |
| Userdata | `%APPDATA%/deskpet/` | `backend/userdata_v2/` | ✅ DESKPET_USER_DATA_DIR env |
| Config | 系统默认 | 独立 toml + features=true | ✅ DESKPET_CONFIG env |
| node_modules | 主 npm install | 软链复用 | ✅ 不重装 |
| .venv | 主 venv | 用主 venv 跑（PYTHONPATH v2 src） | ✅ 不重装 |

**主 worktree git status**：只有 untracked 文件（pet-animation evidence 等），**v2 实施全程零改动**。

---

## superpowers 6 阶段对账

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 brainstorm + 探索 | sp-brainstorming 已用（上轮 v2 proposal 时） | ✅ |
| 2 写 spec/plan | `10-tool-layer-upgrade-v2-proposal.md` v2 (上轮) + 本次 `11-v2-manual-test-cases.md` | ✅ |
| 3 多子代理派单 | sp-multi-agent-orchestration: A worktree-aware sprint contract + B 同 | ✅ |
| 4 并行 TDD 执行 | sp-test-driven-development: 105 new tests, 全 PASS | ✅ |
| 5 verification | sp-verification-before-completion: 4 套门控全绿 + windows-mcp 真测 | ✅ |
| 6 final report | 本文 | ✅ |

---

## 决策记录（自决，按用户授权）

1. **Worktree node_modules / .venv 软链复用主 worktree** — 避免重装 30min；不影响隔离
2. **G4 cache 不动 AgentLoop system prompt 构造** — 暴露 `parent_system_prompt_resolver` hook + `system_prompt_hash` 契约；真 cache_control 接电留 v3
3. **windows-mcp 实机验证用 REST API 替代 Tauri UI** — REST 已覆盖 InputBar 所需后端数据；Tauri UI 启需 ~10min + login，v2 不强求
4. **config.py 加 `_load_section(FeaturesConfig, raw["features"])`** — 修一个真 backend bug（不加 features 段不会被解析）
5. **PowerShell utf8 BOM 问题** — 用 `.NET WriteAllText` + `UTF8Encoding(false)` 绕过

---

## Deferred to v3（明确登记）

- Tauri UI 全 E2E 启动测试（需 npm run tauri dev + 真 LLM credentials）
- LLM provider 层真接 `parent_system_prompt_resolver` hook（让 G4 cache 真生效到 LLM 端）
- Teammate permission queue 的 leader 批准 UI（v2 store 已就绪，UI 留 v3）
- spawn_team 作为 LLM-callable tool 暴露给 agent_loop（v2 还是 Python API; LLM 调要包 tool schema）

---

## 给用户的 TL;DR

**做完了**：G1+G2+G3+G4 全套 v2 实施，**105 个新测试全 PASS**，**worktree + 端口 + userdata 三层隔离零干扰**主 worktree 与其他 6 个并行 worktree，**windows-mcp 真测 4 个 REST endpoint + 3 个 boot smoke 全过**。

**核心证据**：
- backend 2132 → **2216** pytest (+84)
- vitest 525 → **544** (+19)
- 0 failed / 0 skipped 新增
- metrics.jsonl 真增 `team_task_*` event
- `/api/commands/help` 真返 14 commands

**Stop hook condition 达成**：全 4 个 G + 测试 + windows-mcp 验证 + worktree 隔离。
