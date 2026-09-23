# TaskGraph → 第三部分（Assurance）

状态：READY_TO_CONTINUE：主体阶段 PASS，独立交接评估 PASS；2026-09-23 02:47 CST已成功通知“第三部分”（01a0c5e5-ad91-71a2-930f-9d7ddbc66600）接入开发。整体重型验收后置。

用户于2026-09-23要求本阶段完成主体功能、使用HTN DeepSeeker做少量真实关键链路并修复，然后通知任务“第三部分”接续开发。重型测试明确后置三部分统一集成阶段；不得将后置内容记PASS。

## 接入源码

独立不可变候选：`/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-23/`。

- SDK完整捕获源：上述目录 `sdk/`；安装 wheel：`dist/simple_harness_sdk-0.13.0.dev20260922+taskgraph.23-py3-none-any.whl`。
- 最终Host增量：兄弟目录 `taskgraph-23-host-ui2/host/`；最终前端：`taskgraph-23-host-ui2/tauri-app/`。候选23原host包含已被修复的重复key/对比度问题，不作为最终前端。后端仍为 `taskgraph-23/full-host/backend`；原生安装使用 `taskgraph-23/native-env`，SDK字节与真实Mission的env一致，安装来源对应完整隔离Host vendor。
- 上游基线：`/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/taskgraph/htn1-integration/final-htn-capture/upstream/{sdk,host}`；HTN SDK HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加真实dirty文件。不要只按HEAD重建基线。
- 补丁：本计划目录 `HTN1-FINAL-INTEGRATION-WIP-SDK.patch` / `HTN1-FINAL-INTEGRATION-WIP-HOST.patch`。补丁包含主体源码与验收驱动；来源相对于完整captured dirty HTN基线，不能只对HEAD盲目套用。

包身份：version `0.13.0.dev20260922+taskgraph.23`；wheel SHA-256 `e1d34a46bcabb43d82be39bf8bb39755838d29993e81f1e1b95b92507e004c77`；manifest SHA-256 `185debc0f62edffb417dcff9419bc53bb9d59db9b819692d07b518efa0ecde5d`；deployment `331a8b947bcc5be329d971da6a0937188fee711e4d39cb7ed6995200bd2d35ea`；598包文件匹配，TaskGraph完整acceptance仍NOT_RUN。

## 接线注意点

1. 使用自己的Assurance隔离工作树三方合并，保留现有未提交Assurance改动。不得直接覆盖共享HTN或Host。公共Review/Commit/终态改动以此TaskGraph候选为最新父源码。
2. TaskGraph占用migration25，HTN占23/24。Assurance曾用25做未注册DDL探针，正式接入需追加下一编号，不能覆盖现有checksum。
3. codec manifest v5锁源码。改变被锁类型/codec字节需要新版本，不能覆盖旧manifest；完整部署manifest需由新候选重新冻结，不能沿用TaskGraph23身份声称Assurance版本已验收。
4. 原SDK新增可信可选`startup_assembly`：Host首启/rebuild必须先装HTN+TaskGraph，再恢复工作区和SDK运行池；Assurance若增加运行前依赖需保留这条顺序。不要退回enter后装配。
5. Planning grant和TaskGraph kernel启用分离。原审批/委派不能冒充TaskGraph enable；显式认证启用保留seed/CAPTURED_BASELINE约束。恢复不能重绑已有policy或替换历史来源。
6. PlanCommit APPLIED、Attempt/reserve/manifest/intent、TaskGraph history/followup在原Store事务；新Assurance接线不得引入旁路写入或自造receipt。READY仍fenced；UNKNOWN保持占用；原物理事实解决后才结算。
7. 保留本阶段发现的两项跨层修复：根评审接收有界的原验证事件摘要（未绑定scope为UNBOUND）；Mission judge的view ID依据原注册workspace和BudgetReserved身份识别，不当作Worker Attempt，artifact producers仍核fence。
8. 回写自己的ARCHITECTURE/状态时不要覆盖其他线程的dirty内容；原始证据仅`.local-test-evidence`。取消的htn-taskgraph自动化不重建。

## 本阶段与统一验收边界

本阶段目标及实际独立审查分别见 MAIN-ACCEPTANCE-2026-09-23.md、MAIN-CODE-REVIEW-2026-09-23.md。原始完整TaskGraph42场景、mutation、stateful、legacy/全H1、规模/性能、多领域多trial与完整原生矩阵后置统一测试。HTN原生核心已PASS，但旧语义回放23未知事件类型/1字段/UI账本仍PARTIAL，不能继承成全门通过。

当前共享Host仍安装HTN候选；TaskGraph候选在隔离环境，未发布、未替换共享vendor或DB。Assurance应明确在新合并候选上验证，不把历史TaskGraph/HTN PASS自动继承为新字节的验收。

## 本阶段交付证据

见 MAIN-RESULTS-2026-09-23.md：candidate23真实DeepSeeker单Mission正式COMPLETED、11calls/49646tokens/0unknown，4读口与cold rebuild不变；最终Host UI2原生基本交互通过、0新增模型调用。SDK补丁104文件 SHA-256 `d6298679cb2b5c3c1e9ab344bcfeb48370a385d3297b6c10278e3f2e67c1dade`，Host补丁12文件 SHA-256 `e41b2e7687186ebb8cdb46b5b70952f7431c30ac96850a8cd0b7ef6d89cf9ce2`。主体阶段PASS，独立交接核验PASS；可通知第三部分直接接入，无须让用户再转述。

本机磁盘可用空间曾降至约116MiB；请先只读确认，避免复制node_modules、缓存与运行数据库。保持自己的Assurance源码及本任务原始证据，不自行清理共享uv缓存（已有清理询问尚无用户答复）。原始最终Host前端基于已捕获HTN前端+TaskGraph增量，完整资源包附于candidate23/full-host；只做所需的轻量源码接入。
