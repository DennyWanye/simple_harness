# BODY_WIRED 施工检查单

本清单只说明主体真实路径已接好，不是功能测试PASS，不要求新增测试已跑才准写代码。一个 integration owner 负责原AgentRuntime/ExecutionUOW、ToolGateway、Assurance exposure adapter及Host热文件；其他Agent只能在约定边界提供补丁。

| ID | 必须接通的生产路径 | 静态/定点核查 | 统一验收时对应 |
|---|---|---|---|
| BW01 | authenticated factory→originalBaseAgent→explicitprotocol/profile/session→真实CREATING文件建立 | 原创建caller、同UOW、无缺行legacy | R01/R02/I07 |
| BW02 | 原Journal闭组→同txnINDEXjob→realembedding→partition→原ACK | 不免费调用、不跨库ACID、不混agent | M01–M06 |
| BW03 | 每个角色每个Provider ordinal→effectivesettings/A–E→F/G→真实renderer计量→originalfreeze | 无另一个PlannerProvider直调 | C01–C10 |
| BW04 | Provider实际inputreceipt→ContextManifest→Assurance原exposure | catalogue/返回toolview不等曝光 | C09/I05 |
| BW05 | Task/Operator capability→actualregistry/currentprovider→binding→execution | no invented deployment/authority | T01–T04 |
| BW06 | dynamictoolsnapshot→原ToolGateway→参数/当前授权→read/sandbox/OPS | MCP hint不是policy；全部写通道受guard | T01–T10 |
| BW07 | skillinstall→quarantine→原evalTASK_CONTENT→admit→exactdependency/load/execute | 不新ReviewPurpose/SkillRun、固定bundle | K01–K10 |
| BW08 | close/destroy→generationfence→全来源清理证明→真实FileGuard→unlink→PURGED | Unknown不丢/关闭不泄漏/旧lateindex不能复活 | R03–R10/M08 |
| BW09 | allGC/read/restore→正式roots＋quarantine | 恢复外部当前授权独立于旧backup | R07/R08/I04 |
| BW10 | contextsettings→sameUOWadoption→下一真实request | oldrequest不重组/现有profile不改 | C08 |
| BW11 | 原Task/Assurance/Operation identities与receipts导入 | 原生产者不是临时模拟器 | I01–I06 |
| BW12 | Host DTO→fixedcaller→原viewstore/native→defaultnewARPafteracceptance | 独立userdata/端口/制品 | I07/I09/I10 |

尚未部署的可选workflow或solver只标CAPABILITY_UNAVAILABLE，不妨碍已有Provider实现；但不能伪装完成其部署。至少instruction＋script Skill、原实际tooladapter、真实tokenmeter＋embedding、用户实际OS必须完成。

## 实施顺序

RP-A先固定全部Schema、source/owner映射与SQL接口；RP-B/C按共同frozen types同时实现context与能力，RP-D完成guard/生命周期/跨库/Host；主体阶段只针对阻塞做小检查。BW01–12静态路径确认完成后，RP-E统一60场景、16mutation、stateful、原legacy/Assurance/OCC/TG对应项、模型12局、原生平台、独立代码审查。

合并与默认设置属于同一交付：通过验收后新factory默认ARP，活动旧Agent继续原模式。不是允许提前打开未完工TaskGraph；不要求再向用户确认普通名称/迁移号/依赖锁的选择。

## 必须更新

`ARCHITECTURE/AGENT_ORCHESTRATION.md`或当前实际事实源、AgentRuntime模块入口、`PROJECT_STATUS.md`、协议版本/DB分工/临时删除范围/证据索引。Git只写结论、命令、nodeid、相对证据路径、SHA；原始日志/SQLite/trace/截图/源码副本在ignored路径。
