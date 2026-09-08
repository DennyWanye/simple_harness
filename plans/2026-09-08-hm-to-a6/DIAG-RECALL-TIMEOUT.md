# DIAG：模型侧 typed recall 在负载下超时（HM-TO-A6 turn 22）

- 事件：HM-TO-A6 turn 22（`那你现在按哪个版本执行这套校对流程？`，DeepSeek，库内 11 条 memory head）
- 现象：`context_route(route=memory_standalone)` 连续 5 次返回 `context_route_recall_timeout`，Run 以 `react_repeated_tool_exceeded` 死亡
- 证据：`.local-test-evidence/2026-09-08/native-a6-b3682fe1/primary-ui-xmqudtzt/userdata/data/{operation-audit.db,human_memory_v7.db}`、`primary-ui-hv9k7ncq/native.log`（UTC 05:54–06:04）
- 复现脚本与 DB 副本：`/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/6927d19d-804c-42ea-a91d-fd3cf836f540/scratchpad/recall-diag/`
  （`replay_recall.py` = 离线重放 typed recall；`bench_embed.py` = 真实 WeMM 嵌入基准；`bench_maintenance.py` = 维护重建 DB 侧成本）
- 结论一句话：**主因在 SDK 侧**——短时域/认知世代重建把 `embed_batch` 放在 `_write_lock` 内，形成永不收敛的活锁；前台 recall 取同一把写锁时没有 deadline，1000 ms 预算在碰到第一条候选之前就被耗光。

---

## 1. deadline 取值与设置位置

| 项 | 值 | 位置 |
|---|---|---|
| typed recall 预算 deadline | **1000 ms** | Host `backend/deskpet/memory/human_memory_v7.py:424` → `RecallBudget(8, 16_384, 2_048, 1_000)` |
| 协议上限 | 2000 ms | SDK `simple_harness/runtime/memory_protocol.py:534-550`（`RecallBudget.__post_init__` 的 `deadline_ms` 最大值）；契约同 `plans/.../slices/S3-cognitive-systems-recall.md:216`（`deadline_ms=1..2000`） |
| deadline 计时起点 | `started_monotonic + budget.deadline_ms/1000` | SDK `simple_harness_memory/backends/sqlite_v5.py:3746-3747` |
| 抛 `TimeoutError("DEADLINE_EXCEEDED")` 的分支 | 3786/3791、3839-3843、3853-3857、3879-3883、3959-3963、3968、3983-3987、4003 | 同上 |
| Host 把 `TimeoutError` 映射成 `context_route_recall_timeout` | `backend/deskpet/sdk_adapters/context_route.py:361-364` | Host 侧无二次 `wait_for`；440 行的 `wait_for` 只是取消路径的审计写入（`_AUDIT_CANCEL_SECONDS=2.0`），与本次失败无关 |
| 契约中的性能边界表述 | `deadline_ms=1000`，「查询嵌入受同一 deadline 与 audit 预留约束」「**冷态只退化不失败**」 | `simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/DECISION-2026-09-07-cognitive-vector-lane.md:96` |

`RecallContext.expires_at = moment + 60.0`（`human_memory_v7.py:400` 附近）是授权有效期，不是本次超时的来源。

---

## 2. 时间都花在哪里：逐阶段数字

### 2.1 现场审计（`operation-audit.db.memory_call_attempts`，UTC）

| 起始 | 结束 | 耗时 | state | caller | turn_ordinal |
|---|---|---|---|---|---|
| 05:57:11.867 | 05:57:13.310 | 1.443 s | raised | foreground_recall | 1 |
| 05:57:17.017 | 05:57:20.523 | 3.505 s | raised | foreground_recall | 2 |
| 05:57:23.571 | 05:57:28.177 | 4.606 s | raised | foreground_recall | 3 |
| 05:57:38.535 | 05:57:43.330 | 4.795 s | raised | foreground_recall | 5 |
| 05:57:55.784 | 05:57:58.550 | 2.766 s | raised | foreground_recall | 8 |
| 对照 05:40:23 / 05:52:44 / 05:55:21 | | 0.518 / 0.906 / 0.458 s | returned | foreground_recall | — |
| 对照 discover_procedure_drafts 05:40:44 | | 1.426 s | returned（bound） | | |
| 对照 analysis_candidates ×24 | | 0.003–0.177 s | returned | | |

**关键**：`human_memory_v7.db.typed_recall_terminals` 里这 5 条终态全部是
`terminal_kind='deadline_exceeded'`、`candidate_query_started=0`、`candidate_query_count=0`、`degradation_codes=[]`。
→ **一条候选都没扫过就超时了**；向量扫描 / 词面扫描 / 排序 / 预算裁剪根本没执行。
（成功的 3 次对照是 `candidate_query_started=1, candidate_query_count=1`。）

