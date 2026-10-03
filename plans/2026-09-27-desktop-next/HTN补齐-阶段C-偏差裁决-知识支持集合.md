# HTN 补齐 · 阶段 C 偏差裁决：知识支持集合记在哪

- 裁决人：独立裁决子代理（只读代码与文档，除本文件外没有改任何文件、没有跑测试）。日期 2026-10-03。
- 代码以工作目录 `simple_harness-a4`（分支 `htn-c`，未提交改动在内）为准。`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`。
- 被裁的计划文字：`HTN补齐-阶段C-开工裁决与施工清单.md` 1.4、1.5、C4 第 1/2/5 条、第四节用例 3；`HTN补齐计划-2026-10-02.md` 阶段 C 第 1 条。

---

## 〇、结论

**选乙：支持集合不进 `justification_sets` / `support_members`，改记在知识记录自己的 `support` 字段里，与知识行同一次写入；`knowledge_standing()` 只读这个字段。同时删掉 `KnowledgeRecord.dependencies`（与 `support.knowledge` 是同一件事，只留一处）。两张表本阶段不接也不删，去留并入 D（与计划读集 `SupportSetRead` 一起定）。不新增迁移，不动保证通道任何代码。**

一句话理由：这两张表是保证通道的"验收支持图"输入，知识的依赖是记忆层的事，把后者写进前者，等于让每条新知识都去改所有验收证书的读集；知识表本来就不在通道清单里，记在知识行上是唯一不碰通道、也只记一处的做法。

---

## 一、核实结果（偏差单描述属实）

1. **证书完整读不按主体类型过滤——属实。** `SDK/storage/assurance_reads.py:279-291` 的 `_QUERIES` 对 `MISSION_TABLES` 每张表一律 `SELECT * FROM <表> WHERE mission_id=? ORDER BY 主键`，`justification_sets`、`support_members` 都在 `MISSION_TABLES` 里（`SDK/storage/assurance_source_inventory.py:13-14`），没有 `subject_kind` 条件。`read_complete_evidence_snapshot`（:294-373）逐行进 `set_hash`，读集指纹 = `schema_hash + count + set_hash`（:249-264，不含纪元）。
2. **这两张表就是验收支持图的输入——属实。** `SDK/orchestrator/assurance_validity.py:501` 读完整快照，:509 `_snapshot_sources` 把 `justification_sets`/`support_members` 解成 `JustificationInput` 交给 `evaluate_acceptance_support`（:521-533）；:830-860 三类缺一即 `EVIDENCE_EVALUATION_INCOMPLETE`。附带发现：按 1.5 实现后，知识的支持集合（`rule_ref` 为空、主体 `knowledge`）还会作为"验收的支持依据"混进这个公式——不只是读集变了，语义上也串了。
3. **收尾复查只比这三类查询集——属实。** `SDK/orchestrator/assurance_recheck.py:25` `EVIDENCE_QUERY_KINDS = {observations, justification_sets, support_members}`；`changed_items`（:45-76）重读完整快照比指纹，变了就记 `QUERY_SET … SET_CHANGED`。所以任何一步写知识支持集合，**本任务所有现行验收证书和根结论证书**都会被判"依据已变"，与跑出来的 `EVIDENCE_STALE` 完全对得上。
4. **`knowledge` 表不在通道清单里——属实。** `assurance_source_inventory.py` 与 `assurance_barrier_v26.sql` 里均无 `knowledge`；`Store.upsert_knowledge`（`SDK/storage/store.py:980-998`）写的是 `knowledge.json` 列，不触发屏障，不进任何证书读集。
5. **修复影响分析的读法。** `SDK/orchestrator/repair_impact.py:53` 用 `HtnStore.support_dependency_edges`（`SDK/storage/htn_store.py:936-943`）把每条"成员 → 主体"加进 `reverse_support`。生产里这两张表此前没有写方，所以这条边现在只会有知识这一种主体。选乙后少的是：`analyze_impact`（`SDK/planning/htn/repair_decision.py:389-451`）的 `revalidate` 列表里不再出现知识编号；`affected_occurrences`（`SDK/planning/htn/graph_repair.py:25-60`）最后只保留步骤实例，知识编号本来就被滤掉；没有任何代码按 `revalidate` 里的知识编号做事。**实际损失 = 修复请求的影响清单里少列几个知识编号，不影响哪个步骤要重做**；知识是否过时本来就是读时判定，修复后再读自然是过时。

