# Windows 真机 UI 证据索引

> 设备：Xiaomi 1920×1080（`DISPLAY2`）
> 应用：DeskPet Tauri dev，已登录，未显示或记录任何凭据
> run：`bb09339bc2dc44dba8477e1ff44ec98a`（DeepResearch v3，WebGPU）

## 截图

| 文件 | 可复核事实 |
|---|---|
| [03-xiaomi-compact-timeline.png](./03-xiaomi-compact-timeline.png) | 同一画面显示 13/13 总体卡、末三步动作/结果、Markdown ArtifactCard 与最终引用报告。 |
| [05-xiaomi-timeline-actions-results.png](./05-xiaomi-timeline-actions-results.png) | compact timeline 折叠态显示“理解任务”“规划调研”“扩展查询”及各自一行结果；ArtifactCard 同屏。 |
| [06-xiaomi-expanded-plan-stage.png](./06-xiaomi-expanded-plan-stage.png) | “规划调研”单步展开；显示动作“拆分可独立核验的问题”、结果“拆分 5 个问题，启用 5 个分支”、问题/分支计数、7 秒耗时与下一步 `expand`，其他阶段保持紧凑。 |
| [01-xiaomi-current-session.png](./01-xiaomi-current-session.png) | 最终报告底部的引用、Coverage 与 degraded/error diagnostics。 |

`02`、`04` 是相同状态的补充捕获；核心判定使用上表四张。

## 后端与 durable 事实源

- 可提交脱敏摘录：[`backend-safe-excerpt.txt`](./backend-safe-excerpt.txt)。它只保留 run/version、事件计数、delivery 状态和 provider/quality 汇总。
- 机器 benchmark：[`benchmark-live-final.json`](../benchmark-live-final.json)。
- 本机原始事实源为 gitignored Tauri 日志与 workflow DB，仅用于生成和交叉核对上述脱敏摘录，不在证据索引中直接链接，也不是提交后复核的唯一依据。

截图用于判定可见 UI，脱敏摘录用于判定真实 v3 运行、provider attempt/probe 与 outbox delivery；二者共同满足 AC-UI-06，不以协议注入或脚本回放代替真机操作。
