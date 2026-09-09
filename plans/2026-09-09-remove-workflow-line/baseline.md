# 绿色基线（动手前锁定，2026-09-09）

## 代码基线

| 仓 | HEAD | 工作树 |
|---|---|---|
| Host `simple_harness` | `03de5052` | 干净（仅本 plan 目录未跟踪） |
| Harness SDK `simple-harness-sdk` | `fd12e7dd`（0.7.10 候选提交为 `031fdc68`，之后两个 docs 提交） | `.gitignore` 有未提交改动（本次不动） |
| Host 钉版 | Harness 0.7.10 / Memory 0.6.38 / Service 0.3.13 | installed target `.local-test-evidence/2026-09-07/installed-h0710-m0638-s0313` |

## 目标测试基线（Host，单进程）

命令：

```bash
backend/.venv/bin/python -m pytest -q -p no:cacheprovider tests/sdk_adapters/test_tool_catalog.py tests/test_deskpet_agent_loop.py tests/test_execution_build_manifest.py tests/test_provider_runtime_refresh.py tests/capabilities/test_failure_receipts.py tests/companion/test_skill_runtime_snapshot.py tests/test_p5s2_sse_diagnostic.py
```

结果：**2 failed / 89 passed**（前四个文件 2 failed / 65 passed；后三个文件单独实测 24 passed，挑战第 1 轮补测）。失败集合（既有环境红，与 09-09 各裁决备忘记录一致）：

- `tests/test_execution_build_manifest.py::test_checked_manifest_is_canonical_and_current`
- `tests/test_provider_runtime_refresh.py::test_human_epoch_composition_registers_three_authorities`

另核：`tests/sdk_adapters/test_product_host_ports.py`、`test_s5b_acceptance_matrix.py`、`test_tool_activate_unavailable_disclosure.py`、`test_tool_authority.py` 也消费冻结清单/名字集合，但只有相对断言、无硬编码计数，不入 AC-7 门。

## Spike 证据

| 假设 | 命令 | 实际输出 | 结论 |
|---|---|---|---|
| TRUST-2：SDK 仓 main 就是 0.7.10 wheel 的源，可重复构建 | `git archive HEAD src pyproject.toml README.md LICENSES` → `SOURCE_DATE_EPOCH=<ct> uv build --wheel`；解包两份 wheel 对比 RECORD（排除 dist-info） | RECORD diff 为空（exit 0）；整包 sha 不同（spike `0d74fdb8…` vs vendor `e559bc1b…`）仅因 SOURCE_DATE_EPOCH 不同（vendor 记录 `1788719622`，对应提交 `031fdc68`） | 成立。0.7.11 按同法构建，SOURCE_DATE_EPOCH 取新提交时间 |
| 冻结清单删一个工具后能重算哈希 | `deskpet.tool_catalog.manifest.canonical_hash` 对去掉 `workflow_spawn`、`workflows={}`、`tool_count=76` 的 raw 重算 | 现有 embedded == 常量 == 重算值（True/True）；新 sha `df979c0e044112338e0531d53e6d5906767b6ef0fbdaa312fec7d8f162790bc4` | 成立。`load_tool_manifest` 里 `tool_count != 77`、`workflows` 集合断言需同步改 |
| 交付版 provider 请求里的模型视野 | `execution-v6.sqlite3` 的 `provider_invocations.request_json` 统计（43 条） | `workflow_spawn` 0 次；catalog 提示词 0 次；tools 名字集合 14 个：context_page_in / context_route / file_write / procedure_discover / procedure_use / prospective_ack / task_scope_search / task_scope_update / todo_complete / todo_write / tool_activate / tool_describe / tool_search / write_file | 模型视野改前已无 workflow_spawn；AC-2 运行时断言是不回归断言 |
| `operation-audit.db` 的 17 处命中是什么 | 按词形拆解 | 五张 SDK 表名各 26 次，裸工具名 0 次 | 不能作 AC-2 锚点 |
| `schema_migrations.json` 删行后 `migrate_tool_schemas` 能过 | 用改后 manifest 对象 + 临时 migrations 文件真跑 | migrated 76 / records 70 / closed_object_paths 总和 71；新 sha 与上行一致 | 成立 |
| 重签的字节级往返 | `json.dumps(obj, indent=2, ensure_ascii=False) + "\n"` 与原文件比对 | 两个 JSON 均逐字节相同 | Task 1 用此格式写回 |

## 主流程冒烟基线

交付版冒烟记录：`plans/2026-09-09-two-flow-journey/SMOKE-7e64dab0-MAINFLOW.md`（流程一 6/6、重启就绪、流程二 4/4）。本次 AC-1 用同一驱动、同一 `TF_TURNS` 子集复跑。

## 执行期追加

- 2026-09-10 code review P0 修复后，回归套件新增 `tests/test_turn_preparer_static_helpers.py`（2 例）；八文件门实测 **2 failed / 91 passed**，失败集合仍为上述两条。
