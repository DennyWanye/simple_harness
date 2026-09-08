# 语料 rerun-flash-01 复核记录（C01/C02/C03/C04/C06 共 97 例，deepseek-v4-flash）

更新：2026-09-09 凌晨。性质：Opus 子代理逐类语义审查 + 主代理复核裁定，**不是人工标注**。
原始证据（gitignored）：`.local-test-evidence/2026-09-09/corpus-rerun-flash/run-01/`；
复核材料 `run-01.review-material.md`（并按类拆分为 `.C01/.C02/.C03/.C04/.C06.md`）、
裁定 `run-01.review-verdicts.json`、
prospective 缺陷诊断 `diag-prospective-2/`（含 `registration-error.traceback.txt`）。

## 一、范围、组合与"跑的到底是哪份代码"

- **97 例**，即 `supported_case_ids()` 里 C01/C02/C03/C04/C06 的全部：
  C01 20、C02 19（无 C02-19）、C03 19（无 C03-20）、C04 20、C06 19（无 C06-01）。
  任务书写的是 98 例，实际支持集是 **97**（C06 为 19 而非 20）。
- provider：**`deepseek-v4-flash`**（用户 2026-09-09 决定），97/97 走 primary，无回退、无 502。
- 组合：Host main、installed `installed-h0710-m0631-s0313`（H0.7.10 / **M0.6.31** / S0.3.13）。
- 批次经 `scripts/run_corpus_batch.py` → 每例 `scripts/run_resource_bounded.py`（6 GiB / 1200 s），
  串行；97 例 **39.7 min**，中位 22.2 s，最长 89.7 s，**97/97 rc=0，stop_reason 全 None**。

**代码基线的准确说法（重要）**：批次 02:33:05 启动、03:13:5x 结束。本工作树的 HEAD 在此期间
只在 **03:13:39** 动过一次（`git reflog`：`f161f5a4 HEAD@{03:13:39}: merge worktree-token-budget-reconcile`）。
因此 96 例跑在 `c4605f39`（= 任务书给的 `c9384422` + 一条纯文档提交，代码逐字相同）；
只有 **C06-20**（03:13:22 启动）横跨该合并瞬间，但工作进程在 03:13:22 已完成 import，
`primary_context` / `context_route` 均为进程启动时载入，故实际仍是合并前的文本。
**本轮测的不是当前 HEAD `f161f5a4`**——后者在 02:48（`e3ef4aed`）与 03:13 又压缩了 PERSONA 与
`context_route` schema 措辞，那是本轮被测文本的**下一版**，需要另行重跑才能计量。

## 二、被测的三处改动

| 改动 | 决策记录 | 本轮是否被真实执行 |
|---|---|---|
| `context_route` 的 `memory_types` 四条类型选择规则（R1–R4） | `DECISION-EXTRA-TYPE-RATE.md` | **是**，逐例可见于 `route_audit[].detail.recall_selection.requested_memory_types` |
| PERSONA 补齐五条 context_route 路由 + 驱动墙钟 600 s | `DECISION-TERMINATION-AND-PERSONA-ROUTES.md` | **是**，每例 `backend.log` 均有 `react_termination_limits … max_wall_seconds=600.0` |
| 分析协议 v8（指代不当事实 / 指代解析 / 关系端点） | `DECISION-RELATION-EXTRACTION.md` | **否——本轮无法计量**。语料跑道在评分前关闭分析车道（`backend/deskpet/quality/corpus_scoring_session.py:435` `await main._memory_analysis_lane.close()`），97 例的 `backend.log` 里 `host-analysis-prompt/*` 与 `analysis_operations_rejected` 均 0 次。v8 只能由 native 旅程或专门的写侧度量验证。 |

## 三、总结果

