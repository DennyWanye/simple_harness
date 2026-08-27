# Phase 3 D — 并行验证准备轨

## 时间线

- 2026-08-28：读取 testcase lifecycle、并行验证清单与 iterator；读取冻结 acceptance/contract/plan。
- 2026-08-28：build inventory、逐篇审查相邻候选、完成 reuse decision 与 5 条 black-box TC。
- 2026-08-28：完成两轮 challenger（FAIL→补 supporting assets→PASS），编译 manifest 草案。

## 清单

- [x] inventory/index 已发现并重建；legacy 保持 needs-review。
- [x] 相邻候选全文审查；未读取实现代码、diff 或实现代理产物。
- [x] 每条 obligation 的 reuse decision 通过 validator。
- [x] AC-SI-1～6 与 SI-M1～M5 由 5 条 active black-box testcase 覆盖。
- [x] testcase challenger 2 轮，末轮 PASS。
- [x] verification spec 编译成功，`case_sets.full=5`。
- [x] scenario/evidence/impact/applicability 草案齐备；SI-M5 impact 留空以 fail-closed 触发全量复测。
- [x] malicious fixture 规范、环境准备器、核心 smoke inputs、full-surface checker 已存盘。
- [x] 真实 HTTPS GitHub 负向 fixture 已锁定：`octocat/Hello-World@7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`，用于无 `SKILL.md` 的零副作用拒绝路径。
- [ ] 昂贵真实 UI 执行、gate init/testcase lock 在实现汇合与候选冻结后进行。

## Black-box 边界声明

本轨仅使用 acceptance、assurance contract、plan、testcase inventory/候选原文和 plan-test gate 公共格式；没有读取业务实现源码、git diff 或执行代理中间产物。impact paths 只来自冻结 plan 的文件影响清单；不确定的 full-surface scenario 留空而非猜测。