---

## 二、三个方案的取舍

| 方案 | 要动的东西 | 主要问题 |
|---|---|---|
| 甲：通道读集排除知识集合 | `_QUERIES` 查询文字（`support_members` 要连表才能按主体过滤）、"一份清单两边共享"的冻结约定（`assurance_reads.py:276-278`）、读集语义 `MISSION_SUPERSET` | 动最敏感的读集定义；屏障照旧加纪元；一张表里两类数据、读方要各自过滤，等于两条路；以后谁忘了过滤就再撞一次 |
| **乙：记在知识记录里** | 知识记录多一个字段、写入点、判定函数、两处展示 | 修复影响清单少列知识编号（见上，无实际后果）；重放清单两条缺口保持原样（本来就该归 D 定） |
| 丙：新表 | 新迁移、新写方、新重放条目 | 知识行建成后支持就不再变，另放一张表 = 同一事实两处存；为一个字段加迁移 |

裁决口径对照：保证通道"没有必要不动"→ 排除甲；"只记一处"→ 乙优于丙（并顺手删 `dependencies`）；"不为了关掉一个清单缺口引入跨模块连带失效"→ 1.5 的收益里"关掉重放缺口"正是这种。

---

## 三、选定方案落点

1. `SDK/memory/verified_knowledge.py` `KnowledgeRecord`：
   - **删 `dependencies` 字段**（及 :82 的校验项）；新增 `support: Mapping[str, Any] = field(default_factory=dict)`，形状固定：
     ```
     {"acceptance_id": "<来源验收编号>",
      "artifacts": [{"id": "...", "version": <int>, "content_hash": "..."}],   # 按 id 排序
      "knowledge": [{"id": "...", "version": <int>, "content_hash": "..."}]}   # 按 id 排序
     ```
   - `__post_init__`：非空时校验恰好三个键、`acceptance_id` 为非空文本、列表元素恰好三个键；存成普通 dict/list（`to_json` 原样输出）。`from_json` 缺 `support` 照默认空——空的含义见第 3 条。
2. `SDK/orchestrator/commit_service.py`：
   - `_record_knowledge_support` 改名 `_knowledge_support`，只计算不写库，返回上面的 dict（成员取法照现有：来源验收 `acceptance_id_for(task.id, envelope.id)`；确认引用的产物或 `record.evidence` 命中的产物带版本与内容哈希；`used_knowledge` 里同任务、非自身的知识带版本与内容哈希）。
   - 已验证结论分支：先算 `support` 再构造 `KnowledgeRecord(..., support=...)`，删 `dependencies=envelope.used_knowledge`；系统测试观察分支：`replace(record, verifier=..., support=self._knowledge_support(...))` 后一次 `upsert_knowledge`。**不再调 `HtnStore.insert_justification_set`**。
   - `KnowledgeCommitted` 载荷：`support_set_id` 换成 `support`（整份 dict，供审计与重放）。
   - 删掉 `KNOWLEDGE_SUBJECT` 的导入（不再用）。