| 类别 | 执行 | PASS | FAIL | NOT_SCORED | 09-08 记录 | 变化 |
|---|---|---|---|---|---|---|
| C01 精确召回 | 20 | 17 | 2 | 1 | 20/0/0 | **−3 PASS** |
| C02 偏好复用 | 19 | 19 | 0 | 0 | 19/0/0 | — |
| C03 冲突/多来源 | 19 | 19 | 0 | 0 | 19/0/0 | — |
| C04 时间/提醒 | 20 | 1 | 0 | **19** | 17/3/0 | **整类被 Host 缺陷阻断** |
| C06 额外来源/流程 | 19 | 11 | 8 | 0 | 13/6/1（含 C06-01） | **−2 PASS / +2 FAIL** |
| **合计** | **97** | **67** | **10** | **20** | | |

- **隐私违规 0 例**（77 例可评分全部 false；四个复核子代理独立确认）。
- 执行侧零环境噪声：97/97 `driver_returncode=0`、`stop_reason=None`，无中转 5xx、无超时被杀。
- 路由：77 例可评分用例**全部**首选 `context_route(route=memory_standalone)`，零例走错路由面、零例不调工具直答。
  唯一的路由噪声是 C01-08 多走了一次 `task_scope_search`（该例 `requires_task_scope_search=false`），
  方向是"多查"而不是"走错"。**PERSONA 五路由补齐在本批未见任何退化。**

## 四、逐类裁定

### C01（精确召回，20 例）：17 PASS / 2 FAIL / 1 NOT_SCORED

| 用例 | 裁定 | 依据 |
|---|---|---|
| C01-01/03/04/05/09/10/11/12/13/14/15/16/18/20 | PASS | 各自绑定 gold 指定的 A 当前值，干扰项 B 被显式排除，未越权执行 |
| C01-02/06 | PASS | 答案正确，仅多提 `episode`，未污染回答 |
| C01-07 | PASS（边界） | 主回复无禁用结尾；备选项"有需要再找我"与禁用句语义接近，属措辞瑕疵 |
| **C01-08** | **FAIL / 模型行为** | 已召回"手机短列表"分支，却宣称"任务甲/任务乙…均没有对应记录，无法编造整理"而拒绝出稿——两项内容就在用户问句里。run-02 同跑道同 gold，luna 直接正确出稿 |
| **C01-19** | **FAIL / SDK 阈值（见 §五缺陷 3）** | `recall_refs=[]`、`fragments=[]` 后宣称"没有找到记录"；A 确在库中、向量世代 `activated=true, vector_count=2` |
| C01-17 | NOT_SCORED / Host 缺陷 1 | `ProspectiveSetupNotReady`，无 transcript |

### C02（偏好复用，19 例）：19 PASS / 0 FAIL

19/19 全部真正检索并复用了 required 的 `semantic` 当前值。
**历史 FAIL 例 C02-04（"未检索直接作答"）本轮已消失**：正确召回 `instruction_accessibility`
并给出"颜色+文字双通道"三种写法。C02-18 首次 `semantic` 召回 0 片段后模型自行改写关键词式
查询救回两条（见 §五缺陷 3 的同族现象）。多提由 15/19 降到 5/19，**required 召回 19/19 未受影响**。

### C03（冲突/多来源，19 例）：19 PASS / 0 FAIL

**R2（episode 收窄）未误伤本类**：19/19 全部同时请求了 `episode` 与 `semantic`
（`recall_selection.requested_memory_types`，`origin=model_proposal`，`selection_policy_departures` 全空），
`proposed_required_matches=2/2`。每例都拿回了 gold 需要的 E 与 S 片段。多提只剩 C03-09 一例（`prospective`）。

### C04（时间/提醒，20 例）——主代理直接裁定

19 例 `SETUP_BLOCKED`，只有 C04-12 可评分。逐例裁定不必展开：19 例的失败形态逐字相同
（`ProspectiveSetupNotReady: corpus_prospective_registration_missing:<memory_id>`，
`execution_status=SETUP_NOT_READY`，`trace=null`，无任何 provider 调用），
根因见下节 Host 缺陷 1，归类 **NOT_SCORED / Host 缺陷**。

