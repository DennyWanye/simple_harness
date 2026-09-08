# 决策记录：解除 Procedure 关系端点的具名扣留（F-S1b），并重新裁定 `applied_use_fingerprints`

> 义务：`HM-TO-A6` / 验收 **A6-6**（同一 plan 新建节点 + relation memory）的 **Procedure 形态**
> 前情：事件 S 备忘 `DECISION-S-RELATION-KEYERROR.md` §3「坑一/坑二」、§4.3（具名扣留）、
> §4.4（`check()` 必须失败而不是换一个假门）、§7（未做）、§8 后继 **F-S1**
> SDK 侧：0.6.35 `DECISION-2026-09-09-procedure-relation-endpoint-classification.md`（修坑二）、
> 0.6.36 `DECISION-2026-09-09-analysis-lane-applicability-fingerprints.md`（修坑一）
> 分支：`worktree-f-s1b`（基线 `dbf967fc`，本轮**已 `git merge main` 到 `1e7043e9`**，见 §7.1；未合回 main）
> SDK 分支：`m0636`（`dc70e2b`，工作树 `simple-harness-memory-sdk-0636-work`，未发布、未构建制品、未合 main）

---

## 1. 扣留是为了什么，现在为什么可以撤

事件 S 在正向用例里量到两条 SDK 事实，任何一条单独成立都足以让「放行 Procedure 端点」
**比事故本身更糟**：

| 坑 | 后果 | 谁修的 |
|---|---|---|
| 坑二：观测提交出来的 revision 没有自己的 `cognitive_classification_decisions` 行 | 端点解析抛 `MemoryCorruptionError('relation endpoint classification is missing')`，**整批分析死掉**（同批 episode 一起丢） | **SDK 0.6.35**：管辖某一版的分类决定 = 血缘上最近的已分类祖先 |
| 坑一：`check_history_visibility` 对 Procedure 恒判 stale（写死空指纹集合） | `check()` 整批 `analysis_candidate_no_longer_visible` | **SDK 0.6.36**：离线车道可提交显式 provenance 的适用性指纹，并由 Memory 自己的观测审计佐证 |

两条都闭合，所以 `_endpoint_unverifiable` 里那句「Procedure 一律扣下」可以撤——
**但只在装着的 SDK 真的具备第二条能力时**。

## 2. 事件 S §4.4 的要求已被满足，而且不止于「提交了一份 attestation」

§4.4 的原文要求是：谁解除 F-S1，必须**先设计真正的复核路径**，
不许把 Procedure 候选从 `check_history_visibility` 的绑定里摘出来交给一个什么都不读的谓词。

本轮 `check()` 的 Procedure 候选**仍然是 `check_history_visibility` 的普通绑定**——
一条都没有被摘出去。变的只是这次调用**额外提交**了一份 attestation，
于是 SDK 那道原本恒 False 的适用性门改为按既有判据裁定；
head CAS、`content_hash`、状态门（Procedure 仍必须 `active`/`reinforced`）、有效时间、
隐私类与信息属性、记忆与逐条证据的 suppression、整批的披露与收件人绑定，一条不少地照跑。

**而且 Host 不满足于「签名里有这个参数名」**（评审 MUST-FIX 1）：能力探测只能证明
存在一个**叫这个名字**的参数，证明不了被调用方对它做了什么。一个接受 kwarg 却什么都不做的
后端——将来的重构、部分回移、fork、或经 `backend_factory` 注入的自定义后端——会让 Procedure
端点零适用性复核地进 `authorize_plan`。所以 `check()` 还要复核 SDK 返回的收据：
`observed.procedure_applicability` 必须存在、其 `attestation_hash` 必须等于本次提交的那一份、
且**每一条 Procedure 绑定都必须出现在 `admitted_binding_hashes` 里**；
任何一条不成立即 `analysis_candidate_no_longer_visible`。
一条 `memory.analysis_relation_applicability_admitted` 审计行记下
attestation 哈希、指纹条数与逐条命中数。

## 3. 提交的是「同一份事实」，不是重新求的答案

`check()` 用的指纹**必须**与下发这些候选的那次召回所绑定的完全相同，
否则复核复核的是另一件事。实现：

* `_relation_candidates` 现在把它用过的 `fingerprints` 一起返回；
* `prepare` 把它写进**候选快照**的新键 `relation_applicability_fingerprints`
  （快照是 durable evidence，`bind_attempt` 把整个快照哈希进 attempt，
  `snapshot_for_attempt` 复核该哈希）；
* `check()` 只从快照里读，`sorted(set(...))` 归一后构造
  `ProcedureApplicabilityAttestation(APPLIED_USE_FINGERPRINTS, …)`。

