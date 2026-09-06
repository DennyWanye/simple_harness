# Procedure 创建判别：v4 源码候选

2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，分支 `feat/procedure-adoption-source`，base `387a8d9eb539906af6fcfa5e2fe5fc270fdc5f02`。源码 `d1465653`，测试修正／补充 `f2e747e9`。原契约来自 `9deb3610`，本目录 `CONTRACT.md` 保留原文。首批发现真实跨版本恢复 P1，当前候选暂不可合入默认 v4；没有新 worktree、环境或真实模型调用。

## 首批真实缺口：failed batch 重试与取消回收不同

新增控制 r1：**24 PASS／3 FAIL，2.17s**。两项失败是测试断言错误：公开 payload 字段应为 `proposed_risk_level`；普通异常重试实际有多个 analysis_batches，读取应用事实应限定唯一 applied 行。`f2e747e9` 已修正这两处，尚未成功续跑。

第三项是确定产品接缝失败，不能用调整期望掩盖：v3 response 在 Host 已 durable succeeded，派生抛普通异常后 SDK 将 batch 标 failed；升级配置再重试，SDK 新建 attempt2/v4 request，Host 的 evidence_set_key 包含三版本，不能命中旧 v3 response，因而再次调用 Provider。测试假 Provider 无第二个响应而进入 unknown；不是实际模型调用，也不能当零重复调用通过。原红保留在 r1 的 `[3-True]`。

准确代码：Memory `backends/sqlite_v5.py::claim_analysis_batch` 10548附近优先恢复过期 active claim并回读原 request；10600／10713附近对 pending job新建 batch，使用当前 config 三版本。`core/jobs.py::DurableMemoryJobRunner.run_once` 499附近把普通 executor Exception 送 fail_analysis_batch；CancelledError则传播、保留 active claim。Host `analysis_executor.py::evidence_set_key` 包含版本；`sdk_adapters/post_turn_invoker.py::_durable_evidence_set_result` 只复用同key结果。

补充了两个取消后重开测试来区分真实语义：v3/v4在 response-only处取消，过期回收必须保持原 request bytes/hash，最新worker不得改写；它们尚未运行，不能借这两个控制替换普通异常跨版本红例。r2尝试只跑两处修正与两项新增，资源入口 **BUSY75，未启动child，未轮询**。

最小后继建议交主协调 SDK owner：失败重试若沿同一实际 evidence/job 来源，应在新 claim生成前保留／核验前次持久 request 的三版本及相关输入绑定，避免 Host 收到已经换版本的 request；正确处理多个历史成员／不同预算或版本，不能扫描 SDK 私表拼 Host request。若选择 Host 跨版本旧response兼容，必须另固定旧原始响应／协议／复用来源记录，不能把 v3 payload伪装成v4 Provider输出。本叶未擅改冻结SDK、旧receipt、batch或schema，也不添加永久blocked wrapper充当恢复完成。

资源：r1 PG97556 exit1、remaining=[]、cleanup_error=null，额外ps无成员，锁释放；入口耗时2.997s、峰172512KiB、最低磁盘5184MiB，512MiB／90s限制。旧short／WeMM绿未重跑。

ignored原始根：`/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/procedure-adoption/`。

| 文件 | SHA-256 |
|---|---|
| run_controls.py | 3f848afa804bd748cfb38e2e8d105f1ba686cbf2395ad24d6f61988c46afb380 |
| r1/command.log | fc95872c4698ac2f3a15078504e4fbb88f9f51c3aa0b48d4fee8d4b67d21fcc1 |
| r1/resource.json | c098441602d6633c0acb67af5e6f64938893a00bc1a04673242dcd1758476dba |

命令：`<primary-m0614/venv/bin/python> /Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir <上述根>/r1 --rss-mib 512 --seconds 90 -- <同Python> -I -B <上述根>/run_controls.py <上述根>/r1`。r2相同入口，末尾附 `-k 'high_risk or cancelled_response or (response_only and not 3-True and not 4-False)'`。carrier加载实际H077/M617 installed和本Host源码，没有安装或修改包。

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

## 新控制范围（执行状态以上文为准）

`backend/tests/memory/test_procedure_adoption.py`：三分类精确 span、19项来源／字段／步骤变异、高风险采用与模型语义剩余边界、v3保持／v4不降级，以及3条实际公共 Memory 写入／response-only 重开路径（v4正常、v4恢复、v3恢复）。既有 Host terminal 测试夹具使用确定性事实端口；Memory builder、worker、executor、job runner 和应用走公开生产接口，SQL仅只读诊断本测试结果，不造源或改SDK表。

公共路径要求三分类映射的 DRAFT 在真实写入后仍为 draft、成功／失败观察计数均0；v3历史 ACTIVE保持，持久 plan replay 零编译／零 Provider；混合／未知版本真实 Host审计拒绝。当前公共集成覆盖 reported_steps 写入与旧 v3恢复，adoption／uncertain 已备编译级控制，不能声称所有生产分类均已实际写入。

固定源码与真实红例交主转 Dirac，未称整体通过；后继只继续未闭合风险控制，不重跑 SDK 旧绿或 short/WeMM 绿。没有使用 plan-test 系列流程。