| 用例 | 裁定 | 依据 |
|---|---|---|
| C04-12 | **PASS** | `memory_types=[episode,prospective,semantic]`，两条 required 均命中；回答给出"场地检修"与"2026-09-09（周三）09:30 Asia/Shanghai"，并明确该提醒为当前有效版本，未把 9 月 7 日旧提醒当作当前待办。多提 `semantic`（P4）。隐私违规 false |
| C04-01/02/03/04/05/06/07/08/09/10/11/13/14/15/16/17/18/19/20（19 例） | **NOT_SCORED** | Host 缺陷 1：prospective 注册全线失败，评分轮从未启动 |

注意 C04-12 幸存不是偶然：它的 fixture 自己 ack 了注册
（`backend/deskpet/quality/corpus_prospective.py:76-78`），因此不经过出问题的注册消费者。
这反过来证明**故障点确实是注册消费者而不是 fixture 或 gold**。

本类此前记录为 20 执行 / 17 PASS / 3 FAIL（`trigger_local` 时刻换算错）。
本轮**没有推翻也没有确认**那 3 例——它们根本没跑起来。
`trigger_local` 是否修好，必须等 Host 缺陷 1 修复后重跑 C04 才能回答。

### C06（额外来源/流程，19 例）：11 PASS / 8 FAIL

| 用例 | 裁定 | memory_types | discover | 程序访问 | 依据 |
|---|---|---|---|---|---|
| C06-02 | PASS | procedure, semantic | 1 | SATISFIED | ISO 格式 + 先核时区再核日期重叠，未建提醒 |
| C06-03 | PASS | semantic | 1 | SATISFIED | 两位小数 + 先分固定/浮动再核总额，拒绝补编字段 |
| **C06-04** | **FAIL / 模型行为**（Host 共因） | semantic | **0** | NOT_SATISFIED | 对"整理顺序"答"没有找到存档"后自拟 6 步 |
| C06-05 | PASS | semantic | 3 | SATISFIED | 小体积预览 + 两步核对，明示不执行 |
| **C06-06** | **FAIL / SDK 阈值** | procedure, semantic | 3 | SATISFIED | procedure 拿到，但 semantic 零召回，终答自承"未检索到可访问性要求" |
| C06-07 | PASS | semantic | 1 | SATISFIED | 日期_主题 + 三步，停在说明层 |
| **C06-08** | **FAIL / SDK 阈值** | semantic | 1 | SATISFIED | 同 C06-06，semantic「先结论」零召回 |
| **C06-09** | **FAIL / 模型行为**（Host 共因） | episode, semantic | **0** | NOT_SATISFIED | 对"排程做法"答"没有找到"后自拟 7 条 |
| C06-10 | PASS | semantic | 1 | SATISFIED | 明示"流程发现不代表执行授权" |
| C06-11 | PASS | episode, semantic | 1 | SATISFIED | 优先 Python + 先最小例再解释边界，未执行代码 |
| C06-12 | PASS | semantic | 1 | SATISFIED | 保留来源链接 + 分原话与概括，自拟部分已标注 |
| C06-13 | PASS | procedure, semantic | 1 | SATISFIED | 不超两层级 + 只列候选不删除 |
| C06-14 | PASS | semantic | 1 | SATISFIED | 正确扣留个人联系方式，明示未发送 |
| **C06-15** | **FAIL / 模型行为**（Host 共因） | semantic | **0** | NOT_SATISFIED | 对"核对顺序"答"存储记录中没有找到任何条目" |
| **C06-16** | **FAIL / 模型行为**（Host 共因） | episode, semantic | **0** | NOT_SATISFIED | 对"排序"答"存库里没有"，改给三个自拟方案 |
| C06-17 | PASS | episode, semantic | 1 | SATISFIED | 先风险 + 先清单后抽样，不采用失败捷径 |
| C06-18 | PASS | procedure, semantic | 1 | SATISFIED | Markdown + 通用来源整理，命中 P1 未套 P2 财务模板 |
| **C06-19** | **FAIL / gold** | semantic | 1（0 候选） | NOT_SATISFIED | discover query 与种子名"通用手工清单核对"零词面重叠，结构性不可达 |
| **C06-20** | **FAIL / 模型行为**（Host 共因） | semantic ×2 | **0** | NOT_SATISFIED | 两次定向 `memory_standalone` 后答"没有检索到任何存档记录"，始终未转发现面 |

