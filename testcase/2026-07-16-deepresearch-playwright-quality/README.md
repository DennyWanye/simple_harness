# DeepResearch v5 Playwright 质量闭环测试入口

> 日期：2026-07-16
> 计划：`plans/2026-07-16-deepresearch-playwright-quality/`
> 平台范围：**仅 Windows 11 x64**
> 当前状态（2026-07-17 质量修复复验）：**核心质量修复与 TC-UI-NOW 单次立即生成主链 PASS；Gate F 总体 PARTIAL。** 三类真实 UI root 已恢复有效证据与正确三态交付；未执行项保持 PENDING。

## 范围边界

- 本组用例验证 DeepResearch v5 的问题建模、缺口补证、Playwright 离线运行、证据准入、专业报告、三态交付、控制动作、进度投影与历史兼容。
- 安装验证直接在当前 Windows 11 主机使用隔离安装目录和隔离用户数据目录完成。
- **Windows 10、Hyper-V、虚拟机、ISO、Windows Sandbox 和任何要求重启系统的操作均不在本轮范围，也不是前置依赖。**
- Windows 11 证据不得外推为 Windows 10 已兼容。
- 涉及 UI 的结果必须来自 Computer Use 真坐标点击/输入、截图和真实运行栈证据；脚本、直接 WebSocket 注入、导入后端模块或单测不能替代真人 E2E。

## 文档与职责

| 文档 | 内容 | 当前结果 |
|---|---|---|
| [automated-benchmark-test.md](./automated-benchmark-test.md) | SC-EDU/AI/PW/BLOCK/RESCUE/LEASE/PLATEAU/CAP/NOW/CONTINUE 的自动化与 benchmark 门 | PENDING |
| [win11-isolated-install-manual-test.md](./win11-isolated-install-manual-test.md) | Win11 隔离安装、断网首启、动态页、更新、卸载和 orphan 检查 | PARTIAL：安装/离线 bundle PASS；updater/卸载 PENDING |
| [desktop-ui-manual-test.md](./desktop-ui-manual-test.md) | Xiaomi 桌面真人 UI、进度卡、逐步气泡、立即生成、重连、重启、继续调研 | PARTIAL：AI/统计/产品与单次立即生成主链 PASS；立即生成的重复/重连/强杀分支及 history PENDING |
| [idempotency-review.md](./idempotency-review.md) | 控制动作、事件投影、报告写入、迁移与恢复的副作用幂等审查 | PENDING |
| [RESULTS.md](./RESULTS.md) | 唯一 testcase 执行状态汇总；没有证据时不得改成 PASS | PARTIAL |

AC→Task→code→testcase→result 的逐条关系见计划目录
`traceability-matrix.md`。执行证据应放入本计划的 `evidence/win11/` 或对应
Gate F results 文件，并从 `RESULTS.md` 和追溯矩阵反向链接。

## 统一结果语义

- `PASS`：步骤全部执行，预期全部满足，并有可定位的证据链接。
- `FAIL`：已执行且至少一个预期不满足；记录实际结果和复现信息。
- `BLOCKED`：按纪律尝试至少 3 种不同 workaround 后仍受外部环境阻塞；不得用 BLOCKED 掩盖产品失败。
- `PENDING`：尚未执行或证据尚未归档。本文档创建时所有 Gate F 用例均为 PENDING。

## 统一证据要求

1. 自动化记录命令、开始/结束时间、退出码、pass/fail 数和日志路径。
2. benchmark 保存输入 fixture、机器 audit、report/trace hash 与人工 review；不能只记录 pass 数。
3. 每个真人动作前写：`坐标=(x,y)|动作=<点击/输入/关闭/启动>|期望=<可观察结果>`。
4. 每个真人 case 至少保存动作前截图、关键中间截图、终态截图，以及对应 backend/workflow DB/log 证据。
5. 截图不得包含账号、密码、token、Cookie 或其他凭据；UI 也不得显示 raw query、URL、prompt、异常栈或 token 明细。