在 `check()` 里重新调一次 `applied_use_fingerprints()` 是错的：那是**另一个时刻**的答案，
重放同一批分析会得到不同的复核输入。

## 4. 能力探测，不是版本号

`sdk_offline_applicability_capable()` 只问两件事：根导出里有没有那三个类型、
`MemoryManager.check_history_visibility` 的签名里有没有 `procedure_applicability`。
按属性而不是版本比较，理由是这才是 Host 真正依赖的东西；
副作用是同一份 Host 代码在 0.6.34/0.6.35 上继续按名扣留、在 0.6.36 上放行。

**两处调用点，两条 fail-closed 分支**：

1. `_endpoint_unverifiable`（下发前）：不具备能力 → `sdk_procedure_applicability_gate_unavailable`；
   具备能力但这一批**没有指纹**可提交 → `sdk_procedure_applicability_absent`
   （后者是纵深防御：指纹为空时 SDK 的召回门本来就不会让任何 Procedure 露面，
   所以生产路径上到不了这一行；它存在是为了不让将来的调用方下发一条本批 `check()` 复核不了的端点）。
2. `check()`（复核时）：快照里有 Procedure 候选，而此刻**不具备能力或没有指纹** →
   `analysis_candidate_no_longer_visible`。这条覆盖的是「新 SDK 准备的快照在旧 SDK 上重放」：
   宁可整批失败，也不把 Procedure 混进一次没有适用性的复核。
   构造 attestation 时的任何异常（SDK 的 1..256 上界、空白串指纹）也被折进这个码，
   不让 SDK 形状的异常从 `authorize_plan` 裸奔出去。

**理由码为什么不叫 `sdk_procedure_endpoint_unresolvable` 了**（评审 MUST-FIX 2）：
0.6.35 已经修好了端点解析，所以在 0.6.35 上「SDK 解析不了这个端点」是**假的**，
会把下一个读到它的人送去错误的 SDK 修复。跨所有缺少 0.6.36 入口的 SDK 都为真的那条事实是
**可见性门**，因此扣留的具名理由改为 `sdk_procedure_applicability_gate_unavailable`。
0.6.31–0.6.34 上另外还叠着端点解析那条，但它不是**唯一**的阻断者，不该独占这个名字。

## 5. 重新裁定 §4.1 的取舍（事件 S 备忘明写「解除时必须重做」）

`applied_use_fingerprints` 保住「曾经被真实用过」、放弃「此刻仍然适用」。
0.6.31–0.6.35 上这笔取舍没有暴露面（所有 Procedure 端点都被扣下）；解除之后它**直接决定
`check()` 放不放行**，所以必须重裁。

**裁定：可以，作为复核依据成立。** 三条理由：

1. **保住的那一半被收紧到「成功且可归因地用过」，而且两半都由 SDK 自证**。
   要说准（SDK 侧评审的纠正）：「提交的指纹必须等于 SDK 自己存的那个值」**本来就**由既有的
   类型权威门保证——它比的是 `procedure_records.applicability_fingerprint`，
   而该列只由观测提交路径写入，Host 编不出来。0.6.36 新增的那一条加的是另一件事：
   要求同一指纹被一条 `outcome='success' AND attributable=1` 的 `procedure_observations`
   背书，于是「用过」不再包含「只是被一次失败观测绑定过」。匹配口径是 memory + 指纹，
   不含 revision 与 epoch（复制链上指纹逐字继承）。两条判据都读 **SDK 自己的**表，
   Host 的 `procedure_uses` 在这条链上一个字节的权威都没有。
2. **佐证来源被链锚定**：`procedure_observations` append-only + 不可变触发器 +
   open 时逐字复核（SDK 侧用例实测：改一列则库开不起来）。
3. **它只是复核，不是授权**：端点在 apply 时仍被 SDK 逐门重解析
   （S3 §2-补.4 的完整门序），Procedure 仍必须 `active`/`reinforced`。

**放弃的那一半，诚实记账（本轮不修）**：
一个工具后来改名 / 换签 / 被撤的 Procedure，`current_fingerprints` 重算不上它，
因此它会退出**前台**召回；但分析车道仍会把它当作关系端点候选下发。
代价是可能出现一条 `applies_to` 指向「当前不可用、但确曾被真实用过」的流程。
可接受的理由：这条关系记忆陈述的是「这个事实适用于那个流程」，
是一条**历史陈述**，其真值并不随工具改名而失效；且该边带 exact revision，
遗忘 / suppression / 冲突 / 状态各门照常对它生效。
彻底消掉它需要 Host 把「适用性快照」本身持久化成可离线重算的事实——
SDK 侧记为 **F-S1c**，Host 侧记为本备忘 §8 的 F-S1B-1。