`procedure_discover` 调用率 **14/19 = 73.7%**（此前 18/19 = 94.7%）；
`required_procedure_access.status=SATISFIED` **13/19 = 68.4%**（此前 14/19）。

## 五、缺陷与裁定

### 缺陷 1（Host，本轮最重要发现）: state.db 升到 user_version 55 后，prospective 注册全线失效

#### 事实链

1. `backend/deskpet/memory/s5c_store.py:93-95` 用**闭集合白名单**挑选游标表：

   ```python
   self.cursor_table = (
       "prospective_outbox_cursor_v52" if db.execute("PRAGMA user_version").fetchone() in ((52,), (53,), (54,))
       else "prospective_outbox_cursor")
   ```

   该白名单由 `f6e70fc2`（09-06 14:46）引入，当时最高版本是 v54。
2. `865bfe7a`（09-08 23:29，F-K1 历史因果组）新增
   `backend/deskpet/memory/migrations/primary/047_primary_assistant_tool_calls_v55.sql`，
   把 `PRAGMA user_version` 推到 **55**。
3. 55 不在白名单里 → `cursor_table` **静默回落**到 v50 的
   `prospective_outbox_cursor`，而该表已被
   `backend/deskpet/memory/migrations/s5c/044_prospective_terminals_v52.sql:35-36` 的
   `s5c_cursor_v50_sealed` 触发器封存：

   ```sql
   CREATE TRIGGER s5c_cursor_v50_sealed BEFORE INSERT ON prospective_outbox_cursor
   BEGIN SELECT RAISE(ABORT,'s5c_cursor_successor_required'); END;
   ```

4. 于是 `backend/deskpet/memory/s5c_store.py:384` 的游标 INSERT 必然抛
   `sqlite3.IntegrityError: s5c_cursor_successor_required`
   （复现回溯：`diag-prospective-2/registration-error.traceback.txt`，
   路径 `prospective_runtime.py:83` → `s5c_consumer.py:145` → `s5c_consumer.py:86`
   → `prospective_registration_source.py:148` → `s5c_store.py:384`）。
5. 后果：**任何一条待注册的提醒都注册不上**。语料侧表现为
   `settle_prospective_registrations`（`backend/deskpet/quality/corpus_prospective.py:91`）
   三次 tick 后抛 `ProspectiveSetupNotReady`，整例 `SETUP_NOT_READY` / `SETUP_BLOCKED`。

#### 影响面

- 本轮 **20 例 SETUP_BLOCKED**：C04 19 例（除 C04-12）+ C01-17。
  C04-12 之所以幸存，是因为它的 fixture 自己 ack 了注册
  （`corpus_prospective.py:76-78` 的"read before writing"分支），根本不走 lane。
- **不是语料跑道专有**：`ProspectiveRuntimeLane` 是生产车道
  （`backend/deskpet/memory/runtime_composition.py:91`），
  且产品数据目录 `~/Library/Application Support/com.dennywanye.simpleharness/data/state.db`
  实测同样是 `user_version=55`。凡是在 09-08 23:29 之后新建/迁移过的库，
  真实产品里注册提醒同样会失败。本轮没有在 native 日志里观察到该报错，
  只是因为那次旅程没有产生需要注册的提醒。
- 上一次 C04 全绿批次（`run-01j`，09-08 08:10，installed **M0.6.26**）早于 `865bfe7a`，
  所以此前从未撞上。**根因在 Host，不在 SDK 0.6.26→0.6.31 的差异**。

#### 建议修法

把白名单改成**下界判断**（`user_version >= 52` 用 v52 表），或由迁移登记表派生"当前生效的游标表"，
使新增任何一条与 s5c 无关的迁移都不会再把游标写回封存表。并补一条测试：
`user_version` 大于当前最高 s5c 版本时仍选 v52 表。

