# DECISION：typed recall 超时的 Host 侧修法（HM-TO-A6）

- 上游诊断：`plans/2026-09-08-hm-to-a6/DIAG-RECALL-TIMEOUT.md`
- 分支：`worktree-recall-host-side`（基线 `188394b1`）
- 装机版本：`simple-harness-memory-sdk 0.6.26`；SDK 侧「把 `embed_batch` 移出 `_write_lock`」由另一路 agent 在 0.6.27 处理，本文只做 Host 侧，不在 Host 绕过它
- 涉及文件：`backend/deskpet/memory/wemm_embedder.py`、`backend/deskpet/memory/short_index_worker.py`、`backend/deskpet/memory/human_memory_v7.py`
- **不动** `backend/deskpet/sdk_adapters/context_route.py`（另有属主）

---

## 0. 一句话

Host 侧只能做三件事：把嵌入批处理化到**确实更快**的那部分、让维护重建有一个能装下真实成本的超时并在失败后退避、把前台 deadline 抬到协议上限留抖动余量。**短时域 chunk 文本的长度上限不在 Host**，只能记录并交回 SDK/契约侧。

---

## 1. `WeMMEmbedder.embed_batch`：实测驱动的分组批处理（已实现）

`backend/deskpet/memory/wemm_embedder.py`

### 事实

`WeMMEmbedder` 原先没有覆写 `embed_batch`，走 SDK 基类
`simple_harness_memory/embedders/base.py` 的 `[await self.embed(t) for t in texts]`，
即 N 条文本 = N 次串行模型调用。

### 本机实测（WeMM-Embedding-2B / mps，**两条路径都预热之后**比稳态）

| 输入 | 批处理 | 基类串行 | 比值 |
|---|---|---|---|
| 6 条 20–58 字符 | 322 ms | 1 430 ms | **0.22** |
| 8 条 600 字符（等长，无 padding 浪费） | 2 014 ms | 2 192 ms | 0.92 |
| 54 / 108 / 1682 三条塞进**同一次**调用 | 2 084 ms | 1 151 ms | **1.81（更慢）** |

**关键发现（与 DIAG §5 的原始判断不同）**：一次 `encode` 会把批内所有输入
**补齐到最长的一条**，成本随 `最长 × 条数` 走，而不是长度之和。所以
"一次 `model.encode(texts)` 替掉串行循环"这个朴素修法，在现场那种
54/108/1682/7798/19655/29778 的异构分布上实测**比串行慢 1.39 倍**——
三条短 chunk 被补齐到 7 798 各付了一遍。

DIAG §2.3 里"54 字符也要 912 ms"的读数是**冷态**首次编码；预热后单条短文本
约 230 ms，其中绝大部分是每次调用的固定开销。批处理省的正是这一份固定开销，
**且只在最长一条足够短、算力开销还没占主导时才省得到**。

### 决定

按 `最长 × 条数 ≤ _BATCH_MAX_PADDED_CHARS(=1024)`、`条数 ≤ 32` 分组；
按长度**升序**访问位置（稳定排序 `(长度, 位置)`），让短的和短的凑一组；
结果按输入位置回填。由此：

- 同型短 chunk（正常会话）走一次调用 → 实测 4.4 倍加速；
- 任何一条超过该区间的 chunk **独占一次调用** → 与今天的串行成本完全一致，
  **结构上不可能比它替换掉的串行循环更慢**；
- 分组与输出顺序都只由输入的长度/位置决定，与输入排列无关（确定性）。

复核实测（分组生效后，两路径都预热）：现场 6 条 0.93、短三条 0.82、6 条短文本 0.22。

### 等价性

真模型对照（`-m model_required`）：批量与逐条 cos ≥ 0.99988、逐元素差 ≤ 0.0018、
模长 0.9986–1.0027；逐条路径本身逐位确定（两次完全相同），所以这点偏差只来自
批内 padding 与批矩阵乘的 bf16 噪声。用例容差按实测最坏值留约 3 倍余量
（cos ≥ 0.9995、逐元素 ≤ 5e-3、模长 ≤ 1e-2），不是凑一个刚好能过的数。

### 用例

`backend/tests/memory/test_wemm_embed_batch.py`
- 默认（假模型）：一个分组 = 一次物理 encode、输出顺序 = 输入顺序、
  分组与输入排列无关、长 chunk 独占调用、条数上限、空/非法输入、
  输出条数/维度校验、取消时仍占住物理队列不被插队。