3. `SDK/memory/knowledge_standing.py` `knowledge_standing()`：不再调 `HtnStore.list_justification_sets`，直接读 `record.support`：
   - 状态不是 `VERIFIED` 或已被取代 → `SUPERSEDED`；
   - `support` 为空 → `STALE:no_support`（开发期不兼容：没有依据记录的知识不算当前；生产里所有知识都经第 2 条写入，必有 `support`）；
   - `acceptance_id` → `acceptance_is_current()`（不变）；`artifacts` 每项 → `_artifact_is_current()`（不变）；`knowledge` 每项 → 取上游、比版本、递归（不变，`_seen` 防环照旧）。
   - 模块说明里"`justification_sets` / `support_members`，入库时与验收同一事务写下"改为"知识记录自带的 `support` 字段，与知识行同一次写入，建成后不改"。删 `SUBJECT_KIND` 常量与导出（无其它用处）。
4. `SDK/memory/code_observations.py:118`：删 `dependencies=envelope.used_knowledge`（`Claim` 的 `dependencies` 是结论合同字段，不动）。
5. `SDK/context/retrieval.py:347`：输出键 `dependencies` 保留（读工具结果形状不变、工具说明不动），值改为 `[f"{k['id']}@{k['version']}" for k in record.support.get("knowledge", ())]`。
6. 不动：`SDK/storage/htn_store.py`（`insert_justification_set`、`list_justification_sets`、`support_dependency_edges` 原样留，去留归 D）、`SDK/orchestrator/repair_impact.py`、保证通道全部文件、迁移。
7. 重放清单 `SDK/observability/business_replay_inventory.json`：`justification_sets`、`support_members` 两条**不动**（缺口保留，去留归 D）；`knowledge` 条目加 `note`："支持集合记在 json 的 support 字段，与知识行同一次写入、建成后不改；是否当前为读时推出，不落库"。
8. 用例：第四节用例 3 的断言改（见第四节）；把已跑出的"单步任务收尾报 `EVIDENCE_STALE`"的产品同形用例留作回归用例，并加一个两步变体：第二步写入知识后，第一步验收证书的 `changed_items` 为空、收尾不含 `EVIDENCE_STALE`。

---

## 四、计划文字怎么改（可直接粘贴）

### 4.1 开工裁决 1.4 节

- 结论段改为：
  > **结论：不重建"写已过时"的生产方。知识是否当前，在每次被读（读工具、上下文推送、`used_knowledge` 核对、审查包取条目）时由一个函数 `knowledge_standing()` 当场判定；判定依据只读这条知识记录自带的 `support` 字段（见 1.5）。**
- "判定规则"一条里"②支持集合里的验收……③支持集合里每个证据产物……④支持集合里引用的上游知识"三处"支持集合"改为"`support` 里"，其余不变。

### 4.2 开工裁决 1.5 节（整节替换）

> ### 1.5 知识的依据记在知识记录里；没人用的支持集合表本阶段不接，去留归 D
>
> **结论：`KnowledgeRecord` 加 `support` 字段（来源验收编号；证据产物的编号、版本、内容哈希；用过的上游知识的编号、版本、内容哈希），与知识行同一次写入，建成后不改；`knowledge_standing()` 只读它。`KnowledgeRecord.dependencies` 删掉（与 `support.knowledge` 是同一件事，只留一处）。`justification_sets` / `support_members` 不写。**（2026-10-03 偏差裁决改，见 `HTN补齐-阶段C-偏差裁决-知识支持集合.md`）
>
> - 事实：这两张表是保证通道的源表（`SDK/storage/assurance_source_inventory.py:13-14`、`assurance_barrier_v26.sql`），每张使用证书都完整读它们、不分主体类型（`SDK/storage/assurance_reads.py:279-291`），并作为验收支持图的输入（`SDK/orchestrator/assurance_validity.py:501-533,830-860`），收尾复查就比这三类查询集（`SDK/orchestrator/assurance_recheck.py:25`）。往里写知识支持集合会让本任务全部现行验收证书和根结论证书"依据已变"，收尾报 `EVIDENCE_STALE`（已用产品同形用例跑出）。`knowledge` 表不在通道清单里，写知识行不碰证书。
> - 代价：修复影响分析（`SDK/orchestrator/repair_impact.py:53`）的影响清单里不再列知识编号；没有代码按它做事，知识过时本来就读时判定。重放清单里这两张表"生产无写方"的缺口保持原样。
> - 两张表与计划读集的 `SupportSetRead`（`SDK/contracts/htn.py:2250`）一起，接上或删归 D。