### 缺陷 2（Host，可观测性）：真正的约束名从不进日志

`backend/deskpet/memory/prospective_runtime.py:88` 只记
`log.warning("prospective_runtime_registration_failed type=%s", type(exc).__name__)`，
丢掉了异常消息。批次证据里因此只有 `type=IntegrityError`，
`s5c_cursor_successor_required` 这个**唯一能定位根因的字符串在任何持久化证据里都不存在**——
本轮是靠一个临时 sitecustomize 钩子（只作用于复制出来的 installed target，未改仓库源码）才拿到的。
建议至少记录 `exc.args` 或 `str(exc)`。

### 缺陷 3（Memory SDK，非本仓库）：`COGNITIVE_VECTOR_MIN_SCORE = 0.45` 对中文短 semantic 记忆没有余量

三个 FAIL 同根：**C01-19、C06-06、C06-08**。共同形态是——认知向量世代已激活
（`execution.json` 的 `cognitive_vector_generation.activated=true`、`vector_count=2`、`cas_miss=false`），
查询与记忆语义等价但**词面零重叠**，结果 `fragments=[]`，模型据此宣告"没存过"。

C01 复核子代理用生产同一嵌入器做了离线复算，给出本轮最硬的一个数据点：

| 查询 | 与 A（`explanation_order=先说结论再说理由`）的余弦 | 是否过 0.45 |
|---|---|---|
| flash 本轮："说明事情时先报结果还是先铺背景？用户的表达习惯/偏好" | **0.4354** | ❌ |
| luna run-02："用户之前关于说明事情时应先报结果还是先铺背景的约定或偏好" | **0.5839** | ✅ |

同一条记忆、同一个语义、两次都算合理的改写，**跨在阈值两侧**。
阈值出处 `simple-harness-memory-sdk-0631-source/src/simple_harness_memory/features/cognitive_vector.py:23`，
判定点 `backends/sqlite_v5.py:5323`。该常量的注释自称"同义改写通常 ≥0.6"，实测不成立。
诱因之一是向量文本里混了英文 predicate（`user:self / explanation order / 先说结论再说理由`），
`explanation order` 对中文查询是稀释项。

**裁定**：这不是"模型措辞不好"，而是**召回可靠性依赖模型措辞抖动**——
同一条记忆能不能召回，取决于一次改写落在 0.4354 还是 0.5839。归 **SDK 缺陷**，
不归模型行为（措辞差异是常态输入，不是被测对象的错误）。
建议：用这三例的 query/记忆对做一次阈值扫描，并给 semantic 的向量文本补中文 predicate 描述
（对标 0.6.26 给 prospective 加的 `cognitive_text_supplement`）；下调阈值必须用 C07 零召回子集回归。
同族但已自愈的一例：C02-18 首次 `semantic` 召回 0 片段，模型自行改写关键词式查询后救回
（score 仅 0.0065，走的是词法通道）——说明词法通道正在替向量通道兜底，这本身就是失衡信号。

### 缺陷 4（跑道，可观测性）：失败时把整张注册回执丢了

`backend/deskpet/quality/corpus_prospective.py:90-92` 在抛 `ProspectiveSetupNotReady` 前
已经攒好了 `receipt`（含 `ticks` / `tick_errors` / `registered` / `missing`），
但异常路径不返回它；`backend/deskpet/quality/corpus_scoring_session.py:532-539`
也只在成功时给 `outcome["prospective_registration"]` 赋值。
结果 20 例 SETUP_BLOCKED 的 `execution.json` 里 `prospective_registration` 全为 `null`，
`tick_errors=["IntegrityError"]` 这条线索被丢弃。建议失败时把 receipt 一并写进 outcome。

### 缺陷 5（Host，R4 的已知风险已兑现）：`procedure_hint` 与 `memory_types` 耦合

`DECISION-EXTRA-TYPE-RATE.md` §3.4 预告的风险本轮**确实发生**。
`backend/deskpet/sdk_adapters/context_route.py:510-513`：

