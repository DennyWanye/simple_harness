# R5.5 持久 Admission 与切换就绪结果

> 日期：2026-07-21
> A-source 提交：`69c6980ae7297997fdffd3c49770adbf0c7e5c80`
> 本切片后的生产 owner：`legacy/0`

## 结果

R5.5 已完成。休眠态 ProductVenue 现在通过一个持久 Admission boundary 进入现有 Kernel，不再依赖内存审批缺口。本切片故意不激活生产 owner；原子 owner 切换留给 R6。

持久状态链：

```text
pending -> accepted_start_pending -> launch_claimed -> launched
       \-> rejected | cancelled | expired | launch_unknown
```

同一个 UoW authority 负责关联、等待、决策、launch claim、Provider footprint 和终态 delivery。`resolution_fingerprint` 锁定精确重放语义：相同信号幂等，改变内容或相互冲突的响应关闭失败。

幂等恢复复用稳定 `launch_operation_id`；非幂等歧义产生一个 `launch_outcome_unknown` final。`run.final` 是统一终态事件；历史终态事件仍兼容。

## 机器门禁

| 门禁 | 结果 |
|---|---:|
| 原始 orchestration LOC | `34,756 <= 34,800` |
| 迁移调整后 LOC | `34,247 <= 34,250` |
| 核心 LOC | `5,950 <= 5,950` |
| Kernel LOC | `924 <= 925` |
| Kernel 公开操作 | `6` |
| 公开事务 starter | `23` |
| execution-table DML authority | `1` |
| fault hook | `39` |
| run map / Supervisor / Presenter | `1 / 1 / 1` |
| 未分类 LOC | `0` |
| 产品能力映射 | `141 / 141`，未映射 `0` |
| 切换就绪 | 15 个 owner、14 个声明入口全部覆盖；休眠 factory 不可从生产到达 |

这些产物锁定到上面的 A-source 提交。切换清单证明 R6 的准备度，不代表已经激活。

## 验证

- Admission / UoW 聚焦验证：`42 passed`
- 最终重复 signal 修复后的产品审批回归：`12 passed`
- Harness 全量分两段运行：`218 passed, 8 xfailed` + `253 passed`，合计 `471 passed, 8 xfailed`
- Authority、parity、cutover readiness 和 LOC construction gate：全部通过
- [幂等性审查](./r55-idempotency-review.md)：无阻塞缺陷

一次超大 pytest 调用超过 300 秒，遗留的两个 pytest 进程随即按精确 worktree 命令行停止。之后改为两个有界分段重新完成全套测试。每个 spike、测试、benchmark 和 gate 结束后均确认 `cleanup_remaining=0`。

## 测试策略

R5.5 只修改休眠装配和持久后端边界，生产仍为 `legacy/0`，没有新的可达 UI 行为，因此本切片不把脚本模拟冒充真人点击。R6 激活后再覆盖 Text、Voice、审批、取消、重启恢复和终态 delivery 的 Windows 真人 E2E。

## 后续

R6 必须原子 drain `legacy/0`，把生产 owner 切到当前 Kernel generation，运行 exact / similarity / reference / reachability / live-stack 切换门，并执行真实 UI 验收矩阵。
