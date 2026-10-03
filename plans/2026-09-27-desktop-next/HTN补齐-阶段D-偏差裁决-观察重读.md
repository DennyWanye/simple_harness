# HTN 补齐 · 阶段 D 偏差裁决：观察重读与"世界变了之后的真值"

- 日期：2026-10-03；裁决对象：施工方偏差单（D7，开工裁决 1.7、1.8、用例 11、12）
- 读码位置：`simple_harness-a4`（分支 `htn-d`），`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`
- 依据：AER 设计 §8.1、§8.3、§9.2 第 1～2 步、§9.3 末段（`annex/aer-1.0/design.zh-CN.md:289-364`）；v1.4 完整计划 ADR-07（`complete-plan.zh-CN.md:174-176`）与 C29 消费者规则"不得沿用旧 TRUE"（同文件 `:694`）

## 〇、结论

1. **两点都属实。** 算真值时同一命题的全部观察一律合并，没有任何"较晚观察取代较早观察"的规则；观察记录里也没有谓词引用和参数。用例 12 按现状只能得到"冲突"，而且永远是冲突。
2. **采纳施工方方案，并收成一处：** 同一观察器对同一命题，只取它最新的一条观察；不同观察器之间照旧合并，反面观察不被压倒。规则直接写进 `EvidenceEntry.from_observations`，不另起函数。`world.snapshot()` 和纪元写方原来就调它，不用改调用。规划包事实行和读集核对都不动。
3. **谓词引用与参数不记事件，记在观察行上**：`observations` 表加一列 `question_json`，由唯一写入口 `insert_observation` 在同一条 INSERT 里写入，并核对它能算回本行的命题键。两条取证路径都经过 `record_observation`，所以不用各写一遍。
4. **纪元写方**：插入前后各算一次 `from_observations(...).truth()`。插入前是 TRUE/FALSE、插入后变了，就加纪元；其余情况都不加（未知→已知、冲突→任意）。
5. **重读要有一道屏障，不能每轮都读**：只在"世界变了"（新增验收，或资料表有变动）之后对已记录的命题重读一遍，每次世界变动只读一遍。种子领域的观察器会跑 git、调 AppWorld 接口、复制工作区跑测试，每轮全读代价不可接受。
6. 用例 11 只补一句断言；用例 12 改写剧本和断言（见第五节）；改坏检验加两行。

## 一、核实（文件:行，均为 a4 工作树）

| 项 | 事实 |
|---|---|
| 快照合并全部观察 | `planning/htn/world.py:320-326`：按命题键分组后，把整组交给 `from_observations`；`contracts/evidence_state.py:383-388` 逐条 `support.merge`，`:150-153` 只做加法；`:141-148` 两边都有支持就判冲突。同一观察器先 FALSE（权威反面观察，f=1）、后 TRUE（t=1），结果是 CONFLICT，之后再观察也只会让 t、f 继续增加 |
| 观察记录无谓词与参数 | `ObservationRecord`（`evidence_state.py:175-198`）只有 `proposition_key`（谓词引用加参数的摘要，`knowledge/predicates.py:287-297`），算不回原值；`observations` 表的列（`storage/htn_store.py:1352-1355`）也没有 |
| `valid_until_ms` / `Validity` / `not_after_ms` | 都**不是**取代机制：`valid_until_ms` 存进了列（`htn_store.py:1370`），但快照建条目时既不传有效性也不传 `not_after_ms`（`world.py:325`），条目恒为 CURRENT，`truth()` 里的过期分支（`evidence_state.py:409-414`）在观察链上走不到。桌面观察本来就写 `valid_until_ms=None`（开工裁决 1.7） |
| `support_revision` | 只是观察总条数（`world.py:334`；规划情境摘要 `orchestrator/event_handler.py:2644`），不带取代语义 |
| **漏看的两处"取最新"** | ①读集核对：引用的观察只要同命题后面有更新的记录，就判"已被取代"而过期（`orchestrator/_read_set.py:336-342`）；②规划包事实行：每个命题只展示最新一条（`planning/htn/planner_package.py:385-395`）。两处都是"按命题取最新"，而算真值的地方是"全部合并"，三处口径不一致：事实行展示的是新的 TRUE，旁边的 `truth` 列却是 CONFLICT |
| 同一命题实际只有一个观察器 | 建观察器索引时，同一谓词注册两个观察器会直接拒绝（`planning/htn/observation_pipeline.py:22-26,121-131`）。所以在同一部署里，"按命题取最新"与"按（命题，观察器）取最新"结果相同 |
| `supersedes` | 只用于知识与声明（`contracts/models.py:649-743`、`memory/verified_knowledge.py:50-51`）和操作意图，与观察无关 |
| 提问内容现有的去处 | 规划器取证这条路：`persist_questions` 的返回值里已带 `question`（谓词引用、参数、命题键，`orchestrator/planning_evidence.py:63`），写进了 `PlanningEvidenceRecorded` 事件（`event_handler.py:6693-6694`）。自动取证轮（`planning/htn/evidence_round.py:186-188`）什么都不记 |