### 4.3 C4 第 1 条（`knowledge_standing` 那一小条替换）

> - `knowledge_standing(store, record) -> "CURRENT" | "SUPERSEDED" | "STALE:<原因>"`：按 1.4 的四条只读 `record.support`；`support` 为空判 `STALE:no_support`（开发期不兼容）。不读 `justification_sets`。

### 4.4 C4 第 2 条（"同一事务里调 `HtnStore.insert_justification_set`……"那一小条替换）

> - 构造 `KnowledgeRecord` 前由 `_knowledge_support()` 算出 `support`（来源验收 `acceptance_id_for(任务, 结果)`；确认引用或 `evidence` 命中的产物带版本与内容哈希；`used_knowledge` 里同任务、非自身的知识带版本与内容哈希），随知识行一次 `upsert_knowledge` 写入；系统测试观察那条用 `replace` 补上后再写。**不写 `justification_sets` / `support_members`。** 删 `KnowledgeRecord.dependencies` 及其写入（`commit_service.py`、`SDK/memory/code_observations.py:118`），读工具展示（`SDK/context/retrieval.py:347`）改从 `support.knowledge` 生成同名键。`KnowledgeCommitted` 事件载荷加 `basis`、`support`。

### 4.5 C4 第 5 条（整条替换）

> 5. 重放清单 `business_replay_inventory.json`：`justification_sets`、`support_members` 两条不动（缺口保留，去留归 D）；`knowledge` 条目加 `note`："支持集合记在 json 的 support 字段，与知识行同一次写入、建成后不改；是否当前为读时推出，不落库"。

### 4.6 第四节用例 3 预期列（附带，必须同步）

> 第 1 条知识 `VERIFIED`、`verifier.basis=="review_confirmed"`、带记录编号；`support` 含来源验收编号与产物（编号、版本、内容哈希）；本任务 `justification_sets`/`support_members` 无行；第 2 条仍是"有支持"；验收成功；收尾评估不含 `EVIDENCE_STALE`

并在第六节"不在本阶段做的"表格中，`SupportSetRead` 那一行事项改为："计划读集的 `SupportSetRead` 与 `justification_sets`/`support_members` 两张表接上或删、`manager_epoch` 删"。

### 4.7 主计划 `HTN补齐计划-2026-10-02.md`

- 标题改为"第 3.9 版"。
- 阶段 C 第 1 条里"知识被读时由一个判定函数按支持集合判定"改为"知识被读时由一个判定函数按知识记录自带的依据（`support`）判定"；"原先没人写的支持集合表接上，作为知识依赖的唯一记录处"改为"依据与知识行同一次写入、只记这一处；原先没人写的支持集合表不接（它是保证通道源表，写入会让全部验收证书失效），去留归 D"。
- 表二第 8 条"没人用的支持集合表二选一"后补"（第 3.9 版：本阶段不接，归 D）"。
- 修订记录加一行（放在第 3.8 版之后）：

> - **第 3.9 版（2026-10-03）**：阶段 C 偏差裁决（`HTN补齐-阶段C-偏差裁决-知识支持集合.md`）：按原裁决把知识依据写进 `justification_sets`/`support_members` 后，单步任务收尾报"依据已变"而失败——这两张表是保证通道源表，每张验收证书都完整读、不分主体类型。改为依据记在知识记录的 `support` 字段、与知识行同一次写入，删 `KnowledgeRecord.dependencies`；两张表本阶段不接，去留并入 D；不动保证通道、不加迁移。改阶段 C 第 1 条与表二 8。
