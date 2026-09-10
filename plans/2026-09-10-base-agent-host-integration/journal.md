# Harness SDK 0.8.0（BaseAgent 第一阶段）钉入 Host · 记录

日期：2026-09-10。对应 SDK 计划 `simple-harness-sdk/plans/2026-09-10-base-agent-phase1/`（S1–S5 全部 SHIPPED）。

## 1. 钉版事实

| 项 | 值 |
|---|---|
| SDK 版本 | 0.8.0（源提交 `dffd13c829d42ac3df213ac0c490f3d5834a8b2b`，`SOURCE_DATE_EPOCH=1789050129`） |
| wheel | `backend/vendor/simple_harness_sdk-0.8.0-py3-none-any.whl`，sha256 `f1f1a46925b63539ede574c366a5688006aa60b679d0e7cb873dcbcac0c22d67` |
| 候选清单 | `backend/vendor/simple_harness_sdk-0.8.0.candidate-manifest.json`（`execution_schema: 10`） |
| 单一真相 | `backend/deskpet/sdk_adapters/sdk_candidate.py`（SDK_VERSION/WHEEL_SHA256/MANIFEST_SHA256/SOURCE_COMMIT） |
| pyproject/uv.lock | `simple-harness-sdk==0.8.0`（三处）；`uv lock && uv sync --extra dev`（换 wheel 需先 `uv cache clean simple-harness-sdk`） |
| 启动迁移 | `composition.py::start`：`migrate_execution_to_v9` 之后新增 `migrate_execution_to_v10`（备份 `execution-v6.sqlite3.pre-schema-10.backup`，同目录、不覆盖、回放同一回执） |

## 2. 钉版过程中发现并修复的问题

| # | 问题 | 处置 |
|---|---|---|
| H-1 | 本机真实执行库 `~/Library/Application Support/com.dennywanye.simpleharness/data/simple-harness-sdk/execution-v6.sqlite3` 是 **v7 且没有显式审计表**（早于审计 schema 的 SDK 写的）。0.7.10/0.8.0 的 `Database.open` 拒绝 v7（要求显式迁移），而 `migrate_execution_to_v9` 又要求审计表已存在 → 该库既开不了也升不了（0.7.10 钉版时"installed/native pending"，从未在真实库上启动过） | SDK `dffd13c`：v9 升级器对"审计对象完全缺失"的 v7/v8 库，在备份保留之后、同一事务内引导审计 schema（等价 `Database.open` 的做法）；部分审计表仍拒绝。真实库副本干跑：7→9→10 共 0.56 s，两次回放相等，重开 schema 10、runs 24 行、19 张 base_agent 表 |
| H-2 | 全新 v10 库上无条件调用 `migrate_execution_to_v9` 报 `execution_short_upgrade_unknown_catalog`（Host 测试 `test_start_reconcile_recover_query_close_and_schema_independence` 红） | 同一提交：v9 升级器对已在 v9 之后的库只复核描述符序列 + 完整性并返回 None/回执，Host 可在启动时无条件顺序调用 v9、v10 |

## 3. 测试

| 项 | 结果 |
|---|---|
| Host `backend/tests/sdk_adapters`（0.8.0，Host `f0027c98`） | 661 passed / 34 failed；红集与 0.7.10 基线（同一树 `2db35b9c` 的 worktree + 独立 venv：660 passed / 35 failed）完全一致（`comm` 差集为空），0.8.0 额外修好 1 条（`test_start_reconcile_recover_query_close_and_schema_independence`，原断言"全新库 = v7"改为跟随 SDK fresh descriptor）。基线红集属于 effect_gate / s5a·s5b 矩阵 / task_scope_update / objective_events 等已知与本次无关的失败 |
| 新增 Host 测试 | `test_composition.py::test_start_upgrades_a_pre_audit_v7_execution_library_to_v10`：无审计表的 v7 库启动 → 7→9→10、两份备份、第二次启动两回执回放且文件字节不变 |
| 真实库副本干跑（Host venv 内 0.8.0 wheel） | 7→9→10 共 0.56 s，回放相等，重开 schema 10 |
| 后端启动冒烟（真实用户数据**副本**，`main.py` 端口 18121，`.local-test-evidence/2026-09-10/host-startup-smoke-080/`） | `/health` = ok（strategy cloud_first），日志 `startup complete`；执行库 `[7] → [7, 9, 10]`，`pre-schema-9/10.backup` 均存在，19 张 `base_agent_*` 表；日志中唯一 Traceback 是 SIGTERM 关停时 MCP stdio 客户端的 `CancelledError`（关停路径，与启动无关，见 §5） |
| SDK 侧 | `simple-harness-sdk` `dffd13c`：agents/execution/contracts 364 passed（6 基线红）；全量回归 73 红 ⊆ 基线、0 新红；干净 venv 装 wheel 242 passed；DeepSeek 真实委派链 run 9 通过 |

## 4. 原生 App 手工测试

（回填）

## 5. 遗留

| # | 事项 | 归属 |
|---|---|---|
| HL-1 | 后端 SIGTERM 关停时 `deskpet/mcp/manager.py::_teardown_runtime` 冒出 MCP stdio 客户端 `CancelledError` Traceback（关停路径，早于本次改动；启动与 `/health` 不受影响） | Host 后续观测 |
| HL-2 | Host 尚无任何调用 `simple_harness.agents`（BaseAgent）的产品路径：本次只交付 SDK 能力 + 库升级；产品接线是 taskSys2 下一阶段 | 下一阶段 |
| HL-3 | 真实执行库的正式升级在用户下次启动 App 时发生（备份 `execution-v6.sqlite3.pre-schema-9.backup` / `.pre-schema-10.backup` 与库同目录，不会被覆盖） | 用户启动时 |
