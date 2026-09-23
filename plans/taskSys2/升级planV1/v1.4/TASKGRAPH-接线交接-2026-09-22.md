# TaskGraph 接线交接：V1.4 去除 NanoJev

交接状态：READY_FOR_TASKGRAPH_WIRING。本阶段核心集成、原生完整效果闭环、冷恢复与回放无副作用均已验收，可以开始接线并继续开发。完整语义回放显示仍 PARTIAL，原完整门禁与 H6/H8 大评测不因此变为 PASS。

## 当前权威入口

1. `ARCHITECTURE/index.md` 与 `ARCHITECTURE/AGENT_ORCHESTRATION.md`。
2. 同目录 `最终集成与端到端交付-2026-09-22.md`：本阶段的范围、真实结果和失败历史。
3. 同目录 `v14-current-source-map-2026-09-22.json`：当前实现入口和安装身份。旧 9 月 20 日的“只有离线准备”交接是历史时点，不应覆盖本次真实 producer 的代码和验收证据。

## 源码与安装身份

- Host：`/Users/denny/projects/simple_harness`，main，HEAD `6c457908e49757035c21c6dc2b252415107a494c` 加保留的未提交改动。
- SDK：`/Users/denny/projects/simple-harness-sdk-h1h-impl`，`codex/h1h-impl`，HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加保留的未提交改动。
- Host 已安装：`simple-harness-sdk==0.13.0.dev20260922+htn.1`，wheel SHA-256 `af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`。
- wheel/manifest：Host `backend/vendor/` 同版本文件。manifest 明确 dirty source、输入聚合 hash、版本改写和未发布状态。候选整体尚未合入 SDK main；不可仅凭 HEAD 重建成缺少工作树改动的版本。
- SDK Store 当前 schema 24，使用正常 Store.open 迁移，不直接改数据库。Host 默认 .venv 的 528 个包内文件已与 wheel 逐一核对。

## 接线点与边界

| 接线职责 | 实际入口 | 必须保留的边界 |
|---|---|---|
| Host 装配与创建 | `backend/deskpet/orchestration/hierarchical.py`、`service.py`；SDK `Orchestrator.install_hierarchical_deployment` | Mission 独立 world；创建根语义/Obligation/Requirements 与 Mission 同事务；用户确认完成要求后才规划 |
| 规划授权 | `agent_orchestrator/api/planning_authorization.py`、`governance/planning_authorization.py` | 使用当前 principal、Mission 与 request_id；不可由图形客户端合成授权对象 |
| 预览与编译 | `planning/plan_preview.py::preview_candidate`、`planning/htn/compiler.py`、`orchestrator/plan_commits.py` | 原 Commit Service 才提交；预览不代替授权、read-set 或当前性检查 |
| 运行/操作快照 | `runtime/planning_operations.py::build_operation_snapshot` / `read_running_work` | 保留真实在途工作和未知效果，不用空数组替代缺失 producer |
| TaskGraph 事实读取 | `orchestrator/hierarchical_dispatch.py::read`、`storage/htn_store.py` | 以当前采用的 plan/occurrence/ORDER/DATA/Resolution 为准；legacy Task.status 仅是显示索引，不作为完成真相 |
| 内容/效果状态 | `OperationCompletionReader`、`completion_status.py::read_occurrence_completion` / `read_current_effect` | 内容准备、效果验收和整体完成不同；compound 的效果归属不能由叶完成冒领 |
| 实际操作 | `operation_runtime.py`、`operation_materialization.py`、原 `runtime/actions.py::ActionExecutor` | 正式意图 → ACTION_PROPOSAL 审查 → 物化 → 原审批/执行 → OPERATION_OUTCOME 审查 → EffectFulfillment；禁止自行调用 connector 或据 action SUCCEEDED 宣布整个 Mission 完成 |
| 人工界面命令 | Host handlers/service；`OperationWorkspace.tsx`、`PlanningAuthorization.tsx` | transport request_id 与 durable command/request 身份分开；丢响应重试同一命令；不重发已执行效果 |

