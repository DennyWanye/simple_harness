# V1.4 Operation 两份补遗实施记录

plan-status: finalized（依据两次评估和用户本轮“请你开始处理”；不修改原附件字节）

当前目标仍为 V1.4 H1–H8，去除 NanoJev。本文是既有执行工作的增量 journal，不能替代原 acceptance 或删除未完成门禁。

## 输入与授权

- 规格：`V1.4-D1-D2-D3-Operation补遗-2026-09-21.md`、`V1.4-Operation完成合同补遗-2026-09-21.md`。
- Oracle：前者 D1-01…D3-05，后者 OCC-01…OCC-12 及附件 sdk-test-cases.json；继承完整 V1.4 acceptance、H1-H 矩阵、H1-I/完整 H1/legacy/Host UI 门。
- 最新评估：`Operation完成合同补遗评估-2026-09-21.md`，0 架构待决；参考 DDL 的跨 Spec/Scope 唯一键已复现，正式实现修正。
- 保留 SDK candidate `102ad3d…` 加已有 dirty 文件，不 reset/clean/合并/重装/推送；应用 DeepSeek v4.1 Flash，子代理 GPT-5.6 系列。
- 按 plan-task 执行，沿用既有授权和验收，不逐阶段再问许可；原始证据只存 ignored 本地目录。

## 切片与顺序（同一总体任务，不能以部分 PASS 关闭整体）

| 切片 | 可用能力及入口 | 最小决定性预期 | 后续仍保留 |
|---|---|---|---|
| OC-1 | 固定 Principal 的完成规格批准入口 → 原 CommitService → 原 Store | 真 Requirements/义务精确绑定；跨tenant/伪authority/错hash/冲突重放拒绝；写入及receipt原子；原历史不变 | Scope派生及接受/完成消费者、T0/T3、D3与整门 |
| OC-2 | Plan Commit冻结Scope；准备结果进入实际接受与读取链 | 准备数据可供正式消费者读取，MIXED仍未完成；所有完成入口不因CURRENT Acceptance提前通过 | 真实效果完成链 |
| OC-3 | 明确操作意图 → T0/正式ACTION_PROPOSAL → T1/原审批 → T3正式效果接受 | 原子身份/回执/来源，wrong milestone/UNKNOWN/错slot不能完成；多效果分别验证 | 冷恢复、真实模型与整体门 |
| OC-4 | 原事件循环defer/stop gate/原raw冷恢复 | 不新开模型回复；stale不改绑；超时保持MANUAL_REQUIRED及未决责任 | H1-I、完整H1/V1.4及Host验收 |

OC-1 内合同、存储、测试三个文件集合分别由子代理负责；主代理负责API/Commit接线及全部测试执行。共享候选工作树按文件独占，既有热文件只由主代理写；不从干净HEAD重建而丢失继承修改。底层类型/建表通过只是技术进度，不单独声称切片可交付。

## 实现前 Oracle

1. 批准入口仅接受任务/命令/精确RequirementsRef/完整Spec，认证Principal固定于API；请求体不能自填authority或approved。
2. Spec覆盖正式要求，effect绑定已有义务，来源缺失不能默认为CONTENT_ONLY；同requirements revision不能有第二份不同Spec。
3. Replay必须重新核caller/tenant；同command异body拒绝。同body回原receipt，不产生新Spec/事件。
4. Spec/receipt/event同事务，失败无半份已批准记录。Store直接writer必须有外层事务；旧schema checksum不改。
5. 正式codec拒绝未知字段、重复ID、bool整数和坏hash/引用；贡献类型不得把准备范围变成效果。
6. 同一旧事实在新版Spec/Scope正式重审能持久化不同binding；没有重发Operation，不覆盖历史。

## 基线

证据根：Host `.local-test-evidence/2026-09-21/operation-completion/implementation-20260921T214819/`。

执行：候选 `uv run --frozen pytest -q`，目标 `test_htn_store.py`、`test_h1h_planning_authorization.py`、`test_h1h_preview_compiler_refusal.py`。

