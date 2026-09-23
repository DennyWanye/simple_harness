# ARP-EXEC-1.1.1 继续评审

评审日期：2026-09-23。范围：原生 AgentRuntime / BaseAgent / ReAct。延续用户决定，暂不适配 Pi 或其他 Runtime。附件中的实施指令仅作为评审对象，本轮没有启动业务开发。

## 结论：GO，进入主体开发

**上一轮 R1–R5 的主要规格缺口已经关闭。本轮没有发现需要再退回 PlanAgent 做整体修订的阻断项。建议以 1.1.1 作为施工规格，进入当前父候选盘点和主体实现。**

“可以开工”指主要接口、字段来源、事务责任、失败及恢复路径已经足够明确；不表示数学意义上 100% 无遗漏，也不表示实际 SDK 已实现、环境已就绪或最终验收通过。后续需要处理的是现有源码适配、资源核验和生产验证，不应因未来功能尚未写出来继续循环改计划。

两条非阻断实施注意事项见下文，直接纳入实施记录即可，无需为此再要求用户裁定架构。

## 本轮实际证据

输入 ZIP：`/Users/denny/Downloads/simpleharness-agent-runtime-plane-1.1.1-2026-09-23.zip`。

SHA-256：`a17f9784cf1508027cbbcbf826f0ca55da97a95ae75bacdbda0369420280b22b`。

本地解包根：`.local-test-evidence/2026-09-23/arp-1.1.1-review/simpleharness-agent-runtime-plane-1.1.1-2026-09-23/`。以下包内路径均相对该目录。

| 实际检查 | 结果 | 证明范围 |
|---|---|---|
| `tools/verify_delivery.py` | PASS，77 文件 | 交付清单与文件字节一致 |
| `tools/check_plan.py` | PASS，101 schemas、2011 fields、101 examples、87 SDK cases | 资产引用、字段 hash、选定结构和语义一致性 |
| R1–R5 选定反例与正控 | 12 PASS / 0 FAIL / 0 ERROR / 0 SKIP，unittest 报告 0.044s | 包内最小 SQLite fixture、临时目录和参考 codec / composer |
| 真实 SDK、完整迁移、原生 UI、真实模型 | NOT_RUN | 本轮未实施，不能填为 PASS |

仅执行上轮问题关闭所需的 12 个具名检查，没有运行产品全量测试或批量回归。作者报告的 109 参考测试、32 窄例及 10+5 变异未在本轮整体复跑，不能与本轮 12 项相加作为生产完成度。

原始输出：`.local-test-evidence/2026-09-23/arp-1.1.1-review/selected-review-checks.log`。
日志 SHA-256：`d83b3b53d578943d874750c35c9bf59d838fe6f1ce945b18af338b93261c31b4`。
机器索引及 SHA-256：`.local-test-evidence/2026-09-23/arp-1.1.1-review/review-evidence-index.json`。原始证据留本机，不进入 Git。

## R1–R5 关闭判断

| 问题 | 当前修订及核验 | 判断 |
|---|---|---|
| R1 删除异常与 SQL 状态机冲突 | 保留 DRAINING/PURGING，用 typed `PurgeProgress.blocking` 记录异常；destroy identity 不可更换，普通 rebuild 不得恢复 ACTIVE。检查覆盖 SQL 持久化/重开、真实临时目录 rename、删除回执后再出现异常 | CLOSED_SPEC |
| R2 未完成检索可冒充 COMPLETE_EMPTY | `status_for` 统一 phase / coverage / mode / query total 优先级；receipt v2 区分全查询数量与单页数量；嵌套 HostResponse 递归校验。上轮两个反例被拒绝，正常部分扫描和词法零命中可表达 | CLOSED_SPEC |
| R3 自动 F 内部请求、分页与恢复缺口 | 新增 ContextRecallRequest / Progress / Checkpoint / Result，固定原 prepare slot、高水位、排除组、policy、query call key；中央协调表保存进度，aggregate 绑定所有结果页；manifest 引用聚合结果。参考 composer 的后页命中、重启同身份、聚合错误拒绝和禁止降级时超时阻断通过 | CLOSED_SPEC |
| R4 同分排序规则冲突 | ScoredRow 携带完整五元组，channel heap 与 RRF 使用同一 stable key；反向 record_id / chunk_id、topK=1 的反例通过 | CLOSED_SPEC |
| R5 配置上限与运行限制冲突 | 新 Policy 对 256 行 / 16 逻辑查询 / 500ms 页 / 30s 总时长采用明确硬上限；每路 K 与融合后 M 分开；边界和越界检查通过 | CLOSED_SPEC |

