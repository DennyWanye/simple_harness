# 决策备忘：A6-5「EVIDENCE 事件数」把跨 scope、跨水位的两笔账当成一笔（事件 AC）

> 日期：2026-09-09
> 义务：`HM-TO-A6`（A6-5「README/STATUS 超限拆分」；plan 第 4 节 FAIL 条件
> 「README/STATUS 超限后 EVIDENCE 里 canonical 事件数变少」）
> 证据：`.local-test-evidence/2026-09-09/native-a6-run11/primary-ui-9izlp1ao/userdata/data/state.db`
> （第 11 次原生真机跑，`deepseek-v4-flash`，window 32000）；
> 验证器 JSON `plans/2026-09-08-hm-to-a6/RUN-11-a6-verify.json` item `A6-5`
> 分支：`worktree-evidence-view-count`（自 `c15a0704` 起）
> 结论：**(a) 事件在最后一条视图修订之后才追加，判据应当在视图水位上比**，
> 并且旧判据还多错了一层：它把**两个 TaskScope** 的事件数加在一起比。
> 投影没有 bug，一条 canonical 事实都没丢。

---

## 1. 事故

第 11 次头一回把 ≥16 KiB 的逐字目标（T17，事件 U）顶进了 bounded 视图：

```
view_revisions=24; readme_bounded_revisions=1; readme_bounded_max_bytes=16383(< 16384 上限);
status_bounded_revisions=1
→ FAIL: EVIDENCE 视图 event_count=151 与 task_scope_events 实际 176 行不等, canonical 事实丢失。
```

`evidence_event_count_values=[125, 20, 151]`，`task_scope_events_rows=176`。

## 2. 取证：逐行读证据库

### 2.1 176 行不属于同一个 TaskScope

```sql
select task_scope_id, count(*) n, min(event_sequence), max(event_sequence)
  from task_scope_events group by task_scope_id;
```

| `task_scope_id` | 行数 | `event_sequence` 区间 |
|---|---:|---|
| `870a401b-e019-5423-a1ec-8058eab7571b` | 151 | 1 … 151 |
| `3a5016d0-c365-5cb7-a3ef-62ce8e69bd69` | 25 | 1 … 25 |
| **整表** | **176** | — |

### 2.2 四条 EVIDENCE 视图修订，每一条的计数都正好等于自己的水位

`task_scope_read_view_revisions`（`view_kind='EVIDENCE'`，按 `created_at` 升序）
与它们各自 `source_id` 在 `task_scope_projection_sources` 里的 `event_watermark`：

| # | `created_at` | `task_scope_id` | `source_id` | 视图 `event_count` | source `event_watermark` | 该水位下的事件行数 |
|---:|---|---|---|---:|---:|---:|
| 1 | 1788920717.781 | `870a401b…` | `d2716bdd…` | 117 | 117 | 117 |
| 2 | 1788920804.681 | `870a401b…` | `3de42c24…` | 125 | 125 | 125 |
| 3 | 1788921011.142 | `3a5016d0…` | `2f8c70c3…` | **20** | **20** | **20** |
| 4 | 1788921198.708 | `870a401b…` | `52c9d836…` | 151 | 151 | 151 |

`_load_model_tx` 取事件的条件就是
`WHERE task_scope_id=? AND event_sequence<=source.event_watermark`，
`event_count=len(events)`；四条**逐条对齐**，没有任何一条在自己的水位下少算。

### 2.3 缺的 25 条 = 另一个 scope 的 25 行；而这个 scope 的视图停在水位 20

176 − 151 = 25，正好是 `3a5016d0` 的全部行数——旧判据拿 `870a401b` 的最后一条视图
去和「两个 scope 之和」比，这是**跨 scope**的第一层错。

`3a5016d0` 自己也有第二层错。它的视图在 `created_at=1788921011.142` 物化，水位 20；
而 21..25 这五行的 `occurred_at` 是：

| `event_sequence` | `event_kind` | `occurred_at` |
|---:|---|---|
| 20 | `harness.provider_invocation` | 1788921005.992 |
| 21 | `harness.tool_invocation` | **1788921011.060** |
| 22 | `harness.tool_invocation` | 1788921011.358 |
| 23 | `harness.context_snapshot` | 1788921011.716 |
| 24 | `harness.provider_invocation` | 1788921011.805 |
| 25 | `harness.run_terminal` | 1788921031.660 |

第 21 条（`1788921011.060`）比视图的 `created_at`（`…011.142`）**还早 80 毫秒**：
它就是那次「读视图」的工具调用本身。视图按读取时物化（第 4 次教训已写进 plan 第 127 行），
**触发物化的那次读取，以及它之后的一切，天然落在水位之外**。拿跑完之后的事件表行数
去比一条水位 20 的视图，永远差着这几条。

