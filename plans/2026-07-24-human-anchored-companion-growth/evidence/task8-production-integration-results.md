# Task 8 Candidate 生产集成结果

> 日期：2026-07-25
> 状态：自动化门通过；最终 authority cutover 仍由 Task 13 负责

## 已完成

- `CapabilityBuilderHost` 增加 candidate-only output；候选构建返回确定性 evidence，
  general-install 行为保持不变，candidate 路径不会调用 Manager。
- execution terminal UoW 原子写 child terminal、host-issued draft receipt、terminal
  extension receipt 与 exact immutable material。workflow schema v19 保存 receipt，v20
  保存 canonical archive bytes 和 manifest/file-set/archive hash；重放只核验，不重跑
  child 或重建 bytes。
- `SqliteCandidateDraftMaterialQuery` 从 execution DB 只读恢复 exact bytes。
  `CandidateCompositionService` 依次校验 receipt、当前 Store fence、material identity 和
  统一 package validator，再由 `CompanionStore.commit_candidate_build()` 单事务 CAS
  build 并写 package/source/attempt/files/blobs。
- first-party、local、configured、git、companion-growth 共用安全 materializer 与
  `CapabilityPackageValidator`。Manager 只接收并在安装前复核
  `ValidatedCapabilityPackageRefV1`。

## 自动化与进程证据

- Candidate/Builder/receipt/composition 聚焦：`39 passed in 5.89s`。
- Companion 全量：`335 passed in 22.08s`，root PID 25564，tracked=3，`survivor=0`。
- Capability 全量：`249 passed in 24.59s`，root PID 25904，tracked=17，
  `survivor=0`，释放 private memory 982175744 bytes。
- Harness 相邻按文件：execution start extensions `4 passed`、RunKernel `62 passed`、
  execution contracts `4 passed`、workflow schema `14 passed`。
- 关键目录 `compileall` 与 `git diff --check` 通过。

大型 Harness 合并进程在断言全部完成后受到既有 aiosqlite event-loop shutdown worker
影响未自行退出；每次均按精确 pytest command line、PID/create-time 清理，释放
1321967616、916471808、880644096 bytes private memory，复核 `survivor=0`。没有用按进程名
广杀，也没有把该退出问题写成测试断言失败。

## 仍由后续任务负责

- reserved builder child 的生产 scheduler 与固定 ToolSet 注入、成长 authority 切换：
  Task 13。
- 全链 crash/fuzz、真人主消息页验收与最终 survivor 审计：Task 14～16。
