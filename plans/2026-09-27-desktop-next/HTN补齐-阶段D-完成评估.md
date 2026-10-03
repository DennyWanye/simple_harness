# HTN 补齐 · 阶段 D 完成评估

- 评估人：独立评估子代理（只读代码与文档；除本文件外没有改任何文件，没有动 git）。日期 2026-10-03。
- 评估对象：工作树 `simple_harness-a4`，分支 `htn-d`（相对 `main` 的 15 个提交，`git diff main...htn-d`）。
- 对照依据：`HTN补齐计划-2026-10-02.md`（第 3.12 版）阶段 D 与第六节；`HTN补齐-阶段D-开工裁决与施工清单.md`（下称"清单"）；`HTN补齐-阶段D-偏差裁决-观察重读.md`（下称"观察重读裁决"）；`HTN补齐-实施记录.md` 阶段 D 一节（下称"施工记录"）。
- 路径缩写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`。行号均为 a4 工作树当前版本。
- 本次跑过的测试：只跑了 1 个文件 `T/full_target/test_business_replay_inventory.py`（为确认一处疑似缺陷），结果 **1 红 2 绿**，详见第七节。其余全部靠读代码。

---

## 〇、结论

1. **主体做完了，做得是对的。** D1～D8 的骨干全部落了地：整数闸门与管理纪元删干净（全库 `manager_epoch`/`budget_grant_revision`/`base_graph_version`/`graph_version` 已无残留）；读集补了作用域纪元与义务；过期拒绝不算答错；义务账读时推出；两张支持集合表与"存储规则 + 部署观察"验收路径删掉、迁移 39 写对了且没碰 1～38 的原文；输入默认跟随、可声明钉住；写入目标与运行期写入冲突；内容步骤带固定效果身份；三样桌面观察、同事务加纪元、世界变了才重读；规划器提示词 v20 六项都写了。
2. **观察重读裁决的四个落点全部按裁决做了**（第二节逐条）。
3. **有 1 处必须改的缺陷**：观察表新加了 `question_json` 列，但业务事实覆盖清单没登记，守护测试 `test_every_table_and_field_of_the_current_schema_is_classified` **当前是红的**；施工记录写的是"守护测试跟着过"，与事实不符。改一行 JSON（外加部署清单哈希）即可。
4. **没登记的偏差 16 条**（第四节）。多数是文档或小处做法不同，可以"补登记、不返工"；其中 4 条有实质，建议合并前顺手改掉或正式走裁决：①提交阶段的"读集过期/计划版本过期"仍算规划器答错；②"过期不算答错"的接线没有任何用例钉住，改坏检验只做了 7 条（清单要 14 条）且没登记；③观察器对"同一路径两份现行产出"没有按清单答"看不了"，施工方偏差第 5 条的解释与事实不符；④写入目标按子编号查 `file:` 要求，叶子步骤改了子编号就漏掉编译期冲突检查。
5. **施工方自述的 8 条偏差**：7 条如实且可接受；第 5 条后半句不如实（见上③）。
6. **硬约束**：没有新增"遇到某种语义情况就那样办"的规则；没有加兼容分支；`TOOL_SCHEMAS` 与迁移 1～38 文字没动。有两处"同一件事写了两份"（命题键公式、产物"现行"判断），属轻微违反"只留一条路径"，建议合并前收成一处。
7. **总评：改掉第七节"必须改"的 3 项后可以合并；发版前再补 ARCHITECTURE 与实施记录。** 不需要返工任何一步的主体代码。

---

## 一、清单 D0～D8 逐条对照

判定：做了 / 部分 / 没做 / 做法不同。"登记"列：施工记录里有没有写。

### D0 金丝雀核对

| 条 | 判定 | 依据 | 登记 |
|---|---|---|---|
| 1 观察写入让收尾"依据已变" | 做了 | 施工记录 D0 结论① | 是 |
| 2 过期拒绝的记账路径 | 部分 | `REQUEST_BINDING_STALE` 路径确认并改了；提交阶段 `READ_SET_STALE`/`PLAN_REVISION_STALE` 的路径没写结论（见未登记 U2） | 否 |
| 3 接受一侧因纪元过期被拒后是重建还是重新审阅 | 没写进记录 | 清单要求"后者写进实施记录"；记录里没有这一条 | 否 |
| 4 冻结输入按"跟随"再比会被拒 | 做了 | 记录 D0 结论④；`SDK/artifacts/taskgraph_inputs.py:53-54` 删了比对 | 是 |
| 5 桌面授权放行 `REQUEST_EVIDENCE` | 做了（以用例代证） | 用例 11 跑通取证；记录没单独写结论 | 否（轻微） |
| 6 尝试计次的现行判定函数 | 做了 | `SDK/orchestrator/obligation_accounts.py:18` 复用 `failure_classes.charges_attempt` | 否（轻微） |
| 7 内容步骤只写隔离工作区 | 做了（以注释代证） | `Host/hierarchical.py:80-84` 注释 | 否（轻微） |

### D1 读集与闸门

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 `htn.py` 删三字段、删 `SupportSetRead`、`OutputValue.pin` | 做了 | `SDK/contracts/htn.py:350-359`（pin 校验、只在为真时写出）、`:409-410`（解析可选 pin）；`SemanticReadSet` 三字段与 `SupportSetRead` 已删 | 编码清单 `network_codec_manifest_v5.json` 在分支内改了 3 次（D1、D4、D7 各一次，D7 那次因为 `evidence_state.py` 也变了），合并后对 main 只是一次变化，不影响；"旧图历史读不出"没在记录里写明（U15） |
| 2 编译器读集补纪元与义务；`graph_repair` 两处同补 | 做了，取值时点不同 | `SDK/planning/htn/compiler.py:1354-1459`；`SDK/planning/htn/graph_repair.py:113-116,271-273`；`SDK/planning/plan_preview.py:80-113,364-371`；纪元与义务在 `SDK/orchestrator/event_handler.py:2348-2380` 预览冻结时读 | 清单要"规划请求绑定时的那份纪元"，实际是"预览冻结时现读"（U10） |
| 3 删第 3、4 道闸与 `base_graph_version`，改模块文档 | 做了 | `SDK/orchestrator/plan_commits.py:4-22`（文档）、`:352`（只剩读集与计划修订号）；`hierarchical_dispatch.py` 命令不再填 | — |
| 4 `_read_set.py` 删两个核对、改通道表 | 做了 | `SDK/orchestrator/_read_set.py:14-33`（九通道表）、核对器构造参数与两个函数已删 | — |
| 5 `manager_epoch` 其余各处删；读集索引行删两行、加纪元行与义务行 | 部分 | 各处删干净（全库 grep 只剩 `method_proposals.py:165` 一句旧注释）；`SDK/storage/htn_store.py:1713-1738` 删了两行，纪元行原本就有，**义务行没加**（U8） | — |
| 6 问人题目绑定去掉纪元 | 做了 | `SDK/storage/planning_human_store.py:59-60`；`event_handler.py:8931`、`:9060-9061` | — |
| 7 `facade`/`diagnostics`/`attempt_execution` 删 `graph_version` | 做了 | 另外顺带删了 `Host/projection.py` 的 `graph_version`（前端 `tauri-app/src` 已 grep 确认没人读） | — |
| 8 错误码表加"计入答错"列，`_planning_attempts` 读它 | 部分 | `SDK/contracts/error_table.py:52-76`、`event_handler.py:6051-6052`；**提交阶段两种过期仍算答错**（U2）；**这条接线没有用例钉住**（U3） | — |
| 9 两张禁止字段表 | 做了 | `SDK/planning/decision_codec.py`、`SDK/planning/htn/method_proposals.py:76-79` | — |

### D2 义务汇总账

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 新文件 `obligation_accounts` | 做了 | `SDK/orchestrator/obligation_accounts.py:20-59`：尝试数、失败数（按 `charges_attempt`，基础设施失败不计）、已结算 token、用量未知的尝试数，沿上级义务逐级加总 | — |
| 2 规划包义务视图读真数；计划来源两通道排除这几个数 | 做了，做法不同 | `SDK/orchestrator/planner_views.py:112-126` 读真数；清单要"`ObligationAccountView` 的三字段改由它填"，实际是**把三字段从视图里删了**，规划包直接读函数（U6，做法更合"只记一处"，可接受）；`taskgraph_plan_sources.py:208-211` 因视图已无这几个字段，两通道自然不含 | — |
| 3 删两个写函数、账本算术、`load_ledger`/`persist` 读写 | 做了 | `SDK/storage/obligation_store.py`、`SDK/contracts/obligations.py` | — |
| 4 对外快照加 `obligation_accounts`；Host 诊断导出加一节 | 部分 | 快照做了 `SDK/api/facade.py:705-707`；**Host 诊断导出没加**（`Host/diagnostics.py` 只删了 `graph_version`）（U7） | — |

### D3 删存储规则支持路径 + 迁移 39

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 迁移 39：删触发器、删两表、删义务三列并重建更新触发器 | 做了 | `SDK/storage/schema.py:755-795,836`；同一迁移给 `observations` 加 `question_json`（观察重读裁决要求）；`T/full_target/test_schema_drop_support_sets.py` 钉住"重建的触发器 = 迁移 38 原文去掉三列子句"、迁移 16/26 原文不变 | 清单写"`htn_schema.py:466-480` 同步"——那是迁移 16 的原文，**不改是对的**（改了就违反"已发布迁移不许改"）；但文件末尾 `TABLES` 列表（`htn_schema.py:612-613`）仍列两表，没人用，补登记一句即可（U14） |
| 2 来源清单删两表、义务表删三列 | 部分 | `SDK/storage/assurance_source_inventory.py` 两表与三列已删；**`observations` 没加 `question_json`**（与第七节缺陷同源） | — |
| 3 评估器、证书签发、收尾复查、读集、`HtnStore`、`repair_impact` | 做了 | `SDK/knowledge/assurance_sources.py:310-413`（只剩审阅与检查两类锚点）；`SDK/orchestrator/assurance_validity.py`（`_snapshot_sources` 删）；`SDK/orchestrator/assurance_recheck.py:43-57`（只比钉住对象）；`htn_store.py` 两表读写与 `support_dependency_edges` 删；`repair_impact.py` 删两行 | 完整读集经 `MISSION_TABLES` 自动少两表（`SDK/storage/assurance_reads.py:283`），不会去读已删的表 |
| 4 重放清单删两表条目、义务三列与两写方 | 部分 | `business_replay_inventory.json` 这几项删了；**`observations` 条目没加 `question_json`，守护测试红**（第七节必须改 1） | 施工记录说"守护测试跟着过"，不符 |
| 5 D0 第 1 条用例转绿 | 做了（并入用例 12） | 用例 12 断言收尾无 `EVIDENCE_STALE` | 已登记（偏差 2） |

### D4 跟随授权版本与声明钉住；子目标端口格式

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 `accepted_outputs` 填授权版本；一个端口两个计数中的版本不填 | 做了 | `SDK/orchestrator/hierarchical_dispatch.py:1640-1649`；子目标别名同一取法 | "不填并记原因"——没记原因，消费者那边报"授权版本读不到"，可接受 |
| 2 编译器默认跟随，`pin` 写钉住 | 做了 | `compiler.py:741-748`；`SDK/planning/htn/grounding.py:852-861` `pinned_flows` | — |
| 3 钉住版本的生产方 | 做了 | `hierarchical_dispatch.py:1117-1143` 从冻结输入记录取最早一次；没有冻结记录时取当时授权版本（`SDK/artifacts/input_bindings.py:606`）；施工中修过一次查错列的真问题（记录已写） | — |
| 4 跟随边读不到授权版本报 `REVISION_NOT_AVAILABLE` | 做了 | `input_bindings.py:608-623` | — |
| 5 共享校验与编译器实际策略比；冻结复核删跟随比对 | 做法不同 | `SDK/graph/taskgraph_sharing.py:115-121` **直接不比版本策略**，不是"与编译器会产出的策略比"（U9）；冻结复核删了（做了） | — |
| 6 子目标端口格式不一致不出别名并记原因 | 做了，做法不同 | `hierarchical_dispatch.py:1700-1714`；原因记成 `GoalPortSchemaMismatch` 事件，不进消费者输入结果 | 已登记（偏差 7） |
| 7 HDDL 导出不动、实施记录写明 | 代码没动（对）；记录没写 | — | U15 |

### D5 写入目标与写入冲突

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 `file:X` 链接到的叶子记写入目标；编译期冲突退回 | 做了，有漏洞 | `compiler.py:1462-1472` `_write_targets`；`grounding.py:823-828`；冲突检查沿用 `projection_validation` | 查文件用的是 `child_criterion_id or parent_criterion_id`；叶子步骤允许改子编号（`registry.py:1849-1876` 只要求子目标步骤保持原编号），改了就查不到文件、编译期检查漏掉（U11）；运行期写入冲突能兜底 |
| 2 运行期扫描、一条 `WriteConflict` 修复请求、不先到先占 | 做了，范围更宽 | `hierarchical_dispatch.py:3542-3584` `write_conflicts`、`:3598-3600` 拒绝取舍；`SDK/orchestrator/planning_repair_requests.py:121-132,465`；`event_handler.py:9726-9729` 不开工；`SDK/graph/projection_validation.py:901-916` 复用资源冲突同一套"有无先后" | "下游不开工"范围更宽，已登记（偏差 6） |
| 3 新触发类型登记 | 做了 | `SDK/planning/htn/repair_decision.py:67`、`repair_adapter.py:52-53`；提示词讲了怎么读 | — |

### D6 共享

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 两处类型加固定效果身份并进类型体哈希，仍是本地写 | 做了 | `Host/hierarchical.py:80-95`；`SDK/testing/product_world.py:81-93` | — |
| 2 确认 `sharing_candidates` 出现可共用生产者 | 自述做了，无用例 | 新测试里没有任何断言碰 `sharing_candidates` 或 `effect_identity`；D6 目前零自动化覆盖 | 用例 10 推迟已登记（偏差 4） |

### D7 桌面谓词、观察器、纪元写方、重读

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 新文件观察器；`build_planning_world` 加 `predicates`；两处注册 | 做了，一处没按清单 | `SDK/planning/htn/observers/workspace.py`；`SDK/planning/htn/world.py:416,443-446,492`；`Host/hierarchical.py:21-26`、`product_world.py:43-47`。三谓词 CLOSED、单观察器、只读库 | ①"同一路径两份现行产出 → 看不了"只对资料表做了（`workspace.py:105-108`），对产物文件没做，文件视图取最高版本（U4）；②"现行"另写一份，没调阶段 C 的同一个函数（U5） |
| 2 观察重读裁决版第 2 条 | 做了 | 见第二节 | — |
| 3 观察重读裁决版第 3 条 | 做了，一处没做 | 见第二节；"与未知前提那一半按命题键去重"没做（U12） | — |
| 4 实施记录写明纪元会动、问人题目不受影响、观察不再让证书过期 | 做了 | 施工记录"告知用户的可见变化"与 D7 段 | — |
| 5（裁决新增）6 处旧用例补参数；`world.py` 快照说明改成新规则 | 部分 | 旧用例补了；**`world.py:297-310` 说明文字仍是"全部合并"的旧说法**（U13） | — |

### D8 提示词 v20、清单、文档、发版

| 条 | 判定 | 依据 | 备注 |
|---|---|---|---|
| 1 v20 六项一次写全 | 做了 | `SDK/runtime/role_templates.py:113`；禁止字段 `:138-141`；义务真实累计与 facts 真值 `:165-168`；写入冲突修复请求 `:192-194`；共用 `:239-240`；取证 `:241-243`；前提写法、一例、开工前都要成立、不写会被自己步骤改掉的前提、`EVIDENCE_REQUIRED`、会重读 `:274-281`；`pin` `:288`；同一文件不交给可同时做的两步 `:293-295` | — |
| 2 钉哈希模板测试重生成；工具说明与执行池身份不动 | 做了（自述 + diff 核对） | 分支内没有任何含 `TOOL_SCHEMAS` 的文件被改 | — |
| 3 实施记录、`ARCHITECTURE/`、需求与场景状态；发版 | 未做（待合并后） | 分支内没有 `ARCHITECTURE/` 改动；施工记录标明"待评估、核验、合并、发版" | 按第六节流程放在合并发版时做，可以 |

---

## 二、观察重读裁决的落点核对

| 裁决要求 | 判定 | 依据 |
|---|---|---|
| 同一观察器对同一命题只取最新一条，不同观察器照旧合并；只改 `from_observations` 一处；`observation_refs` 只列取用的 | **如裁决** | `SDK/contracts/evidence_state.py:382-408`：按 `observer_id` 分组取 `(observed_at_ms, observation_id)` 最大的一条，无观察器编号的各算一个来源；合并逻辑不变；`world.snapshot()` 与读集核对、规划包事实行都没改调用 |
| `truth_change` 放在旁边，写方与重读共用 | **如裁决** | `evidence_state.py:461-469`；写方 `SDK/storage/htn_store.py:1380-1381`，重读 `hierarchical_dispatch.py:3156-3161` |
| 观察行带问题：迁移 39 加 `question_json`；`observe_predicate` 带出；`insert_observation` 必填 `question`、同一条 INSERT 写入、核对能算回命题键；空问题明确报错不回落 | **如裁决**（有一处写成了两份） | `schema.py:759`；`SDK/planning/htn/observation_pipeline.py:67-69,184-186,205`；`htn_store.py:1360-1414`（`question` 无默认值，必须传）；核对在 `htn_store.py:185-198`；读到空问题报错 `htn_store.py:1416-1427`。**命题键公式在 `_observation_question` 里抄了一份**，没有复用 `knowledge/predicates.py:287-297`（U5） |
| 纪元：插入前后同一算法比；之前是 TRUE/FALSE 且之后不同才加；未知或冲突→任意不加；与插入同一事务 | **如裁决** | `htn_store.py:1379-1409`：`with transaction()` 内先算前后、INSERT、再 `bump_epoch(..., bumped_by="observation:<编号>")`；条件 `before in (TRUE, FALSE) and after is not before` |
| 重读只在世界变动时做：世界标记 = 验收编号集合 + 资料表摘要；每个标记读一遍；按 `question_json` 与该行范围读；没变不写；写 `EvidenceReread` 事件（键 = 标记，幂等） | **如裁决** | `hierarchical_dispatch.py:3116-3171`：标记含验收编号与资料表（路径、修订号、版本、被取代、撤销）；用事件幂等键判"本标记已读过"（`append_hierarchical_event` 键前缀为事件类型，`:3801`，与判重键一致）；`truth_change` 相同就不写 |
| 同一轮与"未知前提"那一半按命题键去重 | **没做** | `run_evidence_round`（`:3107-3114`）先重读再跑未知前提那半，没有去重（U12） |
| 读集核对与规划包事实行不动 | **如裁决** | `_read_set.py` 的 FACT 通道、`planner_package.py` 都没改 |
| `world.py` 快照说明改成新规则 | **没做** | `world.py:297-310` 仍写"support counts merged"（U13） |
| 用例 11 补"`question_json` 能算回命题键" | **做了** | `T/product_world/test_desktop_preconditions.py:127-131` |
| 用例 12 按替换文字写 | **基本做了** | `test_desktop_preconditions.py:203-290`：两条观察都在 FALSE 在前、快照 TRUE 不是冲突、事实行出现 TRUE 无 CONFLICT、纪元 0→1 与 `bumped_by`、每标记一次、再跑一轮不再读、读集纪元 1、收尾无依据已变。**缺"该步开工见证的 `scope_epoch=1`"一句断言**（轻微） |
| 改坏检验加两行 | **做了** | 施工记录"去掉同一观察器取最新""关掉重读"两条 |

---

## 三、施工方自述 8 条偏差：是否如实、可否接受

| # | 施工方说的 | 如实？ | 可接受？ | 说明 |
|---|---|---|---|---|
| 1 | 用例 2 没另写，旧用例按渠道已覆盖 | 如实 | 可接受 | 核对器按渠道的"读过→变了→过期"在 `test_plan_commits.py` 有；产品接线（预览读集里真有纪元与义务）由用例 1 的读集断言兜住 |
| 2 | 用例 5 并进用例 12 | 如实 | 可接受 | 用例 12 的观察正是在验收之后写的，断言了收尾无 `EVIDENCE_STALE`；两表删除有迁移测试 |
| 3 | 用例 6 只做了"同一做法重试"的接线检验，"上游重做后下游跟过去"留到联测 | 如实 | 可接受，但要记进联测清单 | 现有用例里上游没变，跟随与钉住冻结的是同一版，**跟随的核心行为端到端没有验证**；选择规则有函数级用例（`T/full_target/test_input_manifest_resolution.py:850-868`） |
| 4 | 用例 10 没写，留到联测 | 如实 | 可接受，但要记进联测清单 | D6 现在零自动化覆盖（连"类型带效果身份"都没有断言），联测必须补 |
| 5 | 用例 7 放在 `test_sub_goal.py`；用例 11 的"看不了"用"库读不了"代表，因为同一路径两份现行资料建不出来 | **前半如实，后半不全** | 前半可接受；后半要补登记或补做 | 清单 1.7 说的"同一路径两份现行产出（并发写，见 2.3）"指的是**产物文件**，不是资料表；产物文件撞路径恰恰是用例 9 造出来的局面。观察器对文件没有这一支，`mission_file_view` 取最高版本（`workspace.py:65-83`），等于"后到先占"（U4） |
| 6 | 运行期写入冲突"下游不开工"比清单宽 | 如实 | 可接受 | 理由成立（桌面步骤只有一个数据输入口）；这是秩序保护，不开工后修复请求交规划器定，规划器加先后或重做一步就能解开 |
| 7 | 端口格式不一致记成事件，不进消费者输入结果 | 如实 | 可接受 | 桌面只有一种格式，这条在产品里触发不了；但规划器从规划包里看不到这条事件，以后若有第二种格式，规划器只会看到"数据未绑定"不知原因——记一笔待办 |
| 8 | "裁决题过期后报成没有结论"那条路径没有用例 | 如实 | 可接受 | 题目不再绑纪元后这条路径暂时走不到，等 E 接上真实写方再补 |

另：施工记录"发现并修掉的两个真问题"的第 2 条（前提所依据的观察进读集）是清单外**新增的机制**（`event_handler.py:2348-2380` 读出每个命题最新观察；`plan_preview.py:97-113` 按做法前提挑出来；`compiler.py:1414-1418` 并入读集）。记录写得如实，方向也是清单 1.2"保留前提见证"的本意，但按第六节流程"计划与实际冲突要先登记偏差、裁决、改计划再写代码"，它跳过了裁决（U16）。

---

## 四、未登记偏差（代码与清单不一致，但施工记录没写）

共 16 条。"处理"列：**改** = 建议合并前改代码；**登记** = 补一张偏差单或在记录里补一句即可，不用改代码。

| # | 清单原文 | 代码实际 | 影响 | 处理 |
|---|---|---|---|---|
| U1 | D3 第 4 条"守护测试跟着过" | 观察表新列 `question_json` 没进业务事实覆盖清单（`SDK/observability/business_replay_inventory.json` 的 `observations` 条目）与来源列表（`assurance_source_inventory.py:332-348`），守护测试红 | 缺陷，不是偏差 | **改**（第七节必须改 1） |
| U2 | 1.9"提交阶段的 `READ_SET_STALE`、`PLAN_REVISION_STALE` 若以拒绝事件记账，同样不计" | 提交被拒时 `commit_rejection_code`（`event_handler.py:6199-6210`）把这两个码映射成 `INTERNAL_CONTRACT_ERROR`，`PlanningRejected` 不带 problems 列表，`refusal_charges_planner([])` 返回"计入"，所以**仍扣规划器次数** | 触发窗口小：真值翻转会先动纪元，回复在准入处按 `REQUEST_BINDING_STALE` 拒（不扣）；只有预览冻结到提交之间又写了东西才会走到这里。但与清单和记录里"请求过期类不扣"的说法不一致 | **改**（几行：提交拒绝原因是这两个码时按请求过期记）或登记 |
| U3 | 用例 1 断言"`_planning_attempts` 为 0"；改坏检验 14 条（12 + 观察重读裁决加的 2） | 用例 1 只断言了 `refusal_charges_planner` 函数本身的返回值（`T/product_world/test_epoch_and_read_set.py:51-55`），没数任务里实际计了几次；把 `event_handler.py:6051-6052` 那两行删掉，任务照样完成（上限 3 次），用例不会红。改坏检验只做了 7 条，缺"读集补纪元与义务、过期不算答错、问人题目不绑纪元、义务汇总账、收尾不比查询集、共享、观察器注册"7 条，记录没说为什么 | "过期不算答错"的接线无人钉住；其余 6 条有用例断言能抓（共享除外），只是改坏没实际跑 | **改**用例 1 加一句"计入答错的拒绝数为 0"；其余改坏检验补登记（可放到 F） |
| U4 | 1.7"同一路径同时有两份现行产出 → 答观察器不可用并写明原因、不写记录" | 只对资料表做了；产物文件按最高版本取一份（`workspace.py:65-83`） | 两个无先后步骤写同一文件时，`file-sha256` 会按版本先后答成其中一份；写入冲突修复请求会同时出现，影响有限 | **改**（文件视图里同一路径有两份互不相同的现行产出时答"看不了"）或登记 |
| U5 | 1.7"'现行'与阶段 C 判产物是否现行用同一个函数"；观察重读裁决"按 `proposition_key` 的公式算回" | ①`mission_file_view` 自己写了一遍"最高 VERIFIED 版本"（`workspace.py:76-82`），没调 `SDK/memory/knowledge_standing.py:74` `_artifact_is_current`；②命题键公式在 `htn_store.py:185-198` 抄了一份 `predicates.proposition_key` | 同一件事两处写，以后改一处忘一处 | **改**（把两处收成一个函数）——轻微，可与 U4 一起改 |
| U6 | D2 第 2 条"`ObligationAccountView` 三字段改由它填" | 三字段直接从视图删了，规划包读函数 | 更合"只记一处"，无副作用 | 登记 |
| U7 | D2 第 4 条"Host 诊断导出加一节读它" | `Host/diagnostics.py` 没加 | E 的"预算去向"也读这里，早晚要加 | 改（一两行）或登记推到 E |
| U8 | D1 第 5 条"读集索引行加义务行" | `htn_store.py:1713-1738` 的分组没有 `obligation_revisions` | 索引表查不到义务读项；读集正文里有，核对不受影响 | 改（一行）或登记 |
| U9 | D4 第 5 条"共享校验改为与编译器对这条声明会产出的策略比" | `taskgraph_sharing.py:115-121` 干脆不比版本策略 | 共享校验变弱一点：旧边的版本策略不同也能顶替 | 登记（或按 `pinned_flows` 算出预期策略再比） |
| U10 | D1 第 2 条"纪元取规划请求绑定时那份" | 预览冻结时现读（`event_handler.py:2348-2380`），且过滤掉所有 `assurance:` 开头的范围（请求摘要只滤 `assurance:mission`） | 准入已按纪元摘要拒过期回复，实际等价；过滤口径差别目前无影响 | 登记 |
| U11 | D5 第 1 条"对链接到该叶子的每条 `file:X` 要求加写入目标" | 按 `child_criterion_id or parent_criterion_id` 去查 `c-user-N`（`compiler.py:1462-1472`）；叶子链接允许改子编号，改了就查不到 | 编译期"两步负责同一文件"检查会被漏掉，运行期写入冲突兜底 | **改**（按 `parent_criterion_id` 查；子目标步骤本来就要求两者相同） |
| U12 | 观察重读裁决第五节"同一轮里与未知前提那一半按命题键去重" | 没去重 | 只记过"尽力而为的否定"的命题，可能同一轮被两半各读一次、多写一条 | 改（几行）或登记 |
| U13 | 观察重读裁决 D7 第 5 条"`world.py` 快照说明改成新规则" | 没改 | 文档与行为不一致 | 改（注释） |
| U14 | D3 第 1 条"`htn_schema.py:466-480` 同步" | 没改迁移 16 原文（**做对了**）；`htn_schema.py:612-613` 的 `TABLES` 仍列两表 | 无行为影响；`TABLES` 没人用 | 登记（说明清单这一句与"已发布迁移不许改"冲突，按硬约束不改） |
| U15 | D0 第 3 条、D1 第 1 条、D4 第 7 条要求写进实施记录的三件事 | 记录里没有：接受一侧纪元过期后的走向；编码清单重写后旧图历史读不出；HDDL 导出不支持"跟随" | 审计缺口 | 登记（补三句） |
| U16 | 清单 1.2 认为"前提见证已在读集里" | 新增"预览冻结时读出每个命题最新观察、按前提挑进读集"机制 | 方向对、记录如实，但没走"先裁决、先改计划" | 登记（补偏差单，事后裁决） |

另记一处设计上的小问题（不算偏差）：`GoalPortSchemaMismatch` 事件是在 `accepted_outputs` → `goal_port_outputs` 这条**读路径**里写的（`hierarchical_dispatch.py:1706-1713`）。键是幂等的，目前没问题；若以后有人在只读视图里调 `accepted_outputs` 会出写入错误。可以留到 G 清点。

---

## 五、12 条用例：写了、合并、推迟

| # | 用例 | 状态 | 位置 | 评价 |
|---|---|---|---|---|
| 1 | 过期回复不扣次数 | 写了，断言偏弱 | `T/product_world/test_epoch_and_read_set.py:30-67` | 读集断言齐全；"不扣次数"只测了函数，没测任务里实际计数（U3） |
| 2 | 预览后纪元或义务变了提交被拒 | 合并（旧用例） | — | 理由站得住（偏差 1） |
| 3 | 等回答的问题不因纪元变化被收回 | 写了 | `test_epoch_and_read_set.py:70-113` | 完整 |
| 4 | 义务账目真实累计 | 写了 | `T/product_world/test_obligation_accounts.py` | 完整；"结算没让在途规划过期"没断言，属轻微 |
| 5 | 验收后写观察不让收尾过期 | 合并进 12 | — | 理由站得住 |
| 6 | 重试跟随上游授权版本、钉住变体 | 部分（接线检验 + 函数级） | `T/product_world/test_input_revisions.py`；`T/full_target/test_input_manifest_resolution.py:850-868` | 理由站得住，但核心"跟过去"要进联测清单 |
| 7 | 子目标端口格式不同不出别名 | 写了（换了文件） | `T/product_world/test_sub_goal.py:97-139` | 完整 |
| 8 | 两步负责同一文件编译期退回、加先后可提交 | 写了 | `T/product_world/test_write_targets.py:48-92` | 完整（但没覆盖改子编号的情形，见 U11） |
| 9 | 运行期撞路径成写入冲突 | 写了 | `test_write_targets.py:116-181` | 完整 |
| 10 | 共用生产者在分支换做法后仍有效 | 推迟 | — | 用户要求"先写完再联测"，推迟可以；但 D6 目前零覆盖，联测必须补 |
| 11 | 未知前提→取证→提交 + 三谓词函数级 | 写了 | `T/product_world/test_desktop_preconditions.py:82-180` | 完整；"两份现行产出"一支没有（U4） |
| 12 | 文件写出后重读翻真值、纪元 0→1 | 写了 | `test_desktop_preconditions.py:203-305` | 基本完整（缺开工见证纪元一句） |

推迟的 3 件（6 的后半、10、8 号偏差那条路径）都符合用户"先把代码写完再一起联测、中途只做功能性测试"的要求，理由站得住。**请在整体联测清单里明确写上：用例 6 后半、用例 10。**

---

## 六、硬约束核对

| 约束 | 结论 | 依据 |
|---|---|---|
| 判断交给模型、系统只管秩序 | **符合** | 写入冲突只"如实报 + 不开工 + 交规划器"，不替它挑哪份；观察器只读库、不判断语义；"`file:` 要求 → 写入目标"读的是做法里的结构声明，不猜步骤写了什么；没有关键词匹配、按任务名分支、按文件个数硬拦 |
| 同一件事只留一条路径 | **基本符合，两处轻微违反** | 删掉了三条旁路（整数闸门、管理纪元闸门、存储规则验收）；义务账只记一处；纪元写方唯一入口在 `insert_observation`。违反：命题键公式两份、产物"现行"判断两份（U5） |
| 开发期不做旧数据兼容、旧路径直接删 | **符合** | 新增代码里没有 `legacy`/兼容/回落分支；空 `question_json` 明确报错；迁移 39 是正常升级，不是兼容分支 |
| `TOOL_SCHEMAS` 不改 | **符合** | 分支改动的文件里没有一处含 `TOOL_SCHEMAS` |
| 迁移 1～38 文字不改 | **符合** | `schema.py` 的 diff 只有新增；`htn_schema.py`（迁移 16）没改；有专门用例钉住 |
| 做好的功能默认开启 | **符合** | 观察器、纪元写方、重读、跟随、写入冲突全部默认生效，无开关 |

---

## 七、合并前必须改

只列真正挡合并的 3 项，都是小改动：

1. **业务事实覆盖清单补 `question_json`**（U1）。`SDK/observability/business_replay_inventory.json` 的 `observations.fields` 末尾加 `"question_json"`（顺序要与 `PRAGMA table_info(observations)` 一致，新列在 `created_at` 之后）；`SDK/storage/assurance_source_inventory.py:332-348` 同步加；部署清单 `taskgraph_deployment_manifest.json` 的文件哈希随之重生成。
   - 证据：本次评估在 `sdk/simple-harness-sdk` 下跑了 `uv run --frozen pytest -q -p no:cacheprovider tests/orchestrator/full_target/test_business_replay_inventory.py`，结果 `1 failed, 2 passed`，失败信息 `InventoryError: table observations fields differ from the inventory`。
   - 施工记录里"守护测试跟着过"一句要改正。
2. **用例 1 补一句真实计数的断言**（U3 前半）：数一遍该任务里会被 `_planning_attempts` 计入的拒绝，断言为 0；并对"去掉 `event_handler.py:6051-6052` 的读取"跑一次改坏检验。没有它，阶段 D 第 2 条的"不算答错"在产品接线上是空的。
3. **把未登记偏差补登记**：U2、U4、U9、U11、U12 五条有行为差异，按第六节流程要么改代码、要么补偏差单走裁决；U5～U8、U10、U13～U16 在实施记录里各补一句即可。建议直接改掉 U2、U4、U11、U12、U13（都在十行以内），省掉裁决。

合并发版时（不挡合并，但发版前要做）：`ARCHITECTURE/` 与需求场景状态更新；施工记录补 D0 第 2、3 条结论；整体联测清单写进用例 6 后半与用例 10；发版、Host 钉版、真机前新建编排数据目录（迁移 39 + 提示词 v20）。