```python
**({"procedure_hint": dict(_PROCEDURE_HINT)}
   if "procedure" in memory_types and not any(
       fragment["memory_type"] == "procedure" for fragment in fragments)
   else {}),
```

R4 让模型不再请求 `procedure`，这条提示的触发路径随之关闭。本轮构成一次干净的批内 A/B：

| | 例数 | 调用 `procedure_discover` |
|---|---|---|
| 实际发出 `procedure_hint`（= 请求了 `procedure` 的 4 例：C06-02/06/13/18） | 4 | **4/4 = 100%** |
| 未发出提示 | 15 | **10/15 = 66.7%** |

未调 discover 的 5 例（C06-04/09/15/16/20）经逐例核对，**0 例属于"判断这轮不需要流程"**：
它们的终答全部落在 PERSONA 明令禁止的那句话上（"never conclude from their absence that you
saved no such workflow"）——"没有找到存档"/"存库里没有"/"没有检索到任何存档记录"。
即模型确实想要那条流程、去类型化召回找了、找空了就下了"没存过"的结论，
这正是 `procedure_hint` 设计要拦的失败模式。

**裁定（用户授权自行裁定并记录）：执行 §3.4 的解耦方案，不回退 R4。**
把触发条件从「`"procedure" in memory_types` 且本次无 procedure 候选」改为
「库中确实存在未绑定的 Procedure，且本次 fragments 无 procedure」。
理由：R4 是唯一把多提类型率压到阈下的规则（回退它，24.2% 立刻回来），
而解耦既保住 R4 的指标收益，又能回收这 5 例。列为 **F-ETR-5**。

### 缺陷 6（跑道/gold）：发现面在"用户不给流程名"时结构性不可达

C06-19：`procedure_discover` 的 `match_score` 词命中为 0 即返回空页
（`simple-harness-memory-sdk-0631-source/src/simple_harness_memory/backends/procedure_discovery.py:83`），
而种子流程名"通用手工清单核对"与任何合理的 topical query 零重叠。
用户只说"可用流程"、不给名字时，模型无论怎么措辞都命中不了。
**裁定归 gold**（该例的期望以当前发现面契约不可达），
处置二选一：给发现面加一条 bounded 的"按 lifecycle 列出全部可发现候选"路径，或改该例 gold。列为 **F-ETR-6**。

### 观察（非缺陷，记录备查）

1. **`NO_ACTIVE_GENERATION` 未归零**：C06 19 例中 17 例仍带该降级码。
   `DECISION-PROSPECTIVE-PROCEDURE-RECALL.md` §3.3 预期修 A 后应归零，需单独立案核对
   跑道 lane 的 tick 是否真的补上。
2. **内部 predicate 键名外泄到用户可见回答**：C02 有 8 例把 `food_taste` / `stationery_budget`
   等字段名原样写进答案。不违反任何 gold、非隐私违规，但属产品体感问题，
   可由 PERSONA 补一条"不要向用户暴露记忆字段名"。
3. **C03 的 fixture 日期警告文本被逐字转述给用户**：`backend/deskpet/quality/corpus_c03_dates.py:36-38`
   把"【…日时仅为合成 fixture 取值，非真实发生日…】"拼进 episode title，
   17/19 例的回答主动向用户复述了它。后果不只是难看——**它替模型完成了判断**，
   因此 C03 的结果不能用来佐证 C04 类的日期能力。
4. **干扰项主动披露是 flash 的稳定风格**（C01-02/12/13/14/15 等）：都会额外说明 B 并声明不采用。
   本批 `privacy_allowed` 全为 true，不构成失败；但对 C09/C11/C12 这类零披露要求的类别是明确风险点。

## 六、三阈值重算（机械口径，脚本见 scratchpad，不入库）

口径与既有记录一致：**每例取最后一次可评分批次**；`SETUP_BLOCKED` / `OBSERVATION_FAILED`
不是可评分批次，故本轮 20 个 SETUP_BLOCKED 例回落到它们各自上一次可评分批次
（C04 19 例 + C01-17 用 09-08 的 M0.6.26 批次）。
多提类型率的分母是 `prediction_observation_complete=true` 的 packet 数。