注：开工裁决写的 `htn_store.py:1368-1403`，在 a4 里实际是 `:1344-1380`。

## 二、裁决一：世界变了之后，同一命题怎么得到新真值

**规则：** 算一个命题的真值时，每个观察器只保留它对该命题最新的那条观察（按 `observed_at_ms`，相同再按 `observation_id`，与 `list_observations` 的排序一致），然后再按原来的 (t, f) 方式合并。`observer_id` 为空的记录各算一个来源，不取代别的，也不被别的取代；目前经观察管道写入的记录都带 `observer_id`。

**为什么不违背"反面观察不被压倒"：** AER §8.3 说的是不同来源的支持同时存在时，正支持再多也冲不淡反证，这条照旧，两个观察器一 TRUE 一 FALSE 仍是冲突。同一个权威观察器、同一覆盖范围、更晚的再读，是 §9.3 末段要求的"新的观察明确反证不适用"，也是 §9.2 第 1 步"按当前 as_of 选 anchor"。ObservationRecord 的定义（§8.1）本来就是"某时、某范围的真实观察，非绝对真理"，旧行不删不改，历史可查。C29 写的"不得沿用旧 TRUE"（`complete-plan:694`）同样适用于旧 FALSE。

**落点（只改一处）：** 在 `EvidenceEntry.from_observations`（`evidence_state.py:372-397`）里先按观察器取最新，再合并。`observation_refs` 只列取用的记录。全仓只有 `world.snapshot()` 调它（a4 已用 grep 确认）。纪元写方也调它，前后比较自然用同一套算法。同时更新 `world.py:299-312` 的说明文字。

**不动的：** 读集核对 `_read_set.py:336-342` 属于保证通道，原样保留："引用后又有新观察就过期"是比真值规则更保守的另一个问题（"引用之后有没有学到新东西"），在单观察器前提下与新规则一致。事实行 `planner_package.py:385-395` 也原样保留，因为它必须和读集对得上，免得规划器引用一条会被拒的旧记录。

**对种子领域（code、appworld、drone_sim）的影响：** 只有"同一观察器对同一命题记过两次以上"时才有区别。现有写入路径里，自动取证轮只问 UNKNOWN 的命题，并且每个计划修订号只问一次（`hierarchical_dispatch.py:3036-3046,3126-3127`），可能出现的顺序只有"尽力而为的否定（不计支持）→ TRUE"，新旧规则都得 TRUE。会有区别的只有规划器 `REQUEST_EVIDENCE` 重问一个已知命题的情况：

- TRUE 之后权威 FALSE：旧规则得冲突，新规则得 FALSE；
- FALSE 之后 TRUE：旧规则得冲突，新规则得 TRUE；
- TRUE 之后尽力而为的否定：旧规则得 TRUE，新规则得 UNKNOWN。

新结果就是"最近一次看到的世界"，旧结果是缺陷。可以接受。A96 基线已冻结、不重跑，不受影响。

## 三、裁决二：重读用的谓词引用与参数记在哪

**记在观察行上，不记事件。** 迁移 39（与 D3 同一个迁移，不另起）给 `observations` 加一列 `question_json`，内容是 `{"predicate_ref":…, "arguments":…}`。