- `model_required`：与逐条嵌入的等价性 + 顺序；≥6 条输入的亚线性耗时
  （阈值 0.7，实测 0.21–0.23）。权重缺失时 `pytest.skip`（沿用 G5 纪律：
  宁可 skip，也不用 mock 假装验证过真模型）。

---

## 2. 短时域 chunk 文本长度上限：**不在 Host，记录并交回 SDK**（未实现）

### 追踪结果

chunk 的 `public_text` **不是** Host 拼的：

1. Host 只把每条会话证据注册进去。注册元数据里的 `public_text_json_pointer /
   public_text_hash` 由 **Harness runtime** 派生：
   `backend/deskpet/memory/conversation_registration.py:300` 调
   `simple_harness.runtime.evidence_protocol.authorize_conversation_public_text`
   → `_derive_authorized_public_text_binding`（同文件 `:1544`），从已受理信封的
   sanitized payload 指针处取文本并算哈希。
2. SDK 读回时用
   `resolve_authorized_public_text(envelope.sanitized_payload, metadata)`
   （`simple_harness_memory/backends/sqlite_v5.py:1970`），并在一致性校验里断言
   `sha256(public_text) == metadata.public_text_hash`（`:17342`）。
3. **chunk 文本由 SDK 拼**：`rebuild_short_horizon_projection` 里
   `content = "\n".join(f"{role}: {public_text}")`（`:2164`），一个因果组一条
   chunk，**没有任何长度上限**；`content_hash` 与 `chunk_id` 都是该 content 的
   内容寻址哈希（`:2186-2196`），一致性校验会再核一遍（`:17443`、`:17489`）。

### 决定：Host 不实现，理由是「实现了就是破坏契约」

- 在 Host 截断注册的 `public_text` → 与 Harness 派生的 `public_text_hash` 对不上，
  SDK 的绑定校验 fail-closed（`sqlite_v5.py:17342`）。
- 在 Host 截断进入信封的原始文本 → 那不是"限长"，那是**篡改会话证据**：
  同一份文本还是主对话与审计的来源。
- chunk 的切分边界（一个因果组一条 chunk）完全在 SDK 的 projection 里，
  Host 没有任何注入点。

因此**上限/再分块属于 SDK 的 S5 短时域契约**，需要在 SDK 侧做：
对同一因果组按长度上限切成多条 chunk，每条仍是内容寻址（未变化的 chunk
`chunk_id`/`content_hash` 保持不变，只有被切开的那条产生新 id），
`short_horizon_chunk_evidence` 的 `item_ordinal` 映射同步扩展。
S5/S3 契约文本里目前**没有**任何 chunk 长度上限条款
（`slices/S3-cognitive-systems-recall.md:97` 只写"对超过最近 10 causal groups 且
age≤5 days 的 immutable evidence 建 chunk；保留 roles/order/task/entity/time/source refs"），
所以这是一条需要**新增**的契约条款，而不是实现漏做。

### Host 侧已做的补偿

第 1 节的分组批处理保证"一条 29 778 字符的 chunk 独占一次调用"，
不会再把它的成本乘到邻居头上；但它**消不掉**这条 chunk 自身 23.9 s 的编码成本。
现场 6 条 chunk 的重建成本因此仍在 27 s 量级——**这就是必须由 SDK 限长才能真正解决的部分**。

---

## 3. 维护 worker 退避 + 超时与实测成本绑定（已实现）

`backend/deskpet/memory/short_index_worker.py`

### 事实

- 维护重建（projection + short 世代 + 认知向量世代）与证据扫描/注册**共用**
  `operation_timeout = 5.0 s`（原 `:26`）。
- 真实成本：projection DB 侧 523/487 ms；世代重建的 `embed_batch` 冷态 45 690 ms
  （DIAG §2.4）、预热稳态 27–29 s。
- 5 s 永远来不及 → 世代永不激活 → chunk manifest 永远对不上 → 下一 tick 全额重试，
  形成活锁：每 ~7.7 s 一段 ≥5 s 的 SDK 写锁占用（占空比约 65%）。

### 决定

1. **拆常量**：新增 `maintenance_timeout`（默认 60 s）只管维护重建；
   `operation_timeout` 仍是 5 s，只管毫秒级的扫描/注册。
   合用一个常量正是上一轮活锁的直接原因；S5b 的教训是"两个常量不得各改各的"，
   不是"两件不同量级的事必须共用一个常量"。