### 2.4 排除 (b) 与 (c)

- **(b) 视图刻意排除某类事件**：不成立。`_load_model_tx` 不按 `event_kind` 过滤，
  `route_decision` / `provider_invocation` / `closure` 全在里面；上表逐条相等即证。
- **(c) bounded 路径截断后按截断切片计数**：不成立。`event_count` 来自
  `_render_tx` 里的 `len(events)`（archive 侧），`_bounded_view` 只在最后对**字符串字节**
  做截断，碰不到计数；而且本轮 EVIDENCE 清单只有 466/467 字节，离 16384 差得远，
  根本没进 bounded 分支。（真进了也只会整段换成 `bounded` 存根、清单里连 `event_count`
  都没有 —— 那是 INCONCLUSIVE，不是错数。）

## 3. 裁决与修复

**判据错，投影对。** 按任务书 (a) 支：`event_count` 的定义就是「该视图水位上的
canonical 事件数」，验证器必须在同一水位、同一 `task_scope_id` 上比。

1. **视图自述水位**（`backend/deskpet/task_scope/projections.py`）：EVIDENCE 清单新增
   `event_watermark`，与 `event_count` 并排。这样「计数不丢」这条规则可以只靠视图自身
   证明，读者/验证器不必回查 `task_scope_projection_sources`。
2. **渲染契约版本 v1 → v2**（`projection_sources.py`）：渲染输出变了就换版本。版本进
   `source_hash` → 新 `source_id` → 新 `view_revision_id`，于是老 source 的视图行不会被
   新渲染器的内容用 `INSERT OR IGNORE` 悄悄顶掉，`read_view` 与 `read_materialized_view`
   不会一个新一个旧。视图指纹/回执仍是 source 的纯函数，重建结果逐字节可复现
   （`test_projections_search.py::test_source_views_groups_rebuild_and_checkpoint_drift`
   的「删块重建、块哈希不变」用例照旧通过）。
3. **验证器 A6-5**（`scripts/native/a6_verify.py`）逐条视图核对：
   水位优先取视图自述的 `event_watermark`，老库回落到该视图 `source_id` 的
   `task_scope_projection_sources.event_watermark`；期望值是
   `count(task_scope_events where task_scope_id=? and event_sequence<=水位)`。
   另加一条牙齿：同一 scope 内**水位只进不退时 `event_count` 不许降**（plan 第 4 节
   点名的 FAIL 条件，在拿不到 `event_sequence` 的库上仍然有效）。
   两者都无从判定时记 INCONCLUSIVE，**不再拿整表行数硬比**。
   `numbers` 新增 `evidence_watermark_checks` 与 `task_scope_events_rows_by_scope`，
   整表的 `task_scope_events_rows` 保留但只作背景数字。
4. **plan 口径同步**：`00-PLAN.md` A6-5 行与第 4 节 FAIL 条件补上「同一 TaskScope、
   在该视图水位上」。

按新判据复算第 11 次的四条视图：117/125/20/151 全部等于各自水位下的行数
→ **A6-5 = PASS**（README 1 次 bounded ≤16384B，STATUS 1 次 bounded）。

## 4. 测试

- `backend/tests/native/test_a6_verify_a6_5.py`（5 例，**在 `main@c15a0704` 上 5 红**）：
  第 11 次两 scope 原形转绿；真丢事实（水位 151 只报 149）仍 FAIL；水位推进而计数倒退
  仍 FAIL；老库回落 source 水位仍能判定；水位无从定位时 INCONCLUSIVE 而不是硬比。
- `backend/tests/task_scope/test_projections_evidence_watermark.py`（3 例）：
  水位钉死语义（物化后再追加 5 条，已落库的修订仍只说 20，且 20 == 水位下行数 ≠ 当前 25 行）；
  两 scope 各自计数不相加（151 / 25，整表 176）；
  **16384 边界**——正好 16384 字节不截断，16385 字节走 bounded 且 ≤16384，
  两边 EVIDENCE 的 `event_count` 都仍等于 archive 在水位下的行数。

回归（同一 venv，单进程逐个文件）：

| 范围 | 结果 |
|---|---|
| `tests/task_scope/{test_projections_search,test_canonical_archive,test_provisioning,test_search_access_receipts}.py` | 34 passed |
| `tests/native/`（6 个文件，含新增） | 70 passed |
| `tests/memory/test_human_memory_service.py` 等 5 个下游读视图消费者文件 | 88 passed |
| `tests/sdk_adapters/test_s5b_acceptance_matrix.py` | 9 failed / 13 passed，与 `main@c15a0704` 基线**逐条相同**（既有红，与本次改动无关） |