## 6. 改动清单

`backend/deskpet/memory/semantic_correction.py`：

* 新增 `sdk_offline_applicability_capable()`（`lru_cache`，属性 + 签名探测）与两个理由码常量
  `SDK_PROCEDURE_APPLICABILITY_GATE_UNAVAILABLE` / `SDK_PROCEDURE_APPLICABILITY_ABSENT`；
* `_relation_candidates` 返回四元组，多带 `fingerprints`；
* `prepare` 的两条出口都写 `relation_applicability_fingerprints`；
* `_endpoint_unverifiable(body, *, fingerprints)` 按 §4 裁定；
* `check()` 按 §3/§4 构造并提交 attestation、**复核返回的收据**，或 fail closed；
  新增 `memory.analysis_relation_applicability_admitted` 审计行。

**没有改**：召回本身、`ProcedureRuntime.applied_use_fingerprints` 的行为（只在 docstring 里
补了一句指向本备忘 §5 的交叉引用）、`runtime_composition` 的绑定、v8 提示词与协议哈希、
其余理由码、`authorize_plan` 的动作校验。

## 7. 测试

| 用例 | 断言 | 0.6.34 | 0.6.36 |
|---|---|---|---|
| `test_analysis_relation_applied.py::test_a_really_used_procedure_reaches_the_endpoint_channel`（改） | 真实三次观测进 `active`；指纹进快照且与 `applied_use_fingerprints()` 相等；**然后按装着的 SDK 分两支**：不具备能力 → 具名扣留、提示体里没有该端点；具备能力 → 理由码为空、端点被下发且 `candidate_key` 与提示体一致 | 绿（扣留支） | 绿（放行支） |
| `test_analysis_relation_procedure_applied.py::test_an_existing_procedure_endpoint_is_issued_and_the_relation_is_applied`（新） | 端到端：三次真实观测 → 端点下发 → fixture 模型用 `source_operation_id`+`target_candidate_key` 提 `applies_to` → 这一批 `applied`；`cognitive_relations` **恰好 1 行**，`target_revision` 是**观测提交出来的那一版 head**（`> 1`，正是 0.6.35 之前被判「分类缺失」的那一版）；孪生图 **1 条 `applies_to` 边**且**两端**都在节点集合里 | skip | **绿** |
| `..._procedure_endpoint_withholding_follows_the_installed_sdk`（新，参数化 4 项） | 扣留只在「有能力 且 这一批有指纹」时解除；Prospective 端点从不受影响 | 绿 | 绿 |
| `..._capability_probe_agrees_with_the_installed_facade`（新） | 探测结果必须等于「真实的 facade 签名 + 根导出」——用桩 manager 吞 `**kwargs` 骗不过这一条 | 绿 | 绿 |
| `..._fails_closed_when_the_snapshot_outlives_the_capability`（新） | 新 SDK 准备的快照在老 SDK 上重放 → 整批 `analysis_candidate_no_longer_visible`；**不受 0.6.36 守护**，正好在它重要的那版 SDK 上跑 | 绿 | 绿 |
| `..._check_presents_the_recalled_fingerprints_or_fails_closed`（新） | `check()` 提交的是快照里那一份（排序去重后）且 provenance 正确；没有指纹 / 只有空白串指纹 → fail closed；**没有 Procedure 端点时不提交任何 attestation**（普通批次逐字保持 0.6.35 的调用形状） | skip | **绿** |
| `..._refuses_a_backend_that_takes_the_kwarg_and_ignores_it`（新） | 后端接受 kwarg 却不返回收据、或命中清单为空 → 整批失败（评审 MUST-FIX 1） | skip | **绿** |
| 既有 `test_analysis_relation_candidate_audit.py`(5) / `test_analysis_relation_prospective_applied.py`(1) | 逐字未改 | 绿 | 绿 |

0.6.36 的那一列**不是**主 venv 跑出来的：主 venv 与 `backend/pyproject.toml` 的 pin
（`simple-harness-memory-sdk==0.6.34`）本轮一个字节没动，验证走一份独立叠加 venv
`.claude/worktrees/f-s1b/.venv-0636`（`uv sync` 到 `UV_PROJECT_ENVIRONMENT`，再把 SDK
工作树以 `-e` 覆盖上去）。**装 0.6.34 时本文件三项（端到端 + 两项 `check` 用例）会 skip**，
真正解除扣留还需要另一次「构建 0.6.36 wheel + 改 pin」的交付，不在本轮范围内。

