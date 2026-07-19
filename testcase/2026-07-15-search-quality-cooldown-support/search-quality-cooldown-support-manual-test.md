# Search Gateway cooldown 隔离、DeepResearch v3 引用质量与可视化进度手工测试

> 执行日期：2026-07-15
> 被测环境：Windows 11，Xiaomi 1920×1080 屏幕，Tauri dev 应用 `com.deskpet.app`
> 被测主链：内置 Search Gateway + `deep_research/v3` + Session durable progress
> 自动化与完整结果：[`plans/2026-07-15-search-quality-cooldown-support/results.md`](../../plans/2026-07-15-search-quality-cooldown-support/results.md)

## 1. 测试前置

1. 只启动 Tauri；不要单独启动 backend 或 Vite。
2. Tauri 环境必须指向本 checkout 的 `backend`，日志确认 dev Python 路径，而不是 bundled backend。
3. 应用已完成登录，聊天主界面可见。
4. `[research].deep_research_version="v3"`，Search Gateway 与 v3 质量门均为默认开启。
5. 涉及点击时按“截图/快照 → 声明坐标与期望 → 真点击/输入 → 截图 → 日志或持久化事实源判定”执行。

## 2. 用例与执行结果

### TC-01：折叠态逐步展示动作与结果

目的：用户不展开详情，也能知道已经做了哪些事、每一步得到什么结果。

| 步骤 | 操作 | 预期 | 2026-07-15 结果 |
|---|---|---|---|
| 1 | 坐标 `(100,1045)` 点击新话题 | 新 Session 打开 | PASS |
| 2 | 坐标 `(900,995)` 聚焦输入框，粘贴深度调研请求并按 Enter | 创建 v3 workflow run | PASS，run `61364802154a444db6dcf193d9f1ab0d` |
| 3 | 观察总体进度卡 | 卡片始终可见，显示 `n/13` | PASS，实见 `2/13` |
| 4 | 不展开任何 child | 已完成行直接显示动作和一行结果 | PASS，实见“理解任务”“规划调研”及各自结果 |
| 5 | 继续观察 degraded/失败原因 | cooldown、timeout、低质量丢弃等白名单原因直接可见 | PASS，前端投影与自动化均覆盖 |

判定：**PASS**。默认折叠隐藏的是附加详情，不再隐藏阶段产出。

截图证据：[`05-xiaomi-timeline-actions-results.png`](../../plans/2026-07-15-search-quality-cooldown-support/evidence/05-xiaomi-timeline-actions-results.png)。

### TC-02：单步展开查看指标、诊断与下一步

目的：用户需要审计某一步时，可以就地展开，不产生额外聊天噪音。

| 步骤 | 操作 | 预期 | 2026-07-15 结果 |
|---|---|---|---|
| 1 | 在运行中的总体卡定位阶段行 | 每一行可独立展开 | PASS |
| 2 | 坐标 `(1868,263)` 点击“检查证据缺口” disclosure | 仅该阶段展开，其他行保持紧凑 | PASS |
| 3 | 读取展开内容 | 显示 round、新查询数、新证据数、耗时和下一步 | PASS，实见下一步 `rerank` |
| 4 | 用键盘 Enter/Space 操作 disclosure | 原生按钮语义工作，`aria-expanded` 正确 | PASS（自动化 + tsc） |

判定：**PASS**。

截图证据：[`06-xiaomi-expanded-plan-stage.png`](../../plans/2026-07-15-search-quality-cooldown-support/evidence/06-xiaomi-expanded-plan-stage.png)。

### TC-03：完成报告与 Artifact 交付

目的：进度结束后必须交付正文与可操作的 Markdown Artifact，不能只停在 13/13。

| 步骤 | 操作 | 预期 | 2026-07-15 结果 |
|---|---|---|---|
| 1 | 等待 run 进入终态 | 总体卡显示完成，阶段历史仍可查看 | PASS |
| 2 | 坐标 `(1800,900)` 向下滚动 | Session 中显示最终报告和 ArtifactCard | PASS |
| 3 | 核对 durable delivery | `final_assistant` 与 `workflow.artifact_card` 均为 delivered | PASS |
| 4 | 核对产物 | Markdown 文件存在且非空 | PASS，`DeepResearch/请对-Official-browser-support-status-for-W-bb09339bc2dc44dba8477e1ff44ec98a.md`，13441 bytes |

判定：**PASS**。

截图证据：[`03-xiaomi-compact-timeline.png`](../../plans/2026-07-15-search-quality-cooldown-support/evidence/03-xiaomi-compact-timeline.png) 在同一画面显示 13/13、阶段结果、ArtifactCard 与最终报告；日志索引见 [`evidence/README.md`](../../plans/2026-07-15-search-quality-cooldown-support/evidence/README.md)。

### TC-04：同一进程连续三类真实调研

目的：复现并阻断“一次高压 run 使后续无关主题全部 cooldown”的连坐问题，同时验证引用支持率。

执行时不重启应用/backend，依次提交固定三类题目；结果以 workflow DB、coverage 和 [`benchmark-live-final.json`](../../plans/2026-07-15-search-quality-cooldown-support/benchmark-live-final.json) 为准。

| 类别 | run_id | 状态 | 耗时 | 支持率 | 引用 / 独立域 | provider 探针 |
|---|---|---:|---:|---:|---:|---:|
| Python 官方技术文档 | `904c4117c8a848aaa395df1792301185` | completed | 59065 ms | 1.0 | 9 / 6 | 0 |
| 中国生成式 AI 政策 | `6621f93a890e443eb0e5d69f10f0173c` | completed | 66409 ms | 1.0 | 10 / 7 | 2 |
| WebGPU 官方支持状态 | `bb09339bc2dc44dba8477e1ff44ec98a` | completed | 60055 ms | 1.0 | 8 / 8 | 2 |

判定：**PASS**。3/3 completed，mean support rate 1.0，nearest-rank P95 66409 ms；政策 run 后的 WebGPU run 有真实 provider attempt/probe，并非被前一主题连坐为零候选。

### TC-05：失败与证据不足必须诚实呈现

目的：上游不可用或质量门不达标时，不伪造完成报告。

| 步骤 | 操作 | 预期 | 结果 |
|---|---|---|---|
| 1 | 观察 provider cooldown/timeout 分支 | compact timeline 显示安全诊断，不显示 query、URL、正文或凭据 | PASS（聚焦自动化） |
| 2 | 构造引用、事实数、独立域或正文长度不达标 | v3 返回 `no_results/insufficient_evidence`，不以 appendix 或删分母凑门槛 | PASS（聚焦自动化） |
| 3 | probe 取消/失败后继续请求 | lease 被释放或重新 open，旧 token 不能关闭新 generation | PASS（fake-clock/并发自动化） |

判定：**PASS**。

## 3. 汇总

| 范围 | 结果 |
|---|---|
| Xiaomi 屏幕 Session 逐步动作/结果可视化 | PASS |
| 单步 disclosure 详情 | PASS |
| 最终报告与 Artifact delivery | PASS |
| 同进程三类真实网络 benchmark | PASS |
| cooldown 状态机与诚实 no-results | PASS |

最终结论：**SHIP**。