- `observe_predicate`（`observation_pipeline.py:140-181`）本来就拿着 `reference` 和 `arguments`，把这两样放进 `ObservationOutcomeRecord`。`record_observation` 原样转给 `insert_observation`，后者新增**必填**参数 `question`，写在同一条 INSERT 里。插入时用 `predicate_ref` 和 `arguments` 按 `proposition_key` 的公式算回命题键，与记录的不一致就 `StoreConflict`。这是秩序检查，不是语义判断。
- 两条取证路径（`persist_questions`、`run_round`）都走 `record_observation`，**不用改**。直接调 `insert_observation` 的旧用例共 6 处（`T/full_target/test_htn_store.py`、`test_plan_commits.py`、`test_htn_end_to_end.py`、`test_htn_deployment_wiring.py`），随这次改动补上参数。
- 读到 `question_json` 为空的行时，明确报错，不回落（开发期不兼容，新建编排数据目录）。
- 不改 `ObservationRecord` 的契约：保证通道 `knowledge/assurance_sources.py:186,224` 在内存里构造的审阅/检查锚点没有谓词，改契约就会出现"有的带、有的不带"两种形状。
- 为什么不用"问过"事件：问的内容是这条观察自身的属性（AER §8.1 的命题由"谓词版本加类型化参数"确定），应该和观察在同一行、同一次写入。用事件就得有两个写方、按命题键做幂等，还和 `PlanningEvidenceRecorded` 里已有的问题重复，同一件事记了两处。观察器答不上来时不写观察，也就没有可重读的东西。这是对的：没记录的命题本来就是 UNKNOWN，由自动取证轮或规划器去问。

## 四、裁决三：纪元写方的前后比较

在 `insert_observation` 的同一事务里，INSERT 之前先执行：

```
existing = list_observations(mission, proposition_key=key)        # 插入前
before   = from_observations(key, existing).truth() if existing else UNKNOWN
after    = from_observations(key, existing + (new,)).truth()
if before in (TRUE, FALSE) and after != before:
    bump_epoch(mission, scope_id, bumped_by=f"observation:{new.observation_id}")
```

- 未知→已知不加（已定，不变）。冲突→任意也不加：冲突时开工门不放行，没有发出过可用见证，理由与"未知"相同（开工裁决 1.8 第 3 点）。
- 新观察的 `observed_at_ms` 早于同一观察器已有的那条时（规划器取证在事务外读，可能和自动取证轮交错），它不是"最新"，`after == before`，不加纪元。这是对的。
- "变了"既包括 TRUE↔FALSE，也包括 TRUE/FALSE→UNKNOWN（同一观察器再看时只给出尽力而为的否定）和 →冲突（另一观察器给出相反结论）。
- 重读"没变不写"用同一个比较：重读的一方先把 `(before, after)` 算出来，相同就不调 `insert_observation`。比较只用一个小函数 `truth_change(existing, new) -> (before, after)`，放在 `from_observations` 旁边，写方和重读两处共用。

## 五、裁决四：重读的屏障，以及用例怎么改

**屏障：** "世界标记" = 本任务已有验收的编号集合，加上资料表各行（路径、版本、是否被取代、是否撤销、修订号）的摘要。`run_evidence_round` 的重读那一半只在标记与上一次重读不同时执行：对每个有记录、观察器能读的命题，按 `question_json` 和该行的 `scope_id` 读一遍，变了才写。读完写一条 `EvidenceReread` 事件，键就是标记（幂等），载荷列出读了哪些命题、哪些变了，供审计。同一轮里与"未知前提"那一半按命题键去重。

为什么不用"最新观察时刻早于世界变动时刻"来判断：没变不写，最新观察时刻就不会往前走，结果每轮都会再读一遍。也不能"没变也写"：每写一条都会让读集里引用的旧观察"被取代"（`_read_set.py:340`），规划情境摘要也会变（`event_handler.py:2644`），在途的规划答复会白白过期。

**用例 11：** 断言追加一句："该观察行的 `question_json` 能算回它的命题键"。其余不变。

**用例 12（替换整行）：**