根 Requirements 是整体交付要求；明确 file:/pytest: 条件被投射到叶 Task 的输出和实际验证。根最终评审引用正式的叶验收/效果证明，不伪造根自己执行过 pytest 的回执。

方法定义是共享、不可变的。TRIAL_ADMITTED 仍只适用于原 Mission；新合成请求提供无冲突名称建议，名称不授予准入资格。H6 晋级证据不足的候选不能被当作 ADMITTED 使用。

## 本阶段范围裁定

用户明确不做 NanoJev；H6 大批量晋级评测与 H8 四方案 576 局对比已移出当前阶段并停止自动运行。原 14 场景、历史 H1 专项门禁状态仍按原记录保存；本次交接不将它们全部改写成 PASS。

TaskGraph 接线采用当前 SDK/Host 的真实端口，先完成主体功能再做必要最终验收。用户最新要求不再使用子代理，也不采用 plan-task 编排；不得在主体代码未完成时跑大批量回归。真实模型沿用用户配置的 DeepSeeker `deepseek-v4.1-flash`，凭据只经既有 Provider 路径读取，不输出或复制。

原始证据仅在 Host `.local-test-evidence/2026-09-22/v14-host/native-final-01/`，不得提交或上传 Git。现有工作树、失败回执和其它任务的改动必须保留；不要 reset/clean，也不要覆盖 TaskGraph 既有工作。

## 必须显示的失败/待决状态

真实效果审查若没有完整合法信封，记录 `OperationOutcomeDeferred`，不可因 Action=SUCCEEDED 就显示整体完成；不可自动重复购买审查判决或重发已执行动作。历史 `mission-c362e0c85b00c819` 实际发布成功但审查被输出上限截断，已通过界面取消并保留。当前 Host 新请求默认输出16384，上限32768；已有冻结调用不改写。TaskGraph 应明确投射这一待核对/失败原因，不能仅呈现 legacy READY/排队。

## 最终验收与接手顺序

最终 `mission-5bb7c1fef5597956` 为 `COMPLETED / verification_passed`。1次 Worker执行、3件 VERIFIED产物、1次正式审批/发布/OperationOutcomeAccepted/GoalResolutionCommitted/MissionCompleted；12次 DeepSeek 调用、98229 tokens、0未知。发布哈希 `76c181ff9bd99d9ac39715dbcfd2c622748ca819bbfe27c092ce2ce31e4109b2`。冷恢复及界面回放后1 Attempt / 8 intents / 102 events / 12 calls / 1 action不变。证据hash见最终交付文档。

1. 读取本交接与当前 architecture，以安装manifest和source-map核对代码身份；不再沿用9月20日缺少producer的旧结论。
2. 使用当前安装SDK端口完成TaskGraph最外层生产接线，保留未提交改动，不重装旧wheel、不重置工作树。本次隔离UI/backend已退出，验收端口已释放。
3. 从当前plan、occurrence、ORDER/DATA、正式Resolution和EffectFulfillment读取图状态。旧回放23类新事件未知/1字段未覆盖/UI账本未对齐，覆盖字段差异0；须明确显示覆盖边界，不隐藏为整体PASS。
4. 先主体开发，再必要最终验证；不恢复H6/H8大批评测，不使用子代理，不采用plan-task。

SDK main尚未整体合并、没有release。READY仅表示本阶段接口可供接线，不代表共享方法候选已获得晋级或原V1.4所有门禁通过。

TaskGraph 通知：已通过 Codex 工具发送至「审查 TaskGraph 计划可实施性」（01a0bf90-5fc9-7fe2-bc95-24eac8335dc9），要求开始生产接线并继续主体开发；发送成功不等于 TaskGraph 自身开发已完成。
