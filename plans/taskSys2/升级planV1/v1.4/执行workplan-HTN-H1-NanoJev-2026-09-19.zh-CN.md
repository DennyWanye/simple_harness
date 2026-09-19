# HTN H1 接线与 NanoJev Shadow 执行 Workplan

plan-status: finalized（用户于 2026-09-19 明确授权按本 workplan 开始执行）

日期：2026-09-19

## 目标与范围

本 workplan 以 V2 执行计划和裁定补遗为唯一规格来源，先完成 H1-H/H1-I，再把 `NanoJevAdd.md` 作为独立增量接入。H1 接线与 NanoJev 不合并成一个巨型改动。

当前事实：Host 为 `/Users/denny/projects/simple_harness`；SDK 为 `/Users/denny/projects/simple-harness-sdk`；SDK `main == origin/main == 5ac3f05`。Claude CLI 已安装并验证 4 路并发成功；Grok CLI 禁用。

## 并发与编排规则

- 同时最多运行 4 个 Claude CLI 工作槽，第 5 个日卡槽由另一台电脑使用。
- 每个可写任务使用独立 worktree；实现者与核验者使用不同 Claude CLI 会话。
- 同一热文件同时只允许一个写入者。
- 只读审计、测试设计、NanoJev 契约和模型运行时调查可以并行。
- 不重复实现已经合入的 H1 零件；不为填满并发重复测试。
- Grok 永不调用。
- 记录每个槽的任务、模型别名、耗时、token、实际 diff、测试结果和返工次数。

## 阶段 0：事实与入口审计

四路只读任务：

1. H1-H 热文件、事件流、HTTP 协议字段入口审计。
2. H1-H 最小行为测试与现有测试映射。
3. NanoJev 当前 allocator、retry、event、replay 接入点审计。
4. NanoJev 模型文件、运行时、依赖和本地可用性调查。

阶段 0 不修改业务代码，不运行全量回归。

## 阶段 1：H1-H 测试先行

先写失败行为测试，覆盖：

- 新旧协议双分支；
- HTTP `planning_protocol_version` 合法、未知、缺省三种行为；
- 新协议同时发旧规划拒绝事件和 `PlanningDecisionEvaluated`；
- 旧协议不发新的决定类事件；
- WAIT/NO_CHANGE 只持久化、不编译、不产生计划修订；
- BIND_EXISTING_GOAL/PROPOSE_SUCCESSOR 可解码但本阶段拒绝执行；
- `attempt_ordinal` 从 0 起，复用既有格式重试，最多重试一次；
- 旧协议行为、字节、事件和默认值保持不变。

## 阶段 2：H1-H 实现与独立核验

只让一个实现会话修改 `hierarchical_dispatch.py`、`event_handler.py`、`api/missions.py` 及明确 allowlist 内文件。实现只接线，不新增编译器或状态语义。

另一 Claude CLI 会话只读核验 diff、行为测试、重启/replay、旧路径隔离和至少 12 个行为可区分 mutation。核验者不写生产代码、不提交、不推送。

## 阶段 3：H1-H 完整门禁

开发中先跑接线专项；合入前和合入主干后各跑一次必要完整门禁：

- SDK `full_target` 全量；
- 旧模式 560 条；
- 基线 0 新失败、legacy 0 新失败；
- sentinel ≤22；
- mutation ≥12 全部 killed；
- 独立核验可合。

## 阶段 4：NanoJev Shadow

按以下小片实施，保持 HTN 结构规划不变：

1. 强类型 `DecisionRequest/Result/Provider/Policy`；
2. 包装现有确定性 allocator/retry 为 `ExistingDecisionProvider`；
3. additive Decision 事件与 replay；
4. Fake Provider 驱动的 Shadow Service；
5. optional NanoJev Runtime（MPS/CPU、模型常驻、不提交权重）；
6. NanoJev Provider 转换层；
7. 只接 `READY_TASK_PRIORITY` 与 `RETRY_OR_ESCALATE` 的真实 Shadow。

硬约束：0/1 候选不调用模型；MaxRuns 是 Runtime 硬边界；NanoJev 不能改 DAG；Shadow 返回 Existing 生产结果；Shadow 失败不影响生产；H1 PlanningDecision ID 与 NanoJev `decision_id` 分离。

## 阶段 5：NanoJev Primary（后续门）

Shadow 样本按 DecisionType 分开统计 agreement、high-confidence agreement、verifier pass、retry rate、latency 和 error rate。只有真实数据证明不劣于 Existing baseline 后，才允许 `mode=nanojev`；低置信度、非法输出、超时或异常全部回退 Existing。

