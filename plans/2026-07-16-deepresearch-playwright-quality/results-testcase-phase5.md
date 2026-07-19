# Phase 5 testcase 资产结果

日期：2026-07-16
范围：Windows 11 x64 only
结论：**TESTCASE DRAFT COMPLETE；Gate F EXECUTION PENDING**

## 已完成

- 新建持久化 testcase 入口、固定场景自动化/benchmark、Win11 隔离安装、桌面真人 E2E、幂等审查与统一结果文件。
- 覆盖 SC-EDU/AI/PW/BLOCK/RESCUE/LEASE/PLATEAU/CAP/NOW/CONTINUE。
- 桌面用例覆盖真坐标点击/输入、逐步气泡、重复 generate-now、WebSocket 重连、accepted-but-not-observed 强杀重启、历史恢复和 continue lineage。
- 安装用例限定当前 Win11 真机的隔离安装目录/用户数据目录；显式排除 Windows 10、Hyper-V、VM、ISO、Windows Sandbox 和系统重启依赖。
- 建立 36 条 AC→Task→production code owner→testcase→result 初始追溯矩阵。
- 未执行项目全部标 `PENDING`；Gate C/Task 11 只作为旁证，不冒充 Gate F 或真人 UI 结果。

## 尚未完成

- testcase challenger 两轮与终审应由 plan-task 主流程在最终 code/测试稳定后执行；当前文档是初始版本。
- 自动化、真实网络 benchmark、NSIS 隔离安装和 Computer Use 真人 E2E 尚未执行。
- `RESULTS.md`、追溯矩阵和幂等审查仍需回填最终证据、测试函数名及 `file:line`。

## 结果纪律

只有实际执行、预期全部满足且证据归档后，单个 case 才能从 PENDING 改为 PASS。
Windows 11 证据不得外推为 Windows 10 兼容；本轮不因缺少 Hyper-V 或无法重启而阻塞。