2. **指数退避**：连续失败 `n` 次后延迟 `min(600, 60 × 2^(n-1))` 秒
   （60 → 120 → 240 → 480 → 600 封顶），成功即清零，换 manager 即清零。
   退避基数取 `≥ maintenance_timeout`，保证连续失败时写锁最坏占空比 ≤ 50%
   并随失败次数指数下降；退避基数若小于超时，抬高超时反而会让占用更久。
3. **退避只停维护重建，不停扫描/注册**——否则一次重建故障会让整条短时域索引
   链路停摆，而不只是延后一次世代激活。
4. **payload-free 审计**：每个被跳过的 tick 写一行
   `memory_short_index_maintenance_skipped failures=N retry_in_s=T`；
   每次失败写一行 `memory_short_index_maintenance_backoff failures=N delay_s=T type=<异常类型名>`。
   只有稳定码 + 计数/时长 + 异常**类型名**，没有 run id、证据文本、异常消息
   （沿用同模块 `memory_short_index_unavailable` 的口径，但连 `str(exc)[:200]` 都不带）。
5. `ShortIndexStep` 增补 `maintenance_skipped / maintenance_failures /
   retry_after_seconds` 三个可观测字段（带默认值，位置构造不受影响）。

### 一致性用例（体例同 `increments/2026-09-02-s5b-effect-closure-memory/acceptance.md:214-221`）

`backend/tests/memory/test_short_index_backoff.py`

```
SHORT_INDEX_MEASURED_PROJECTION_MS        = 600.0     # 实测 523/487 取上界
SHORT_INDEX_MEASURED_GENERATION_EMBED_MS  = 45_690.0  # DIAG §2.4 实测（冷态串行，上界）
断言：maintenance_timeout × 1000 ≥ 二者之和   → 60 s ≥ 46.3 s
断言：backoff_seconds ≥ maintenance_timeout   → 占空比 ≤ 50%
断言：backoff_cap_seconds ≥ backoff_seconds
断言：operation_timeout < maintenance_timeout → 两个常量不得再合回去
```

用例断言的是 **`MemoryAnalysisLane.__init__` 生产装配出来的那个 worker**
（`memory_ingestion_outbox.py:429`），不是 `inspect.signature(...).default`
——这正是 S5b 评审 F-05 抓到的假绿。改实测常量而不同步改超时，用例必红。

保留 45 690 ms（冷态串行）而不是 27 000 ms（预热批处理）作为实测上界：
一次真实的维护 tick 完全可能是冷态首跑，用更大的那个数才是保守的。

### 受影响的既有用例

`backend/tests/memory/test_short_index_generation.py` 里三处原本编码"失败后立刻
全额重试"的断言按新语义更新（推进假时钟越过退避窗口）；其不变量
（不产生假确认、`_generation_pending` 保持、`batches` 计数、共享冷加载不重复）
全部保留。其中
`test_cold_shared_load_survives_bounded_worker_timeouts_without_confirmation`
的保证实际上**变强了**：并发的第二个 tick 现在连尝试都不会发生（`attempts == 1`），
而不是原来的两次全额重试。

---

## 4. 前台 recall deadline 1000 → 2000 ms（已实现）

`backend/deskpet/memory/human_memory_v7.py:424` →
`RecallBudget(8, 16_384, 2_048, 2_000)`

### 契约核对（S5 recall 观察切片 + 装机协议）

- `slices/S3-cognitive-systems-recall.md:216`：`RecallBudgetV1` 范围
  `deadline_ms=1..2000`。
- 同文件 `:105`：验证口径 **`p95≤500ms / hard deadline 2s`** —— 2 s 是契约明写的
  硬上限，不是我们自己挑的数。
- 装机协议 `simple_harness/runtime/memory_protocol.py:533-550`
  （`.local-test-evidence/2026-09-07/installed-h0710-m0626-s0313` 与 venv 中的
  0.6.26 一致）：`RecallBudget.__post_init__` 的 `deadline_ms` 最大值 2 000，
  超出 `ValueError`。

**结论：允许，取协议上限 2000 ms。**

### 理由与限度

实测（DIAG §2.2/§2.3）：DB 侧端到端 24–31 ms；查询嵌入热态 206–237 ms、
warmup 之后第一次 1033 ms。1000 ms 的预算里单次嵌入就能吃掉 20%–100%，
抗抖动余量为零，warmup 后的第一次前台召回**结构性必挂**。
2000 ms 覆盖 1033 + 31 并留约 1.9 倍余量。

