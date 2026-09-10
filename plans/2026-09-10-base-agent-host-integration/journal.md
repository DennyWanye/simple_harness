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

（回填）

## 4. 原生 App 手工测试

（回填）

## 5. 遗留

（回填）