结果：113 PASS / 10.49 秒。JUnit、原日志、执行前源码hash与dirty状态已保存。此为定向续接基线，不是重跑全部 V1.4。

## 当前状态

IN_PROGRESS：OC-1 实施中；尚未通过真实完成链验收。每个切片实测后更新对应 ARCHITECTURE；当前没有新增功能完成声明。

## OC-1 中间验证

固定认证主体的 mission_operation_completion_approve 已接至 SDK facade 和原 CommitService/Store。批准只确认完成要求的映射，不授权执行 Operation、不证明效果完成。schema 21/22 增量安装七张关联表；旧 checksum 未改。迁移 runner 使用 sqlite 完整 statement 识别，支持触发器与注释分号，保留外层事务。

- 基础合同/批准/回滚/迁移：completion-third.xml，27 PASS / 0.54s。前两轮夹具接口及错误码失败保留；后继发现 Task contract hash 与整份 semantic binding hash 混淆，已修复，待复验。
- 定向六源文件 Ruff、四源文件 mypy 通过；后继改动仍需重检。
- 原 candidate 两包版本为 0.12.2 / 0.11.1，Host pin 为 0.13.0.dev20260920；原源码 Host 启动正确拒绝。临时 clone 覆盖候选源码，只对副本两包 version.py 对齐；独立 venv 真实 editable 安装、真实 attestation/origin/metadata 验证。Host 原 venv、两仓版本 pin 均未改。
- 副本与变更来源记录：host-derived-source.json / host-derived-attestation.json。仅为 derived-source wiring smoke，不是候选原字节或正式 wheel 验收。
- Host 随后暴露被延期的 PR-7 decision_observer 无条件参数阻止无 NanoJev runtime 启动。依据范围补遗移除启动/重建钩子与启动时 seam 安装，历史模块保留。host-derived-approval-third.log：11 PASS / 18.28s；新增重建 receipt 恢复检查继续运行。

OC-1 SDK 批准入口定向通过，Host 仅派生源码接线证据，原候选包装和原生 UI 未验。OC-2 纯 Scope compiler 与测试已编写，首轮六项失败均为不存在的 World.network 夹具接口，正在修正；Plan Commit/accepted-result 消费者尚未接通。以上不能关闭 OC-2/OC-3/OC-4 或完整 V1.4。

## OC-2 原子准备事务与消费者（仍未关闭切片）

- Spec/Scope 实际 Store、批准、回滚与损坏读取 80 PASS/0.85s；随后相关回归 314 PASS/12.44s（scoped-first.log）。
- 新协议 _accept_result 同事务写 Result、Attempt、Acceptance、Contribution、accepted outputs。MIXED 保持 VERIFYING；不自动 propose_action；Selection 复用同 core；禁止重新派 Worker。
- 实际 Attempt intent 固定原输入 manifest 双 hash、Task/Plan/Scope pin；ResultSubmitted 固定模型 port claims，冷恢复无内存映射也可验收。
- 新 leaf 验收不发布新 Requirements；保留批准版本。实际 required checks 必须逐项有 durable PASS。未纳入该 Result 的同 Attempt 文件及 MODEL artifact provenance 拒绝。Contribution 记录实际 local criteria，不能用它们满足根 Spec。
- 完成状态 reader 接 ORDER、DATA、dispatch、终结和 Goal effect gate：DATA 可读准备结果，ORDER/完整完成仍等待 effects。root/compound collector、完整 Validity、no-dispatchable pending 等继续处理。

## 2026-09-22 后继验证与实际缺陷修复