叠加 venv 的构法（可复现，主 venv 与 `backend/vendor/` 的 0.6.34 wheel 均未动）：

```
cd <worktree>/backend
UV_PROJECT_ENVIRONMENT=<worktree>/.venv-0636 uv sync --python 3.12 --extra dev --no-progress
uv pip install --python <worktree>/.venv-0636/bin/python --no-deps \
    -e /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk-0636-work
```

注意 `uv sync` 会**先**把叠加 venv 拉回 pin 的 0.6.34（`backend/vendor/` 那个 wheel），
所以 `-e` 那一步每次都要重跑，顺序不能颠倒。
`deskpet/sdk_adapters/sdk_candidate.py` 的 `SDK_MEMORY_VERSION = "0.6.34"` 硬校验**没有**挡住
本轮任何用例——关系候选/复核这条链不经过候选制品校验路径，因此**不需要**为 0.6.36 打补丁或
跳过那条 pin 检查；它只会在真正切 pin 的那次交付里被改到。

## 7.1 回归（合入 main `1e7043e9` 之后实测）

本轮先做了 `git merge main`（`dbf967fc` → `1e7043e9`，带入事件 T 的分析协议 **v9**「本句点名的
流程建成节点、关系指向它」、事件 U/V 与 F-E3）。**唯一冲突**是
`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md` 的文件头新条目（两侧各加一条），按两侧都保留解决；
`backend/deskpet/memory/semantic_correction.py` **没有冲突**——main 侧的事件 S/T 改动落在
`analysis_proposal_v9.py` / `analysis_protocol.py`，本轮改的是关系端点的可见性门，两者不相交。

合并后逐条重跑（每次只跑一个 pytest 进程，具名文件）：

| 命令 | 主 venv（0.6.34） | `.venv-0636`（0.6.36） |
|---|---|---|
| `tests/memory/test_analysis_relation_applied.py` + `..._candidate_audit.py` + `..._procedure_applied.py` + `..._prospective_applied.py` | **13 passed, 3 skipped** | **16 passed, 0 skipped** |
| `tests/memory/test_analysis_v8_existing_relation.py` + `test_analysis_v9_named_workflow_relation.py` + `test_analysis_proposal_v9.py` | **30 passed** | **30 passed** |

两版 SDK 的差额恰好是那 3 项 `needs_0_6_36`（端到端 + 两项 `check` 用例），其余逐条相同：
即「同一份 Host 代码在 0.6.34 上继续按名扣留、在 0.6.36 上放行」是被实测钉住的，而不是推断的。
v9 合入没有改变本轮任何断言——分析协议换版影响的是**提案形状**，端点可见性门在其下游。

## 7.2 独立评审（opus，只读）与处置