## 测试节流原则

- 不在每个小片重复全量回归；只跑与变更和验收门直接相关的测试。
- 热文件接线的合入前/后完整门禁是计划要求，不能省略。
- NanoJev 先跑契约、Shadow failure、0/1 候选、MaxRuns、事件 replay 和一条集成链；共享 Runtime 接线后再跑一次必要回归。
- 原始日志和收据放 `.local-test-evidence/`；Git 只保存结论、命令、状态、索引和哈希。

## 停止条件

遇到计划与源码冲突、模型身份/路径不明、超出 allowlist、旧协议出现新失败，立即停止当前片并记录 blocker；不得用放宽验证、静默 fallback 或删除字段解决。

## 执行记录（2026-09-19）

- 阶段 0：四路 Claude CLI 并发探测成功；宽范围审计任务因边界过大触发最大轮次，未产生业务代码。随后缩小为 H1-H 入口审计、NanoJev 入口调查、契约切片和定向测试任务。
- 阶段 0 结论：H1-H 的真实入口为 `api/missions.py::spec_from_request`、`event_handler.py::_collect_plan`、`hierarchical_dispatch.py::apply_planner_reply` 与 `_record_refusal`；NanoJev 的确定性选择入口为 SDK `scheduling/allocator.py` 的 `frontier`/`allocate` 及 v2 对应函数。
- NanoJev-1 当前产物位于 SDK 独立 worktree `codex/nanojev-contract`：新增 `agent_orchestrator/decision/{types,provider,policy,__init__}.py` 和契约测试；38 个定向 pytest 通过，ruff 通过。
- 独立审阅结论：契约切片可落地；“0/1 候选不调用模型”和“Shadow 不影响生产”必须由后续 DecisionService/Shadow 切片分别实现并测试，不能在契约片宣称已覆盖；嵌套 `Mapping` 的深层不可变、跨请求 `decision_id` 一致性属于后续服务层或 provider 边界的待办。
- NanoJev-1 已提交 `79e5bde`；NanoJev-2 在独立 worktree `codex/nanojev-existing` 基于该提交加入 `ExistingDecisionProvider`，提交 `def698d`。两片合计 45 个定向 pytest 通过，ruff 通过；未接入生产调度器。
- H1-H 行为测试提交在 `codex/h1h-tests`：`0073371` 钉住 HTTP 协议字段行为，`af56b90` 修复 `api/missions.py` 的真实请求映射；协议切换文件 19 个测试通过。一次独立热文件接线任务在 20 轮内没有产生 diff，故 `event_handler.py` / `hierarchical_dispatch.py` 仍未修改，也没有宣称 H1-H 完成。
- 当前状态：NanoJev-1/2 是可审阅提交候选；H1-H 仅完成 HTTP 入口子片，接线主链、双事件、格式重试、WAIT/NO_CHANGE 与完整门禁仍未完成。下一步应先补一份 H1-H 字段映射任务书，再由单一实现会话继续，或由用户决定是否先审阅这两个 NanoJev 提交。

## 执行记录（2026-09-20）

- 专项测试计划已评审：T01–T09 作为局部 HTN + Jev 验收保留；R01/R02 在没有明确 NanoJev checkpoint 时记 `BLOCKED_ENV`，不以 DeepSeek 或 Claude 替代；H1-H 原有完整门禁仍独立保留。
- NanoJev 在 `codex/nanojev-existing` 增加了非阻塞 Shadow service 与定向边界测试，候选提交为 `4c608ad`、`b4018b6`，已由主线重放为 SDK `main` 的 `4e17085`、`7ab2630`；覆盖正式结果先返回、异常/超时隔离、请求上下文快照、单候选不调用 Shadow、候选集合与概率归一校验。SDK main 上定向 pytest 51 PASS，ruff PASS；尚未接入 HTN，也没有真实模型验证。
- H1-H 重新按任务书核对后确认：当前生产入口没有 `PlanningRequestBinding` 的创建/持久化调用，也没有逐字段构造 `AdmissionContext` 的权威 builder。已记录 `H1-H-blocker-AdmissionContext-2026-09-20.md`；在解除前不得用默认空值或直接复用旧 `apply_planner_reply` 绕过新协议。
- 两轮 Claude CLI 实现任务均在阅读阶段达到最大轮次，没有产生可接受 diff；后续只派发有明确字段映射和文件范围的短任务，避免继续消耗日卡。