5 次都是不同的 `turn_ordinal`（1/2/3/5/8，中间 4/6/7 是 `tool_arguments.missing`），
`idempotency_key` 各不相同 → **不是幂等重放**，每次都是真实新执行、真实超时。

### 2.2 离线重放：纯 DB 侧成本（`replay_recall.py`，嵌入置零，真实 `human_memory_v7.db` 副本）

同一条 turn-22 的 context/plan（query `校对脚本 Python 版本 执行 校对流程`，`memory_types=['semantic']`、`include_short_horizon=False`、`modes=[full_text, vector]`）：

| 阶段 | run0 | run1 | run2 |
|---|---|---|---|
| `_admit_typed_recall_request`（含写锁） | 1.2 ms | 1.3 ms | 1.0 ms |
| `prepare_history_source_context` | 0.0 ms | 0.0 ms | 0.0 ms |
| `_prepare_cognitive_vector_lane`（含写锁，嵌入置零） | 0.9 ms | 1.6 ms | 1.5 ms |
| `_collect_typed_recall_confirmation` | 0.3 ms | 0.2 ms | 0.2 ms |
| `_collect_typed_recall_candidates`（向量+词面扫描+落库） | 19.1 ms | 13.5 ms | 13.8 ms |
| **端到端** | **30.5 ms** | **23.8 ms** | **26.0 ms** |

结果 `items=2, degradation_codes=()`。**DB 侧总成本约 24–31 ms，占 1000 ms 预算的 2–3%。**

### 2.3 真实嵌入器成本（`bench_embed.py`，WeMM-Embedding-2B / mps / 本机）

| 项 | 耗时 |
|---|---|
| 冷加载 load | 10.6 s（现场 native.log 为 7.56 s） |
| prime（固定短句，首次编码） | 1.58 s（现场 1.03 s） |
| **查询向量 embed（22 字，热态）** | **206 / 212 / 208 / 237 ms** |
| 查询向量 embed（warmup 后第一次） | 1033 ms |
| 短时域 chunk 逐条（len=54 / 108 / 1682 / 7798 / 19655 / 29778） | 912 / 2430 / 3038 / 5121 / 10304 / **23885** ms |
| **`rebuild_short_horizon_generation` 的 `embed_batch`（6 chunk）合计** | **45 690 ms** |

`WeMMEmbedder` 没有覆写 `embed_batch`，走 SDK 基类默认实现
（`simple_harness_memory/embedders/base.py`：`[await self.embed(t) for t in texts]`），
即 **N 条文本 = N 次串行模型调用**，无真正批处理。

### 2.4 维护重建的 DB 侧成本（`bench_maintenance.py`，嵌入置零）

| 调用 | 耗时（DB 侧） |
|---|---|
| `rebuild_short_horizon_projection`（持写锁） | 523 / 487 ms |
| `rebuild_short_horizon_generation`（持写锁，嵌入置零） | 13 / 4 ms |
| `rebuild_cognitive_vector_generation`（持写锁，replay 命中） | 5 / 7 ms |

→ 维护 tick 的真实成本 ≈ 0.5 s（projection）+ **45.7 s（generation 的 embed_batch）**，
而 Host 给这段的超时只有 `PrimaryShortIndexWorker.operation_timeout = 5.0 s`
（`backend/deskpet/memory/short_index_worker.py:26`、110-118 行的 `async with asyncio.timeout(...)`）。

---

## 3. 5 次失败发生时，写锁被谁持有？

**不是 analysis 批次。** 证据：

- `native.log` 中 `memory.committed_turn.applied` / `memory_outbox.applied`（analysis 应用路径，走 `apply_memory_mutation_plan` 持写锁）最后一次在 **05:56:07.414**，失败窗口 05:57:11–05:57:58 内没有任何一次。
- `human_memory_v7.db.cognitive_memory_revisions` 最后一次写入 **05:41:33**；`operation-audit.db.audit_jobs` 在窗口内无作业（前一条 05:56:02.747，后一条 05:58:56.133）。
- 窗口内唯一的 provider HTTP 调用全部是前台 DeepSeek（`api.deepseek.com`），analysis lane（`sf-glm-5.2`）没有并发调用。

**真正的持有者是短时域索引维护 tick**（与 analysis 同一个 `MemoryAnalysisLane.tick`：outbox → `short_indexer.step()` → analysis runner，`backend/deskpet/memory/memory_ingestion_outbox.py:437-457`）：