| 阈值 | 09-08 记录 | 本轮重算后 | 判定 |
|---|---|---|---|
| required-type 召回率 | 134/136 = 98.5% | **136/136 = 100%** | ✅（≥90%） |
| 隐私违规 | 0 | **0**（本轮 77 例可评分全 0） | ✅ 100% |
| 多提类型率 | 55/227 = **24.2%** | **26/228 = 11.4%** | ✅（≤15%，**首次达标**） |

> 09-08 记录写的是 54/223 = 24.2%；用同一脚本对同一批 packet 机械复算得 55/227 = 24.2%，
> 比率一致，分子分母的细微差异来自"可评分"边界的取法，不影响结论。本表的"09-08 记录"列
> 一律用本脚本复算值，以便与"本轮"列同口径比较。

### 只看本轮 97 例（不回落）

| 类别 | 执行 | SETUP_BLOCKED | 可评分 | 多提例数 | required 召回 |
|---|---|---|---|---|---|
| C01 | 20 | 1 | 19 | 3 | 19/19 |
| C02 | 19 | 0 | 19 | 5 | 19/19 |
| C03 | 19 | 0 | 19 | 1 | 38/38 |
| C04 | 20 | 19 | 1 | 1 | 2/2 |
| C06 | 19 | 0 | 19 | 8 | 19/19 |
| **合计** | **97** | **20** | **77** | **18 = 23.4%** | **97/97 = 100%** |

同一批用例在上一次可评分批次上的多提是 **47/76 = 61.8%**，本轮 **18/77 = 23.4%**，
逐类：C01 12/19→3/19、C02 15/19→5/19、C03 3/19→1/19、C06 17/18→8/19。
required 类型召回在这批用例上由 96/97 升到 **97/97**（唯一的两处未命中原在 C06，本轮消失）。

### 残留多提的形态（对照 `DECISION-EXTRA-TYPE-RATE.md` §2 的 P1–P4）

| 形态 | 例数 | 用例 |
|---|---|---|
| P1 多加 `episode` | 12 | C01-02/06/08、C02-01/02/08/15/18、C06-09/11/16/17 |
| P2 多加 `procedure` | 4 | C06-02/06/13/18（Host 咨询码 `procedure_not_served_by_typed_recall` 全部命中这 4 例） |
| P3 多加 `prospective` | 1 | C03-09 |
| P4 兜底加 `semantic` | 1 | C04-12 |

即 **R2（episode 收窄）是剩下的主要缺口**：12/18 的残留都是"提到过去 → 加 episode"。
R4 已把 `procedure` 多提从 17 例压到 4 例，R3 把 `prospective` 压到 1 例。

## 七、flash 与 pro/luna 的行为差异

| 维度 | `gpt-5.6-luna` / `deepseek-v4-pro`（此前各批） | `deepseek-v4-flash`（本轮） |
|---|---|---|
| 单例耗时 | C02 类 4–6 min；C10-13 曾 900 s 被杀 | 中位 **22.2 s**，最长 89.7 s，97 例合计 39.7 min |
| provider 往返 | 常见 4–11 次 handoff、空召回后重复重提同一路由（F03） | **64/77 例只有 2 次 handoff**（一次工具 + 一次作答），3 次 11 例、4 次 2 例，**零空召回循环** |
| 环境失败 | 多批出现中转 502/503、超时 | **0** |
| 路由选择 | C10-17 曾把纯文本改写升格 `create_new`；C05 类走错工具面 | 本批 77/77 正确走 `memory_standalone` |
| 类型选择 | 多提 61.8%（同批用例） | 多提 **23.4%**，且四条规则的判别词逐条生效 |
| 弱点 1 | — | **"召回成功却拒绝出稿"**（C01-08）：把用户问句里已有的内容当成"需另有存档才能用" |
| 弱点 2 | — | **不确定性声明偏少**（C02-14 把 gold 的"需核查"弱化成"一般可适配"） |
| 弱点 3 | — | **把内部字段名写给用户**（C02 8 例） |
| 查询改写 | 措辞更长、更贴近记忆原文（C01-19 得 0.5839） | 更口语、更短（同例 0.4354，跌破向量阈值） |