- 固定源码 `full-target-scoped.xml/log`：4016 PASS / 8 FAIL / 5 SKIP，146.72s；589 个 SDK src/full_target Python 文件 hash 前后一致。原失败不删除。8 个失败为测试辅助变量误替换、首次 Plan/Scope 尚不存在的读取时序、规划前终态投影及存储边界检查；修复后对应组合 231 PASS / 14.89s。不得将定向复验数字改写成完整门禁 PASS。
- 原子准备/完整性检查后继 105 PASS / 1.26s；验收现在要求原 Attempt 冻结 input manifest 和 ResultSubmitted 原 port claims，禁止改用另一 manifest、漏掉输出或换 namespace。冷恢复读取全部分页事件，不能在事件数达到默认列表上限时遗失原 ResultSubmitted。
- 真 Selection API winner→准备接受与注入故障回滚 2 PASS / 0.35s；真 Orchestrator 等待态不误停，保持 Mission ACTIVE/Task VERIFYING，无新增 Worker/模型 intent。
- 真实非空 DATA 下游派发 1 PASS / 0.65s：发现并修复 `freeze_fragment_execution` 在核验 completion manifest 之前拒绝合法挂载路径的问题。现在先从实际已采用计划、已接受产物和输入 witness 重建 manifest，核对完整输入四元组，再供原 fragment 冻结器使用精确挂载路径；伪造挂载路径被拒且不留 Attempt。该两叶夹具为明确 CONTENT_ONLY，未冒称它覆盖 MIXED；MIXED 的 DATA/ORDER 分离由独立消费者测试覆盖。
- 组合及既有 resolution/root 相关 235 PASS / 3.58s。真实嵌套 Method 链暴露中间 local criterion 原先无合法表示，已修：Spec 仅 c-root；inner Scope 为 c-sub；leaf Scope 为 c-leaf-verified。局部映射由真实 ancestor Task/occurrence/Method links 证明。计划/compiler 组合 11 PASS / 0.46s；进一步真实 leaf core acceptance→CompositionAcceptanceAssembly→inner GoalResolution→统一 completion reader 1 PASS / 0.36s；根仍未完成且 Requirements 未新增。root 读取保留原 ALL/ANY 公式。
- OC-3 新只读 intent source reader + V2 strict command 11 PASS / 0.48s：当前认证 tenant、当前 Spec/Scope/唯一 owner、正式准备 Acceptance/Contribution、原 Result/Attempt/CAS 字节均实际重读；wire 不能携带 caller authority。AUTHORIZED_SLOT 尚无正式 producer，具名拒绝，不推测来源。
- D2 payload codec/metadata/CAS/profile 注册初轮遇到 slots dataclass super 与 metadata 列顺序错误，已修后 13 PASS / 0.29s。V2 milestone policy pin 沿现有 CompletionPin，object_revision 仍为 1。它们只是冻结数据和来源接缝，不是已完成 T0/T1/T3。
- 上述原始日志/后继 full_target 继续保存在同一在途 run 的 `.local-test-evidence/2026-09-21/operation-completion/implementation-20260921T214819/`；跨午夜沿用 run ID，文档日期更新为 09-22。未发起新真实 Provider 调用、未合并/重装 Host。T0 官方提交与独立 ACTION_PROPOSAL dispatch、T1 物化、T3 outcome adapter/正式验收、D3、完整 H1 与 H2–H8/原生 UI 收尾仍 OPEN。
- 最新真实准备/安全负控6 PASS/0.55s；关闭重开 Store、DATA与ORDER隔离、缺失原端口声明3 PASS/0.46s。当前8源文件 mypy通过。
- 历史 full-target-operation-first.xml：3987 PASS/15 FAIL/5 SKIP/143.67s，hash不变；schema旧断言3项与未批准Spec成功fixtures12项已处理。后继 full-target-scoped 正在固定源码测试；Ruff发现测试里2处outputs变量替换遗漏，等本轮结束后修复，不能边跑边改。
- 尚未刷新 Host 派生副本；原先11 PASS+rebuild1 PASS不代表当前候选原字节、wheel或UI。后续仍须T0/T1独立ACTION_PROPOSAL、T3效果Review、D3延期以及整套H1–H8门禁。

## 2026-09-22 主体编码继续（不运行大规模测试）

T0/T1/T3、D3 与完成消费者继续接线，新增 H4 真实影响索引、H6 持久评估/晋级和 H7 原 CommitService 边界；H8 配比纠正。只有静态/import 检查，没有新增测试 PASS。逐项代码与剩余主体缺口见 [主体编码检查点](主体编码检查点-2026-09-22.md)。此前测试数字及“尚无 producer”表述保留为历史时点，不能混作当前源码验收。
