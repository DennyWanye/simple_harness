# H1-H 前置依赖包：继承既有裁定，不重新设计

依据：H1H-ADM-1.0 §2–§8；本文仅列生产依赖、allowlist归属与测试定位。当前用户报告三个来源仍未闭合。

## 1. Producer矩阵

| 接缝 | 唯一来源/关联键 | 生产/读取 | 撤销、缺失、恢复语义 | focused |
|---|---|---|---|---|
| Authorization | facade固定Principal/tenant；Mission归属；`planning_lane_grants(grant_id,revision)`；原request绑定的grant hash | `PlanningAuthorizationApi.issue/renew/revoke`经Commit写；`build_planning_authorization`一致只读 | 规划权≠Method前提≠动作审批；TTL/新revision按原补遗。缺grant拒绝；读取失败SourceUnavailable；renew/revoke使旧请求stale；恢复不自动签发 | A01–A08 |
| Operations | frozen `OperationEnvelope`→显式identity bridge→精确`action_key/version/params_hash`→原receipt/history；退役work也扫描 | `propose_action`同事务写link；`build_operation_snapshot`读全集合；`read_running_work`由原instance→occurrence→Task→Attempt/intent | 不按target猜join；空集合须完整读证明；缺关联保持blocked；lease/Task.CANCELLED不证明settled；mapper不触发reconcile重发 | O01–O10 |
| Plan shape | 原decision/request→真实typed Proposal→同一ground/compile→`validate_delta`完整报告 | `pre_admit…`不要求shape；`preview_candidate`只计算；`admit_compiled_plan`判断 | 不默认空shape；NOT_CHECKED/PARTIAL_CHECK不放行；非法candidate不先取消；合法但在途用DEFERRED，原decision可恢复不重问模型 | P01–P10 |
| Commit | 原Command身份 + request/decision + 上述真实读集 + 原PlanReceipt | `CommitService.commit_admitted_plan`为唯一新协议外层；调用原`commit_plan_revision`同connection/同外层写事务 | caller→历史receipt幂等→现grant/operation集合/运行状态/read-set重验→原Commit→decision关联；任何失败共同rollback；不另开connection | I01–I08 |

各读事务：同orchestrator库一致读，结束后做纯预览；最终新写事务重新读取。跨execution库只沿原持久导入/回执，不ATTACH伪造跨库事务。模型reason_refs/visible_refs不是安全读集的替代品。

## 2. `commit_admitted_plan` 的真实就绪定义

源文件可在已审阅等价位置，但必须同时满足：
1. 属于现有CommitService（组合/mixin也可），不是TaskGraph新Service。
2. `PreAdmittedPlanningDecision`类型不能直接进入该入口；需要精确candidate/delta/source identity。
3. 授权只读取真实request侧绑定，operations比较完整集合digest（新增成员也被发现）。
4. 所有业务写调用原Store同connection。原`commit_plan_revision`不得在内层提交外层事务。
5. decision COMMITTED与原PlanReceipt、新图后续记录在一项业务事务中；不能先返回PASS再异步补关联。
6. Replay先验证当前读权，再按原command内容返回原receipt，无新grant/plan/dispatch。
7. 非改图决定不调用它；两种decode-only决定无新图写入。

probe检查函数存在只满足第0步。以上每条必须有真实focused/故障测试。

## 3. 明确allowlist（SDK前缀 `src/agent_orchestrator/`）

| 文件 | H1-H允许变更 | 本次是否扩大 |
|---|---|---|
| `governance/planning_authorization.py` | policy、grant snapshot、纯判断 | 否，H1H-ADM已有 |
| `api/planning_authorization.py` | 固定caller的issue/renew/revoke | 否 |
| `runtime/planning_operations.py` | 精确mapper、running-work reader | 否 |
| `storage/admission_seams_schema.py`、`storage/planning_admission_store.py` | 四表唯一storage | 否 |
| `storage/schema.py` | 注册下一未占用迁移；不改旧checksum | 否 |
| `storage/planning_decision_store.py` | request侧绑定与审计同事务 | 否 |
| `planning/decision_admission.py`、`planning/decision_adapter.py` | 分阶段类型及原转换复用 | 否 |
| `planning/admission_sources.py`、`planning/plan_preview.py` | 一致只读装配与纯预览 | 否 |
| `orchestrator/planning_admission_commits.py` | 唯一grant/request/最终计划提交入口 | 否；当前报告缺失，不能移给TG绕过 |
| `orchestrator/commit_service.py`、`action_commits.py` | 组合新入口、真实action/link与handoff门 | 否 |
| `orchestrator/hierarchical_dispatch.py`、`event_handler.py` | 只接线、原收敛事件；preview无副作用 | 否 |
| `planning/htn/compiler.py` | 仅抽纯计算必需调整，旧语义不变 | 否 |
| `tests/orchestrator/full_target/h1h_admission/` | 原36组/12mutations映射或补测 | 否 |
| `storage/store.py::_apply_migration` + 一个窄SQL iterator | **仅当本地runner触发器不兼容，经TG-A-RUNNER-01单独审阅后允许迁移基础设施小修** | 这是条件式扩大；目前只准离线验证与拟议patch，不直接改dirty候选 |

Host变更仍只限source-map已确认的认证/Mission入口；没有真实路径，不授权`backend/**`。本次准备活动不写Host运行代码。

## 4. 输入端测试不能只测dataclass

A测试必须实际issue/request绑定/revoke/Commit；O必须实际propose_action+link和原receipt，不能人工填reconciled=True；P必须真实compiler/validator，不能返回固定shape。仅provider/受控测试目标可以替身。

原36组完整输入与断言在 `h1h-nodeid-map.json`，暂定nodeid路径如下，**尚未确认本地存在**：
- `.../h1h_admission/test_authorization.py::test_A01` 到 `test_A08`。
- `.../h1h_admission/test_operations.py::test_O01` 到 `test_O10`。
- `.../h1h_admission/test_preview.py::test_P01` 到 `test_P10`。
- `.../h1h_admission/test_integration.py::test_I01` 到 `test_I08`。
已有等价测试填写actual_nodeids/完整coverage，不创建重复的空测试壳。

## 5. 解锁TG-B的收据

`h1h/closure.json`必须包含：candidate HEAD、完整dirty指纹、实际36组nodeids及断言覆盖、12变异行为失败证据、三个producer文件hash、Commit调用边/事务证明、独立review结论、原证据相对路径/hash。

新H1H修改进入候选后重新采集source-map。旧指纹下的测试不能为新字节自动背书。解锁TG-B不等于H1完成；H1-I、完整H1、TaskGraph42组仍待运行。
