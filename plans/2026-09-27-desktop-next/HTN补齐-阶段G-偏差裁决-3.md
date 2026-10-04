# HTN 补齐阶段 G 偏差裁决 3："网络文档推出的四张表与网络文档一致"的核对

日期：2026-10-04　裁决：独立裁决员（只读代码与文档，没跑测试）　对应：偏差单 3、完成评估第四节第 1 项

## 结论：既不选 A 也不选 B——这项核对**已经有了，只是没被认出来**；不写新代码、不加用例，只改记录

## 核对事实（偏差单第 1、2 条说"没有逐行比"，与代码不符）

1. **三张表早就逐行比了，而且在两处跑。** `SDK/storage/taskgraph_history_sources.py` 的 `validate_revision_sources` 把 `method_child_occurrences`（:117）、`order_constraints`（:142）、`data_requirements`（:158）与网络文档解码结果按"整行 + JSON 原文 + 行数"精确比对（多一条、少一条、改一个字都报 `TASKGRAPH_SOURCE_INTEGRITY`）。它被调用两处：
   - **写入时**：`SDK/storage/taskgraph_store.py:256` `insert_revision_record`，与计划提交（`plan_commits.py:1221-1224` 写这几张表）同一事务——少写一条顺序约束，整笔提交直接失败，进不了库。
   - **重建时**：`taskgraph_store.py:384` `_read_one_revision`；`taskgraph_replay.py:183-195` 每次新建 `TaskGraphStore`（没有缓存）逐修订调用 `read_revision`，所以离线重建本身就逐修订核了这三张表。它已接进随机序列不变量（`T/product_world/random_sequences.py:220`）和诊断导出（`BE/diagnostics.py:394-412`）。
   - 写入后这些行在库层不许改删（迁移 41），v3 点名再兜一层。偏差单举的"少写一条顺序约束 v3 不报"在产品里走不到：写不进去。
2. **`operation_completion_scopes` 不该在这张清单里。** 它不是由网络文档推出来的投影，而是自带原文和哈希的独立源记录（`document_json`、`scope_hash`）。写入时 `operation_completion_store.py:401-528` `insert_scope` 已核：计划修订快照哈希、该步骤确在该修订的成员表里、任务合同版本与哈希、完成规格与要求版本——成员表又被第 1 条核过与网络文档一致。按 G-1 口径，它是"不可变源记录，点名 + 哈希"，v3 现在正是这样核的。施工清单 G-2 把它和另三张归成一类，是归类错了。

## 为什么不选 A、也不选 B

- **A**：在 v3 或 `taskgraph_replay` 里再写一份"四张表对网络文档"的比对，就是同一件事的第二条路径，违反"同一件事只留一条路径"；新用例与改坏守的也是已经守住的东西，属于不必要的测试。
- **B**：移交 TaskGraph 第 1 批，等于把已经做完的事写成"还没做"，会让 TaskGraph 第 1 批多出一项假工作，阶段 G 的报告也会偏保守地失真。TaskGraph 第 1 批第 6 条（诊断导出调 `replay_taskgraph`）本来就在用这条现成路径，无需新增文字。

## 要改的记录（只改文字，不改代码，不跑测试）

1. **完成评估** `HTN补齐-阶段G-完成评估.md` 第 40 行与第四节第 1 项改为："已有，偏差裁决 3 核实：三张表由 `validate_revision_sources` 在写入同事务与离线重建时逐行精确比对；`operation_completion_scopes` 是独立源记录，写入时核计划成员、任务合同与规格，v3 点名 + 哈希。施工清单把它与另三张归为一类属归类错误，已更正。"
2. **施工清单** G-2 那一条（第 286 行）后加括注："（偏差裁决 3：前三张已由 `taskgraph_history_sources.validate_revision_sources` 覆盖；`operation_completion_scopes` 改按独立源记录核，不属网络文档投影。）"
3. **台账 R37**（`需求与场景状态.md` 第 91 行）删掉"还差一项待补或待裁决……"一句，改为："由网络文档推出的顺序约束、数据需求、做法子步骤三张表，在计划提交同一事务和离线执行图重建时都与网络文档逐行比对（`SDK/storage/taskgraph_history_sources.py`，偏差裁决 3）。" 状态保持"已接入"，不升级。
4. **阶段 G 实施记录**补一句：偏差单 3 → 裁决 3，结论"已覆盖、只更正记录"。
5. **阶段 G 报告**照实写：这项"在 G 开工前就已由执行图的源记录核对覆盖，G 里没有新增代码"；不要写成"G 新做了这项核对"，也不要写成"移交 TaskGraph"。

## 不做的事

不加新用例、不加改坏、不动 `business_replay.py` 与 `taskgraph_replay.py`；不往 `TaskGraph-补全-方案.md` 加文字。阶段 G 可按现状收尾发版。