| 评审意见 | 处置 |
|---|---|
| **MUST-FIX 1**：`check()` 提交了 attestation 却从不复核 SDK 返回的收据。能力探测只证明「存在一个叫这个名字的参数」，任何接受 kwarg 却忽略它的后端（重构、部分回移、fork、`backend_factory` 注入）都会让 Procedure 端点零适用性复核地进 `authorize_plan`——§4.4 那个洞被移出一层而不是被堵上 | **已改**（§2 末段）：`check()` 复核 `observed.procedure_applicability`——收据必须存在、`attestation_hash` 必须等于本次提交的那一份、且**每条 Procedure 绑定都必须出现在 `admitted_binding_hashes` 里**，否则 `analysis_candidate_no_longer_visible`；新增 `memory.analysis_relation_applicability_admitted` 审计行。新增用例 `..._refuses_a_backend_that_takes_the_kwarg_and_ignores_it`（收据缺失 / 命中清单为空两种形态）。**部分未采纳**：评审还建议把收据持久化进 attempt 审计——`check()` 在一批里被调用不止一次，且收据依赖调用时刻的库状态（其间可能有遗忘/压制），把它写成「必须逐字相同」的持久行会引入假冲突；本轮以「复核 + 具名审计行」落地，持久化记为 F-S1B-2 |
| **MUST-FIX 2**：`sdk_procedure_endpoint_unresolvable` 在 0.6.35 上是**假的**（0.6.35 已修好端点解析），会把下一个读到它的人送去错误的 SDK 修复 | **已改**：理由码改名 `sdk_procedure_applicability_gate_unavailable`（可见性门是跨所有缺少 0.6.36 入口的 SDK 都为真的那条事实），用例与两处 docstring 同步（§4 末段） |
| **MUST-FIX 3**：本工作树里 pin 仍是 0.6.34，两项 `needs_0_6_36` 会 skip，「0.6.36 绿」不可复现 | **口径已补，pin 不动**：本轮明确不改 Host pin（有原生旅程正在主树跑）。0.6.36 那一列由独立叠加 venv `.venv-0636` 实跑取得，路径与构法写进本节与 `ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`；「构建 wheel + 改 pin」另立交付 |
| **MUST-FIX 4**：下发前的判据是**批级**（有能力 + 本批有指纹），复核时的判据是**逐端点**且更强（多一条观测背书），两者不一致时代价是整批而不是一个端点 | **记账不改**（§8 F-S1B-3）：SDK 的背书表在 Memory 侧，Host 看不见，无法在下发前逐端点预判；而端点一旦下发进提示体、模型据此提了关系，`check()` 再把它悄悄摘掉才是更坏的选择（§4.4 的原教训）。健康库上该状态不可达（bound 指纹只由观测提交写入，且 active 需要三次成功观测），SDK 侧只有篡改库用例能构造 |
| **MUST-FIX 5**：attestation 构造可能抛出非 `analysis_candidate_no_longer_visible` 的 SDK 形状异常（>256 条、空白串指纹），从 `authorize_plan` 裸奔出去 | **已改**：指纹过滤改为 `isinstance(f, str) and f.strip()`，构造包在 `try` 里统一折成 `analysis_candidate_no_longer_visible`；用例补空白串一项 |
| **N1** 能力探测未缓存、`import inspect` 在函数内 | **已改**：`@lru_cache(maxsize=1)`，`inspect` 提到模块级 |
| **N2** 探测的是 facade，真正的后端可能拒绝该 kwarg | **部分采纳**：MUST-FIX 1 的收据复核已经覆盖「后端没有真的处理它」这一类；`TypeError` 那一路仍会以 SDK 形状冒出，记入 F-S1B-2 |
| **N3** `attestation is not None` 不校验形状 | **已改**：加 `isinstance(member, provenance)` |
| **N4** `_endpoint_unverifiable` 的 `fingerprints=()` 默认值 | **已改**：改为 keyword-only 且必填 |
| **N5** `SDK_PROCEDURE_APPLICABILITY_ABSENT` 生产路径不可达 | **已改** docstring，明写它是纵深防御 |
| **N6** 「逐字」其实是「同一个集合」 | **已改** 注释措辞 |
| **N7** 指纹在每条出口都持久化，读起来像有条件 | **已改** 注释措辞 |
| **N8** 「新快照在旧 SDK 上重放」那一项被 `needs_0_6_36` 挡住，恰恰在它重要的那版 SDK 上不跑 | **已改**：拆成独立的无守卫用例 `..._fails_closed_when_the_snapshot_outlives_the_capability` |
| **N9** 桩 manager 吞 `**kwargs`，没有任何断言钉住真实 facade | **已改**：新增无守卫用例 `test_capability_probe_agrees_with_the_installed_facade`，直接比对真实签名与根导出 |
| **N10/N11** 循环变量外泄、链式比较 | **已改** |
| **N12** 佐证是 memory+指纹而非 revision 精确 | **已改**：`_endpoint_unverifiable` docstring 与本备忘 §5 都写明这一点（SDK 侧同步写进 S3 `§2-补2.4`） |
| **N13** 「attestation 记录在返回的快照里」对 Host 不成立 | 随 MUST-FIX 1 一并解决 |
| **N14** `applied_use_fingerprints` 的 docstring 没有指向本轮的重裁 | **已改**：补一段交叉引用（行为未动） |

## 8. 后继

* **F-S1B-1（P2）**：把「适用性快照」持久化成可离线重算的事实，消掉 §5 放弃的那一半
  （对应 SDK 的 F-S1c）。今天的暴露面已在 §5 记账。
* **F-S2**（事件 S 备忘 §8）：离线车道用 `request.run_id` 构造 `RecallContext` 而指纹来自
  另一组事实。SDK 已在 S3 `§2-补.6` 把口径写进契约；字段改名是 wire 变更，另立。
* **F-S1B-2（P2）**：把 `check()` 拿到的适用性收据（或至少 `attestation_hash` 与逐条命中数）
  持久化进 attempt 审计，并把「后端拒绝该 kwarg」的 `TypeError` 折进 Host 理由码。
* **F-S1B-3（P2）**：下发前的批级判据与复核时的逐端点判据不一致时代价是整批（§7.2 MUST-FIX 4）。
  健康库上不可达，要真正对齐需要 SDK 暴露一个「这个端点今天能不能过适用性」的只读探针。
* **F-S3 / F-S4**（事件 S 备忘 §8）：未涉及。