```
05:54:36.063  memory_short_index_unavailable type=TimeoutError     ← 从这里开始，每个 tick 都超时
05:54:50.773  ...（此后到 06:04 一直如此，永不恢复）
05:57:13.303  memory_short_index_unavailable   →  05:57:13.310 recall #1 raised   （相差 7 ms）
05:57:20.514  memory_short_index_unavailable   →  05:57:20.523 recall #2 raised   （相差 9 ms）
05:57:28.170  memory_short_index_unavailable   →  05:57:28.177 recall #3 raised   （相差 7 ms）
05:57:58.542  memory_short_index_unavailable   →  05:57:58.550 recall #5 raised   （相差 8 ms）
```

recall 的 settle 时刻与维护 tick 释放写锁的时刻**相差 7–9 ms**：
recall 一直阻塞在 `async with self._write_lock`，维护 tick 的 5 s 超时一放锁它立刻拿到锁，
然后第一行 `if time.monotonic() >= deadline_monotonic: raise TimeoutError`（`sqlite_v5.py:3015-3017`）当场抛出。

配套的活锁证据：
- `short_horizon_audit` 从 05:54 起每 ~7.7 s 写一行 `projection_rebuilt`（`registration_count=147`，chunk manifest 恒为 `8afc4de4…`），直到日志结束；
- `short_horizon_generations` 最后一次激活停在 **05:53:06.751**（content_hash `1bd900e0…` ≠ 当前 manifest `8afc4de4…`）；
- `cognitive_vector_audit` 最后一行停在 **05:54:12.548** —— 认知世代重建排在短时域世代之后，永远轮不到；
- `short_horizon_vectors` 表为 0 行（6 条 chunk 一条向量都没写成）；
- 因此成功的对照 recall 也已经在报 `cognitive_vector_stale`（05:40:23）/ `STALE_ACTIVE_GENERATION`（05:55:21）。

---

## 4. 根因

**一句话：SDK 的世代重建在写锁内做嵌入，且嵌入成本随会话长度爆炸，导致维护 tick 永远超时并周期性霸占写锁；前台 typed recall 取写锁时没有 deadline，1000 ms 预算在做任何检索工作之前就被耗尽。**

因果链：