| 12 | `test_file_change_flips_observation_and_moves_epoch` | 同上 | 根目标拆成"写出 out.md"（第 1 步）和"整理 out.md"（复合目标，后细化）；第一次规划时规划器 `REQUEST_EVIDENCE` 得到 `file-present(out.md)=FALSE`；第 1 步写出 `out.md` 并通过；随后规划器细化第二个目标，做法的 `applicable_when` 写 `file-present(out.md)`；另做函数级参数化检查：同一观察器先 FALSE 后 TRUE 得 TRUE；两个观察器一 TRUE 一 FALSE 得 CONFLICT | 验收后下一轮重读写出 TRUE 观察；库里该命题的两条观察都在（FALSE 在前）；快照真值是 TRUE，不是冲突；规划包事实行 `truth=TRUE`；纪元 0→1、`bumped_by` 为 `observation:<TRUE 的编号>`；一条 `EvidenceReread` 事件；再跑一轮不再读、观察条数不变；第二个目标预览可用，提交读集里 `scope_epochs=[mission:1]`；该步开工见证的 `scope_epoch=1`；任务完成，收尾无 `EVIDENCE_STALE` |

**改坏检验加两行：**

| 同一观察器较晚观察取代较早观察 | `from_observations` 去掉按观察器取最新 | 12 |
| 重读屏障与观察行带问题 | `insert_observation` 不写 `question_json`，或重读那一半不看标记 | 12 |

## 六、可粘贴的计划文字

**开工裁决 1.7**，"观察记录"一条替换为：
- 观察记录 `coverage=AUTHORITATIVE_WITH_SCOPE`（这样 FALSE 才可采纳，`observers/__init__.py:18-25`），`valid_until_ms=None`，新鲜度由纪元管（1.8）。观察行同时记 `question_json`（谓词引用与参数，供重读，见偏差裁决《观察重读》第三节）。只读库，不碰磁盘、不调模型。

**开工裁决 1.8**，结论替换为：
**结论：①真值规则：同一观察器对同一命题只取最新一条观察，不同观察器照旧合并（反面观察不被压倒），写在 `EvidenceEntry.from_observations` 一处，旧观察行不删不改。②写方放在 `HtnStore.insert_observation` 同一事务：插入前后各按 `from_observations` 算一次该命题真值，插入前是 TRUE/FALSE 而插入后不同（含变冲突、变未知）→ `bump_epoch(mission, scope_id, bumped_by="observation:<编号>")`；未知或冲突→任意都不加。E 的要求修订是第二个写方（E 做）。③重读：`run_evidence_round` 在"世界标记"（验收编号集合加资料表摘要）变了之后，对本任务已记录、观察器能读的命题按观察行的 `question_json` 重读一遍，真值变了才写（随之加纪元），每个标记读一次，并记一条 `EvidenceReread` 事件。读集核对与规划包事实行的"按命题取最新"不动。**

**开工裁决 D7**，第 2、3 条替换并加第 5 条：
2. `evidence_state.py` `from_observations` 加"每个观察器取最新"，旁边加 `truth_change`；`observation_pipeline.py` 的 `observe_predicate` 结果带上谓词引用与参数；`htn_store.py` `insert_observation` 加必填参数 `question`，写 `question_json` 并核对命题键，同事务按 1.8 加纪元；迁移 39 给 `observations` 加 `question_json`。
3. `hierarchical_dispatch.py` `run_evidence_round`：加"世界变了才重读"那一半（1.8 ③），结果并入同一返回值，与"未知前提"那一半按命题键去重；写 `EvidenceReread` 事件。
5. 直接调 `insert_observation` 的 6 处旧用例补参数；`world.py` 快照的说明文字改成新规则。

**偏差单**加第 8 条：

| 8 | 计划未写同一命题多次观察怎么定真值；1.8"每轮重读" | 同一观察器取最新、不同来源合并；重读按世界标记触发；问题记在观察行上 | 按旧规则，世界变了只会得到"冲突"；每轮重读对种子领域代价过大（偏差裁决《观察重读》） |

**主计划**：表二第 1a 条里的"每轮重读已记录命题"改为"世界变了（新验收或资料变动）才重读已记录命题；同一观察器较晚的观察取代它自己较早的观察"。修订记录加：

"**第 3.12 版（2026-10-03）**：阶段 D 偏差裁决《观察重读》改入：同一观察器对同一命题只取最新观察（不同来源照旧合并，旧行保留）；观察行记谓词引用与参数供重读；纪元写方前后比较用同一算法，未知或冲突→任意不加；重读改为世界变了（新验收或资料变动）才做，每次变动一遍。"