**这不是修复**：本次 5 次超时分别超出 1.44 / 3.5 / 4.6 / 4.8 / 2.77 s，
2000 ms 一样全挂。只有 SDK 把嵌入移出写锁（0.6.27）之后，这个值才是
给嵌入抖动留的余量。抬到上限也意味着**没有下一格**了。

### 范围

只改前台 `foreground_recall` 的预算。`backend/deskpet/memory/semantic_correction.py:164`
的 analysis 候选查询保持 1000 ms：它只走 `FULL_TEXT`、实测 0.003–0.177 s、
且在后台 lane 上，抬高只会拖长后台作业，不解决任何已观测问题。

### 用例

`backend/tests/memory/test_recall_budget_deadline.py`：捕获**真实 `typed_recall`
装配出来的 `RecallContext`**（而不是读源码常量），断言
`deadline_ms == 2000`、`plan.budget == context.budget`、其余三维不变、
`deadline ≥ 实测最坏前台成本(1064 ms)`、`deadline ≤ 协议上限`，
并对装机协议直接验一次 2001 被拒。

---

## 5. `context_route_recall_timeout` 的重试边界（**本次不改，交回属主**）

`backend/deskpet/sdk_adapters/context_route.py:361-364` 目前把 SDK 的
`TimeoutError` 翻译成**可重试**的工具失败。模型连试 5 次即触发
`react_repeated_tool_exceeded`，整个 Run 死亡——一次记忆抖动不应该杀掉整个 Run。

该文件由另一路 agent 拥有，本次**不动**。建议在那边收口，二选一：

- 改成"带降级码的成功返回"（无记忆片段 + 提示继续），或
- 标记为不可重试并引导模型改走 `direct_standalone`；

无论哪种，**`context_route_recall_timeout` 的重试次数必须有界**，
不能靠 `react_repeated_tool_exceeded` 兜底。

---

## 6. 遗留 / 交接

| 项 | 侧 | 状态 |
|---|---|---|
| 世代重建在 `_write_lock` 内做 `embed_batch`（根因） | SDK | 另一路 agent，0.6.27 |
| recall 取写锁无 deadline（拿到锁即抛超时） | SDK | 同上 |
| 短时域 chunk `public_text` 无长度上限 / 不再分块 | **SDK 契约（需新增条款）** | 本文 §2，Host 不实现 |
| `context_route_recall_timeout` 重试无界 | Host | 本文 §5，属主另有其人 |
| `WeMMEmbedder` 无批处理 | Host | 本文 §1，已实现 |
| 维护 tick 无退避 / 超时对不上实测 | Host | 本文 §3，已实现 |
| 前台 `deadline_ms=1000` 过紧 | Host | 本文 §4，已实现 |

---

## 7. 验证

聚焦套件（19 个文件 = 触碰模块 + 其 typed recall / RecallBudget / 短索引调用方，
202 项）：**16 failed / 185 passed / 1 error**。

- 1 error 只是我给那次运行加了 `-p no:logging` 导致 `caplog` fixture 不存在；
  已把审计用例改成自带 `logging.Handler`，加不加该插件都是 9 passed。
- 16 项失败**逐条在基线 `188394b1` 上复现**（另开只读 worktree 对跑）：
  - `test_wemm_lazy.py::test_installed_public_production_empty_db_and_status_stay_cold`
    （用例硬断言 SDK `0.6.12`，装机是 `0.6.26`）；
  - `test_short_index_worker.py` 11 项（fixture 无生产 embedder →
    `short_horizon_embedder_required`，`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md` 已记为既有红）；
  - `test_short_vector_mode.py` 1 项；
  - `test_primary_visibility.py::test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite`
    1 项（任务书列出的既有红）；
  - `test_no_recall_gate.py` 2 项（基线单跑同样 2 failed / 8 passed）。
- 新增用例：`test_wemm_embed_batch.py` 9（默认）+ 2（`model_required`，真模型跑过）、
  `test_short_index_backoff.py` 9、`test_recall_budget_deadline.py` 3，全部通过。
- `test_short_index_generation.py` 5 项在改动后仍全绿（其中 3 项按退避语义更新）。

未跑：全量 83 个 importer 文件的完整回归。本机同时有多路 agent 在跑测试，
CPU 严重争用，整批要数小时且会与他们的 fixture 抢资源（首次尝试在
`tests/execution/test_model_short_outbound.py` 上出现 9 个 setup ERROR，
而同一时刻另一路 agent 正在跑同一文件）。已按"触碰模块 + 直接调用方"收敛到上述 19 个文件。