一句话：**flash 在路由与类型选择上更守规、更快、更省 token，但查询改写更口语化，
正好把召回逼到向量阈值边缘**——缺陷 3 之所以在本轮才暴露，与换模型直接相关。

## 八、主代理复核裁定

1. **三个阈值的判定以 §六的"回落"口径为准**（每例取最后一次**可评分**批次；
   `SETUP_BLOCKED` 不是可评分批次）。据此 **多提类型率 11.4% 首次达标**、
   **required 召回 100%**、**隐私 0 违规**，HM-AC-8 三项全绿。
   但必须同时写明：**C04 整类本轮未被重新计量**，那 40 条 required 类型分母用的是 09-08 的旧批次数据。
   Host 缺陷 1 修好之前，"required 召回 100%"这个数字里有 40/136 是旧证据。
2. **C01-19 / C06-06 / C06-08 归 SDK 缺陷，不归模型行为。** 判据是 §五缺陷 3 的离线复算：
   同一记忆、同一语义的两次合理改写跨在阈值两侧（0.4354 / 0.5839）。
   把召回成败押在措辞抖动上是被测系统的问题，不是输入的问题。
3. **R4 保留，按 F-ETR-5 解耦 `procedure_hint`。** 依据是 §五缺陷 5 的批内 A/B
   （有提示 4/4 调用 discover，无提示 10/15），以及 5 例未调用者的终答**全部**落在
   PERSONA 明令禁止的那句话上——这是提示消失，不是模型判断"不需要"。
   回退 R4 会让多提类型率立刻回到 24.2%，得不偿失。
4. **C04 的 3 例旧 FAIL（`trigger_local` 时刻换算）本轮既未证实也未证伪**，
   它们根本没跑起来。不得把本轮结果解释成"`trigger_local` 已修好"。
5. **本轮测的是 `c9384422`/`c4605f39` 的 PERSONA 与 `context_route` schema 文本**，
   不是当前 HEAD `f161f5a4`（02:48 与 03:13 又压缩过一轮）。
   压缩版是否保住本轮的收益，需要另跑一次才能回答——尤其 `DECISION-TERMINATION-AND-PERSONA-ROUTES.md`
   §3.3 已经记过一次教训：压缩时删掉两句话就让 C05-05 的修复整个失效。
6. **分析协议 v8 本轮无法计量**（跑道关闭分析车道，97 例日志零命中），
   不得把本轮的绿色结果算作 v8 的验证。

## 九、后续（followup）

| 编号 | 事项 | 优先级 |
|---|---|---|
| **F-ETR-5** | `procedure_hint` 与 `memory_types` 解耦（§五缺陷 5） | 高——回收 5 例 C06 |
| **F-PROSP-1** | `s5c_store.py:93-95` 的 `user_version` 白名单改下界判断（§五缺陷 1） | **最高——阻断整个 C04 类，且影响真实产品** |
| **F-VEC-1** | `COGNITIVE_VECTOR_MIN_SCORE` 阈值扫描 + semantic 向量文本补中文 predicate（§五缺陷 3） | 高——3 例 FAIL |
| **F-ETR-6** | 发现面"无命中时枚举"兜底，或改 C06-19 gold（§五缺陷 6） | 中 |
| F-OBS-1 | `prospective_runtime.py:88` 记录异常消息；`corpus_prospective.py:90-92` 失败时保留 receipt | 中 |
| F-RERUN-1 | Host 缺陷 1 修好后重跑 C04 20 例 + C01-17；并在当前 HEAD（压缩版 PERSONA/schema）上重跑 C01/C02/C06 复核收益 | 中 |
| F-C03-1 | `corpus_c03_dates.py:36-38` 的 fixture 警告文本泄漏进用户可见回答（§五观察 3） | 低 |
