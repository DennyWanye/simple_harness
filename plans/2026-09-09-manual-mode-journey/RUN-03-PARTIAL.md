# Manual 模式旅程 run3（2026-09-09 07:15–08:05，Host dbf967fc，Memory 0.6.34，flash）— T1–T7 后主动中止

证据：`.local-test-evidence/2026-09-09/native-manual-run3/primary-ui-sil305gb`（含 `backend-vmmap-summary.txt`）。run2 在 T3 时遇整机内存耗尽被迫中止。

| 轮 | 类型 | 结果 | 观察 |
|---|---|---|---|
| T1–T2 | Auto | COMPLETED（41 s / 86 s） | 无任何弹窗；grant 全为 `policy:auto`（MM-2 / NC-M1 ✅） |
| T3 | UI 切 Manual | 记录 | 复选框 1→0；`workflow.db.authorization_policy_state` = manual/gen 1/user_explicit ✅；但 `sdk-product-state.db` 的同名表仍 auto/gen 0（驱动读的是后者）→ **发现 MM-D1：产品态镜像未随切换更新，需查哪张是权威、驱动应读哪张** |
| T4 | ask | FAILED（>6 min） | `context_route_binding_authorization_required` → 卡片，答「允许一次」后 grant `source=user gen=1` 落库（MM-3 ✅），但 Run 未恢复，超时 FAILED → **发现 MM-D2：授权应答后 Run 不恢复**（待与 T5 对照：T5 每次工具调用一张卡、逐张答后 Run 能完成） |
| T5 | ask | COMPLETED（465 s） | 24 张卡（每次 tool_search/tool_describe/page_in 各一张），逐张「允许一次」后完成；flash 仍在循环找文件工具 |
| T6 | ask | FAILED（641 s） | 36 张卡；17 次 tool_search + 12 次 page_in；未见预算超限日志（bundle 无 F-E3），Run 以时限失败 |
| T7 | ask | COMPLETED（729 s） | 自动应答 32 张工具卡；**未出现『项目目录授权』卡**，binding_rev 仍 1 → MM-4 未被行使（模型没走到跨根绑定） |

## 中止原因

1. Manual 模式下每次工具调用一张卡，叠加 flash 找文件工具的 tool_search 循环，每轮 8–12 分钟，15 轮要 2.5 小时且大多是噪音。
2. 后端 RSS 逐轮爬升：0.94 → 1.23 → 1.73 → 2.41 → 2.61 → 2.86 → 2.70 GB；`vmmap` 显示 **MALLOC_SMALL 2.1 GB 常驻**（Python 堆对象，而非模型权重的映射/大块分配）→ Python 级泄漏（事件 X，待剖析）；这就是 07:30 应用 17 GB 的来源。

## 下一次前置

- 新 bundle（含 F-E3、事件 T/U/V、F-S1b；钉 0.6.36/0.6.37）。
- 驱动 T5–T7 提示加「用 read_file 工具（不要用 shell）」，减少找工具循环。
- 事件 X：后端内存增长剖析与修复（tracemalloc 离线复现多轮）。
- MM-D1（策略镜像）、MM-D2（授权后 Run 不恢复）分别定位。