1. 会话变长 → 短时域 chunk 变长（本次 6 条 chunk 分别 54/108/1682/7798/19655/**29778** 字符，无长度上限）。
2. `rebuild_short_horizon_generation`（`sqlite_v5.py:2382` 取 `_write_lock`，**2428 行在锁内 `await embedder.embed_batch(...)`**）需要 45.7 s；
   `rebuild_cognitive_vector_generation` 同型（`:2553` 取锁，`:2634` 锁内 `embed_batch`）。
   `WeMMEmbedder` 未覆写 `embed_batch` → 串行 N 次模型调用，无批处理。
3. Host `PrimaryShortIndexWorker` 给这段的超时是 5 s → **永远来不及激活新世代** → chunk manifest 永远对不上 →
   下一个 tick 从头再来，形成 **活锁**：每 ~7.7 s 就有一段 ≥5 s 的写锁占用（占空比约 65%）。
4. 前台 typed recall 需要两次取同一把 `_write_lock`，且**两处都没有 deadline**：
   - `_admit_typed_recall_request`（`sqlite_v5.py:5053`）
   - `_prepare_cognitive_vector_lane` 开头（`sqlite_v5.py:3015`），拿到锁后第一件事就是 `if time.monotonic() >= deadline_monotonic: raise TimeoutError`
   落进上面那 65% 的占空比里就必然 `DEADLINE_EXCEEDED`，且 `candidate_query_started=0`。
5. Host 把它翻译成 `context_route_recall_timeout`（可重试的工具失败），模型连续重试 5 次 → `react_repeated_tool_exceeded`，Run 死亡。

**次要放大因素（Host 侧）**：
- `deadline_ms=1000` 本身过紧：热态查询嵌入 206–237 ms、warmup 后首次 1033 ms，而 DB 侧只要 24–31 ms。
  预算的 20%–100% 被单次嵌入吃掉，没有任何抗抖动余量。
- 短时域 chunk 文本无长度上限，嵌入耗时随会话长度非线性增长（29778 字符单条 23.9 s）。
- `PrimaryShortIndexWorker` 超时后没有退避 / 断路 / 部分进度，下一 tick 以全额成本重试；
  且 `asyncio.timeout` 取消不了已经进入 `asyncio.to_thread` 的物理 encode，写锁的实际释放还会再拖一段。
- 该设计违反了 SDK 自己写下的契约：`DECISION-2026-09-07-cognitive-vector-lane.md:62`「**不在 mutation 写锁内嵌入**」、
  `:65`「查询向量在**取 `_write_lock` 之前**算」、`:96`「冷态只退化不失败」。实现全部反了。

---

## 5. 建议修法（按优先级）

### P0 — SDK：把 `embed_batch` 移出 `_write_lock`（真正的根因）
`rebuild_short_horizon_generation`（`sqlite_v5.py:2368-2470`）与
`rebuild_cognitive_vector_generation`（`:2528-2700`）改成三段式：
① 持锁读 rows + 算 manifest_hash + 判 replay → ② **释放锁**做 `embed_batch` → ③ 重新取锁，
校验 manifest_hash 未变（乐观 CAS，变了就丢弃本次嵌入结果并让下一 tick 重来）再写表/激活。
这正是 `_prepare_cognitive_vector_lane` 对查询向量已经做的事，也是决策文档 §4.2 明确写下的不变量。
**契约约束**：世代激活必须仍然原子、旧世代 retire 不变、audit 行不变；CAS 失败必须是"本次不激活"而不是错误。

### P0 — Host：给 `WeMMEmbedder` 一个真正的 `embed_batch`
一次 `model.encode(texts)` 代替基类的串行循环，并对超长文本做截断/分片（`max_seq_length` 之上的部分本来也不进模型，
但 tokenize + 单条 30k 字符的开销是实打实的 23.9 s）。同时在 projection 侧给 chunk `public_text` 设长度上限或再分块。

### P1 — SDK：recall 取写锁必须带 deadline
`_admit_typed_recall_request` 与 `_prepare_cognitive_vector_lane` 改用
`await asyncio.wait_for(self._write_lock.acquire(), timeout=max(0, deadline_monotonic - now))`。
其中**向量 lane 的锁等待超时应当退化**（`cognitive_vector_deadline`）而不是硬失败——
词面 lane 只要 13–19 ms，完全来得及，这样才符合 `:96`「冷态只退化不失败」。
`_admit` 的锁等待超时仍可判为 `DEADLINE_EXCEEDED`（它必须先落幂等记录）。

### P1 — Host：`PrimaryShortIndexWorker` 加退避与断路
维护重建连续超时 N 次后指数退避（例如 15 s → 60 s → 300 s）并降级上报，避免以 65% 占空比长期霸占 SDK 写锁；
`operation_timeout` 与「一次 tick 能承载多少嵌入」两个常量用一致性用例绑死
（参考 `increments/2026-09-02-s5b-effect-closure-memory/acceptance.md:214-221` 已有的「预算 × 实测 ms/token ≤ deadline」体例）。

### P2 — Host：调高 recall deadline，但这**不是**修复
`RecallBudget(8, 16_384, 2_048, 1_000)` → `2_000`（协议上限，`S3-cognitive-systems-recall.md:216`）。
必要但远不充分：本次 5 次超时分别超出 1.44/3.5/4.6/4.8/2.77 s，2000 ms 一样全挂。
只有在 P0 落地之后，这个调整才是给嵌入抖动留的余量。

### P2 — Host：`context_route_recall_timeout` 不应该让 Run 死
`backend/deskpet/sdk_adapters/context_route.py:361-364` 目前返回可重试的工具失败，模型连试 5 次即触发
`react_repeated_tool_exceeded`。建议要么改成"带降级码的成功返回（无记忆片段，提示继续）"，
要么标记为不可重试并引导模型改走 `direct_standalone`——一次记忆抖动不应该杀掉整个 Run。

---

## 6. Host 侧还是 SDK 侧

| 缺陷 | 侧 | 位置 |
|---|---|---|
| 世代重建在 `_write_lock` 内做 `embed_batch`（根因） | **SDK** | `simple_harness_memory/backends/sqlite_v5.py:2382+2428`、`:2553+2634` |
| recall 取写锁无 deadline，拿到锁即抛超时 | **SDK** | `sqlite_v5.py:3015-3017`、`:5053` |
| `WeMMEmbedder` 无批处理 `embed_batch` | **Host** | `backend/deskpet/memory/wemm_embedder.py`（缺 `embed_batch` 覆写） |
| 短时域 chunk 文本无长度上限 | Host/SDK 交界 | Host 注册侧 + SDK `rebuild_short_horizon_projection` |
| 维护 tick 超时后无退避、全额重试 | **Host** | `backend/deskpet/memory/short_index_worker.py:26,110-127` |
| `deadline_ms=1000` 过紧 | **Host** | `backend/deskpet/memory/human_memory_v7.py:424` |
| 一次 recall 超时导致 Run 死亡 | **Host** | `backend/deskpet/sdk_adapters/context_route.py:361-364` |