R1 的关闭不是让旧的 `PURGING → QUARANTINED` UPDATE 通过。新版选择了更清楚的删除异常表示方式，该旧转换继续被拒绝是正确结果。

关键入口：`implementation/JOBS-LIFECYCLE.md` J6；`implementation/CONTEXT-SEARCH.md` C5；`implementation/CONTEXT-RECALL.md` 全文；`reference/retrieval_status.py`；`reference/search_scan.py`；`sql/execution_additive.sql` 的 purge/recall 表与触发器。

## 对接口和字段完整性的回答

相较 1.1，新版已经补齐自动 F 这条主链最重要的内部对象、caller、字段生产者、Store 接口、两库提交顺序、原 embedding 调用身份、时钟记录、分页聚合及临时 pin 保留责任。它不再要求实施者临时设计整套自动召回协议。

公开 search/read、管理 settings/receipts、目录/Skill、生命周期、错误和事件合同继续存在。版本演进也已明确：新创建协议 ARP_V1_1_1，相关既有 DTO 升级 v2，新内部类型 v1；旧协议按冻结 reader 读取，不能向旧字节补字段冒充新事实。已有旧 DDL 时使用独立升级迁移。

因此可以回答：**核心接口和字段已达到开始实现所需的定义程度。** 不能声称所有目标函数已经存在于当前 SDK，或每个来源都已在最新 dirty 父候选核实；包本身也明确把这些标为待核验。

## 两条非阻断实施注意事项

### A. 16 个结果页是独立资源预算，不保证容纳 512 项

`CONTEXT-RECALL.md` §4 用 `ceil(512/32)=16` 解释结果页上限，但每页还有 64KiB 限制，实际可能装不下 32 项。不能由最多 512 candidates 推导“结果必定在 16 页内读完”。

落地时将 16 视为硬资源预算：若第 16 页后还有结果，按既有 §7 的聚合预算耗尽规则，持久 SKIPPED / PARTIAL / RECALL_AGGREGATE_LIMIT，保留真实调用与诊断，不截断后写 READY/COMPLETE。若以后产品需要保证读全更多大项，再明确调整页数、checkpoint/page_refs 上限；本轮不需要扩范围。

这是对已定义“先到限制生效”规则的落实，不是要求重设架构。生产验收应包含“每页因字节上限少于 32 项”的场景。

### B. 取消时区分非终态 recall 与不可变终态结果

`CONTEXT-RECALL.md` §9 的取消/destroy 文字要求“相关 recall 置 STALE”，而同章和 SQL 又明确 READY/SKIPPED/BLOCKED/STALE 终态不可改写。

实施时只将非终态协调工作转 STALE。已持久终态结果作为历史事实保留，消费处重新检查 Session generation、权限和原请求状态，拒绝用于被取消或已销毁的请求；不得尝试改写终态行，也不得因为终态结果存在就绕过新的使用门。这样同时满足原冻结事实与取消约束。建议在落地文档把“相关”明确写成“相关非终态”。

## 开工、激活、验收应分别判断

| 层级 | 当前判断 | 后续必要工作 |
|---|---|---|
| 计划主要合同可实施 | GO | 按本版继续，不重开 Pi 或替代 Runtime |
| 当前源码基线明确 | 尚需启动盘点 | 最新 TaskGraph / Assurance 父候选、dirty 状态、实际方法签名、两库迁移号及 checksum |
| 计量与 embedding 可激活 | ENVIRONMENT_DEPENDENCY | 实际 tokenizer/serializer/prior 分解、批准的 embedding 制品与原调用/usage 适配 |
| 主体及跨层接线 | 本轮未实施 | 原 runtime/assembly/wire/UOW/Host 真正贯通，无空 producer 或假 receipt |
| 产品验收 | NOT_RUN | BODY_WIRED 后统一迁移、机制反例、原生 UI、真实 embedding/模型及平台验收 |

512K 仍是配置能力目标，需要具体部署和计量证据，不能由 Policy 数字推导已实现。HashEmbedder、参考 call port、最小父表 fixture 都不能替代生产链证明。

建议下一步由主代理集中实施。先完成有界父源与资源盘点，并把上述 A/B 写入实施记录；随后按 RP-A 至 RP-D 完成主链，主体完成后进入 RP-E 统一验收。普通命名、等价封装及迁移空号由实施者处理；确有当前源码语义冲突时再给出精确来源和推荐处理。

本轮产物只有评审结论与本地定向证据；没有完成产品功能，因此不改写 ARCHITECTURE 功能完成度、不默认开启尚未实现的能力、不提交或推送代码。
