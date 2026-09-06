# Procedure 创建判别：v4 源码候选

2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/procedure-adoption-source`，base `387a8d9eb539906af6fcfa5e2fe5fc270fdc5f02`。原契约来自 `9deb3610`，本目录 `CONTRACT.md` 保留原文；因 r8—r12 真实短期阻断暂停后继续本叶。没有新 worktree、环境、模型调用或测试进程。本批源码与新控制草稿供主／Dirac 窄审，未称通过。

## 已写生产路径

| 文件 | 实际增量 |
|---|---|
| analysis_proposal_v4.py | 主模型三分类 prompt/schema；Procedure 严格字段、USER/subject/receipt/ref、完整文本、唯一采用引文及唯一有序不重叠步骤；Host 派生各 EvidenceSpanRef 和固定 reason，adoption→ACTIVE，其余合法分类→DRAFT |
| analysis_protocol.py | 精确匹配 prompt/result_schema/policy 三版本，返回原 v3 或新 v4 模块；未知／混合拒绝 |
| analysis_executor.py | Provider 输入的 prompt/schema 与 response-only 编译都按持久 request 版本选择；已持久 envelope 原样验证回放；不支持版本先审计拒绝，零 Provider |
| memory_ingestion_outbox.py | build_worker_config 显式采用新协议常量；只有新批次默认 v4，SDK validator 仍 v3；预算／模型参数不改 |
| analysis_proposal.py | 保留原 v3 常量、prompt/schema/compiler 默认语义；共用 operation 遍历新增仅 v4 使用的 Host 编译回调，v3 异常／兼容路径保持 |

与旧契约的实现组织差异：没有直接把原 analysis_proposal 全局常量改为 v4，而是保留该模块为可读的 v3 实现，新模块集中维护 v4，worker 常量导入改为 analysis_protocol。因此本叶需增加 worker 的一处 import 变更。模型不能选择协议版本、提交 hash/offset/receipt/lifecycle/success_count/权限。

Host 读取 AdmittedItem 的来源仍是既有 HostEvidenceAuthority 持久 envelope/receipt。新 Procedure 分支进一步验证 typed DTO、真实 USER_MESSAGE、subject、accepted receipt 与 envelope 绑定、request 精确 ref、item 恰一、公开 text 一致；不将分析 batch Run 强行等同于源消息 Run。全部引文派生使用既有 UTF-8 精确字节规则和16KiB上限，不解析关键词或使用第二个 NLP 判定器。非法结构拒绝该 operation，不静默降级为 draft。

## 不能越过的含义边界

模型承担 adoption／reported_steps／uncertain 的语义判断。完整原文与采用引文的字节匹配仍不能证明语义正确；测试专门保留“把成功了一次错误分类成 adoption”的可执行反例，明确 Host 不会凭字符匹配识别这种模型错误。真实模型分类准确性尚未测试，不称自动控制证明了语义质量。

ACTIVE 是明确采用的记忆状态，包含高风险程序也不签执行许可。reported_steps/uncertain 的来源仍是 EXPLICIT_USER/SOURCE_BOUND，不能冒充已核验观察；本叶不调用 record_procedure_observation，不增加 success，不签 terminal/effect。

原义务来源：Memory 原 `acceptance.md` HM-AC-5 及 `slices/S3-cognitive-systems-recall.md` Task3；SDK 已 COMPLETE。Host `testcase/human-memory-program/TC-HM-04-procedure-prospective.md` 步骤1/2/3/5中，本叶只处理创建时明确采用与叙述步骤的区分。真实 TaskScope terminal 成功来源绑定、跨作用域观察累计、使用前 tool/environment/version applicability 仍需后继接线；Procedure recall 不等于产品采用／执行闭合。本叶不修改 SDK、Prospective source/store/consumer、runtime/composition 或 context route/authority。

## 新控制草稿，未执行

`backend/tests/memory/test_procedure_adoption.py`：三分类精确 span、19项来源／字段／步骤变异、高风险采用与模型语义剩余边界、v3保持／v4不降级，以及3条实际公共 Memory 写入／response-only 重开路径（v4正常、v4恢复、v3恢复）。既有 Host terminal 测试夹具使用确定性事实端口；Memory builder、worker、executor、job runner 和应用走公开生产接口，SQL仅只读诊断本测试结果，不造源或改SDK表。

公共路径要求三分类映射的 DRAFT 在真实写入后仍为 draft、成功／失败观察计数均0；v3历史 ACTIVE保持，持久 plan replay 零编译／零 Provider；混合／未知版本真实 Host审计拒绝。当前公共集成覆盖 reported_steps 写入与旧 v3恢复，adoption／uncertain 已备编译级控制，不能声称所有生产分类均已实际写入。

本批未运行任何控制；固定源码交主转 Dirac 挑战后，再协调默认共享资源入口执行这些新风险控制，不重跑 SDK 旧绿或 short/WeMM 绿。没有使用 plan-test 系列流程。
