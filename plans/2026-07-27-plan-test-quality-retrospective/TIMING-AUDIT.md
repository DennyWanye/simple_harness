# plan-test / plan-task 时间审计

> 日期：2026-07-27
> 时区：Asia/Shanghai（UTC+08:00）
> 对象：从 universal-action/Capability 共创到 `2026-07-24-human-anchored-companion-growth` 完成；重点拆解后者的 Task 0～16
> 口径：墙钟时间、Codex turn duration、Git milestone 三套证据交叉核对
> 限制：这是事后审计；原流程没有逐 Task 的 canonical timing ledger

## 0. 先说结论

从第一次进入 `plan-bs` 共创，到原计划的最终 turn 完成，一共经过：

```text
2026-07-23 14:43:47
→ 2026-07-27 05:48:29
= 87 小时 04 分 42 秒
```

这 87.08 小时不能全部算成“模型连续工作”：

| 区间 | 墙钟时间 | 可确认的运行时间 | 说明 |
|---|---:|---:|---|
| 前置窗口：plan-bs、上一份 Capability 计划执行、Companion handoff 准备 | 30.12h | 16.23h | 32 个 turn；24 complete、8 aborted；不能诚实拆成纯 plan-bs |
| 前置窗口到正式 Companion plan-task handoff | 1.45h | 0h | 两个流程之间没有活动 turn |
| plan-task：Task 0～16 | 55.51h | 35.39～38.41h | 21 个 turn；13 complete、7 aborted、1 个缺 terminal |
| **合计** | **87.08h** | **51.62～54.64h** | active 含命令、构建、测试、provider、子代理和等待工具返回 |

因此，对“为什么这么久”的准确回答是：

1. 确实有约 17～20 小时是 turn 之间的暂停、等待继续、额度恢复或没有执行；
2. 但即使只算有证据的运行时间，51.62～54.64 小时仍然过长；
3. 真正的主要耗时不是某一个慢命令，而是超大计划、批量实现、末尾集中 E2E、反复全量门、
   上下文压缩与中断恢复叠加；
4. 耗时很长并没有换来同等质量，因为测试 oracle 和放行协议本身有缺陷。

还必须避免一个统计误导：87.08h 是从最初工具能力共创开始计算的完整用户旅程，前 30.12h
中已经穿插了上一份 universal-action/Capability 计划的代码执行，并不全是“写 Companion
计划”。单独的 Companion `plan-task` 才是后面的 55.51h。

## 1. 统计口径

### 1.1 墙钟时间

墙钟时间使用以下事实点：

- 用户首次发起 `plan-bs` 的主 rollout 时间；
- planning turn 的最终 terminal；
- 用户发出正式 `plan-task` handoff 的时间；
- 最终 commit 与对应 turn complete 时间；
- Task milestone commit 和证据文件时间。

墙钟时间包含睡眠、等待用户继续、额度恢复和 turn 之间没有运行的时段。

### 1.2 运行时间

主 rollout 为每个 Codex turn 保存：

```text
task_started
task_complete.duration_ms
turn_aborted.duration_ms
```

本审计将 completed 和 aborted turn 的 `duration_ms` 相加。它表示该 turn 从开始到 terminal
所经过的时间，包含：

- 模型推理；
- 子代理；
- Shell、pytest、Vitest、Cargo、Tauri 和 provider 等工具等待；
- 文件检查、补丁和证据整理；
- turn 内的长任务等待。

它不是“纯 CPU 时间”，也不等于人工编码工时。

### 1.3 一个日志缺口

plan-task 有一个 turn：

```text
019f9f08-9b1e-7c52-8e7e-6a126da472b0
```

有 `task_started`，没有 `task_complete/turn_aborted`。它从
`2026-07-26 23:26:09` 开始，下一个 turn 在 `2026-07-27 02:27:14` 开始，跨度
3.02h。

因此：

- **35.39h**：有 terminal duration 的精确下限；
- **38.41h**：把整个 missing-terminal span 都视为运行的上限估算。

不能把 38.41h 伪装成精确数字。

### 1.4 可复核证据

- 主 rollout：
  `C:\Users\Administrator\.codex\sessions\2026\07\23\rollout-2026-07-23T14-42-46-019f8db6-5cd1-72c2-a1cb-d0a6322f5978.jsonl`；
- Git milestone：`0970682e^..a4becac8`；
- 定稿计划与 Task 定义：`plans/2026-07-24-human-anchored-companion-growth/plan.md`；
- 自动化、真人与最终审计：
  `plans/2026-07-24-human-anchored-companion-growth/evidence/`。

rollout 当前约 342MB；本审计只读取 task lifecycle、用户 turn、compaction/tool 计数，没有把
完整对话或可能的敏感内容复制进仓库。

## 2. 为什么前置窗口花了 30.12 小时

### 时间

```text
开始：2026-07-23 14:43:47
最后前置 turn 结束：2026-07-24 20:50:48
墙钟：30h 07m 00s
有记录的运行：16.23h
```

Companion 计划文件在 `2026-07-24 15:34:26` 已形成，但同一个混合 turn 持续到
20:50:48。现有日志无法拆分这段时间中上一份 Capability 实施、Companion 挑战和 handoff
准备各占多少，所以不能把文件 mtime 当成前置工作真正结束，也不能把后续全部归给 plan-bs。

### 这一步做了什么

- 通过多轮对话区分“主窗口、Session、Run、Task、Profile、Driver”；
- 重新检查 ProductTurnPreparer → RunKernel → Driver 生产链；
- 讨论模型驱动的路由、失败吸收和重新规划；
- 用户在 `2026-07-24 03:04:37` 已授权“修改完成后直接开始 plan-task”；此后同一窗口实际
  产生了 universal-action、Capability pack、OS runtime、权限和 UI 等代码提交；
- 调研三个已有 Capability worktree；
- 编写 acceptance、架构基线、架构图、research、challenges 和 spikes；
- 将计划挑战到 4,676 行、17 个 Task、16 条 MUST AC、9 个场景。

### 为什么慢

- 用户需求在讨论中持续扩展：能力包、自写工具、auto、Driver/Profile、失败重规划、
  Companion 长期成长都被纳入；
- 架构解释、需求确认、第一份计划执行、真实代码调研和下一份 handoff 交错进行，已经不能
  从现有日志诚实还原 A/0/1/2 与上一份 plan-task 的独立耗时；
- 32 个 turn 中有 8 个 aborted；
- 最后一轮单独运行 9.98h 后仍以 `turn_aborted` 结束；
- 计划没有在复杂度超限时强制拆 program/slice，反而继续把内容装进一份 plan。

## 3. plan-task 的真实时间线

### 总时间

```text
正式 handoff：2026-07-24 22:17:39
最终 commit：2026-07-27 05:47:35
最终 turn complete：2026-07-27 05:48:29

到 commit：55h 29m 55s
到 turn complete：55h 30m 50s
```

### Turn 统计

| 指标 | 数值 |
|---|---:|
| task_started | 21 |
| task_complete | 13 |
| turn_aborted | 7 |
| missing terminal | 1 |
| completed turn duration | 32.15h |
| aborted turn duration | 3.24h |
| terminal-recorded active | 35.39h |
| missing-terminal 最大跨度 | 3.02h |
| active 可信区间 | 35.39～38.41h |
| turn 间无运行区间 | 17.10～20.12h |

`aborted` 不等于所有工作丢失：已经写入工作区、提交或保存的测试结果仍会保留。但每次恢复
都要重新读取上下文、核对 HEAD/进程/计划进度，产生额外开销。

## 4. Git milestone 间隔：不能还原逐 Task 净耗时

下表是 **milestone-to-milestone 墙钟时间**，不是排他的 CPU 时间。并行任务、测试和证据
回写会交错；证据不足以可靠拆开的 Task 不强行制造假精度。

| Task / 阶段 | 截止时间 | 从上一 milestone 起 | 主要内容 |
|---|---|---:|---|
| Task 0 + 上游 universal-action 收口 | 07-24 23:43:22 | 1h 25m 42s | HEAD、dirty、三个 worktree、schema、绿色基线；区间同时包含 `0970682e/f844a706/cde016ad` |
| Task 1 | 07-25 00:25:24 | 42m 02s | Durable growth authority / Store |
| Task 2 | 07-25 01:09:02 | 43m 38s | Human identity 与 owner scope |
| Task 3 | 07-25 01:52:09 | 43m 07s | GrowthEvent substrate |
| Task 4 | 07-25 02:13:40 | 21m 31s | Preference authority |
| Task 5 | 07-25 02:36:10 | 22m 30s | Recoverable dormant runtime |
| Task 6 | 07-25 02:55:24 | 19m 14s | Governed action foundation |
| Task 7 | 07-25 04:53:09 | 1h 57m 45s | owner-scoped Skill、三阶段 dispatch、durable runtime integration |
| Task 8～9 | 07-25 11:08:03 | 6h 14m 54s | Candidate、Evaluator、RiskPolicy、activation；两者共享闭环证据，无法可靠拆开 |
| Task 10 | 07-25 14:25:47 | 3h 17m 44s | Capability/Skill instruction 与 runtime authority |
| Task 11 | 07-25 15:04:06 | 38m 19s | Reminder authority |
| Task 12 | 07-25 16:47:52 | 1h 43m 46s | Durable notification 与 UI |
| Task 13 初次 cutover | 07-25 19:21:55 | 2h 34m 03s | 切换成长 authority |
| Task 14 初次自动化门 | 07-25 20:11:20 | 49m 25s | 初次质量门 |
| Task 15 S-6/S-7 自动化 | 07-25 20:41:48 | 30m 28s | lifecycle automation；真人矩阵尚未完成 |
| 跨 Task production integration + Task 16 初稿 | 07-26 03:57:53 | 7h 16m 05s | `e8290719` 横跨多个早期 Task；全量 Harness、性能/隐私/权限重跑；`0f966f47` 已写交付审计初稿 |
| 跨 Task 修复 + Task 15 真人 E2E + Task 16 最终收尾 | 07-27 05:47:35 | 25h 49m 42s | Tauri 主消息页、6 个 required 场景、代码/测试修复、证据整理、架构/DoD 和 99-file 最终提交 |

合计：55h 29m 55s。

最后四行必须按交错阶段理解：

- Task 13 以及更早的 production wiring 并没有在第一次 cutover commit 后真正结束；
- Task 14/15 自动化先绿，随后又发现生产 wiring 和质量门需要回补；
- Task 15 真人 E2E 被集中推迟到最后；
- Task 16 的代码修复、文档、测试结果和最终审计与 Task 15 证据回写交错，不能诚实拆成
  独立分钟数。

## 5. 为什么 Task 8～16 特别慢

### 5.1 规模本身很大

最终范围包含：

- 17 个 Task；
- 16 条复合 MUST AC；
- 9 个场景；
- 25 个连续 milestone commit；
- `0970682e^..a4becac8` 共 25 个 first-parent commit；
- 两个端点之间的 **净 diff** 为 601 files、164,066 insertions、14,440 deletions。

其中大量增量来自测试、架构文档和真人运行 evidence，但依然说明它不是一个适合一次性交付
的 release unit。

### 5.2 自动化不是跑一次

Task 14 和 Task 16 记录了多组重复或交叠的：

- Companion；
- Skill / Memory；
- Harness；
- Capability / Workflow；
- Frontend Vitest / TypeScript；
- Rust check/test；
- 故障、性能、隐私和权限；
- deterministic smoke。

这些数字不能简单相加为“独立 testcase 数”，因为有重叠和重跑。但每次全量回归都真实
消耗构建、执行、诊断和清理时间。

### 5.3 真人 E2E 全部压到末尾

最后 25h49m 窗口承担了：

- 多个隔离 user-data；
- Tauri 启动和真实 provider；
- S-1～S-5、S-8；
- 截图、日志、Session/Run/业务终态核对；
- 每个实例按 PID/create-time/端口/Job Object 清理；
- survivor=0 与 private memory 记录；
- 失败后的修复、重启和重测；
- testcase、manual-results、Task 16 与架构文档回写。

如果核心用户旅程在每个垂直 slice 结束时就测试，很多问题会更早暴露，也不需要最后一次性
重建全部环境。

### 5.4 上下文和中断开销非常高

仅 plan-task 窗口，主 rollout 记录：

| 事件 | 数量 |
|---|---:|
| context_compacted | 82 |
| mcp_tool_call_end | 1,824 |
| patch_apply_end | 812 |
| sub_agent_activity | 329 |

这些是事件流计数，不等于 1,824 个独立用户功能，也不能直接换算成人工工时。但它们说明：

- 工作上下文远超单次稳定执行的合理体量；
- 每次 compaction 都需要恢复任务、证据和进程状态；
- 大量工具调用和补丁发生在同一个 release unit 中；
- 七次 aborted turn 又放大了恢复成本。

### 5.5 进程与 worktree 纪律有必要，但成本高

本计划要求：

- 三个上游 worktree 逐个审计、测试、合并和清理；
- Tauri 不得和手动 backend/Vite 双启；
- 每次长测试都按完整命令行、worktree/config、PID/create-time、端口和 Job Object 清理；
- survivor=0，并记录 private memory。

这些要求是有价值的，因为项目此前确实出现过 orphan、错 backend、旧 frozen executable 和
端口冲突。但在没有统一 runner/attestation/cleanup receipt 工具时，每一轮都需要重复人工
编排。

## 6. 哪些时间是必要的，哪些是流程浪费

### 基本必要

- 真实代码和架构基线调查；
- worktree 中未知改动的保护与合并；
- 数据迁移、权限、Provider、Harness、UI 的跨层测试；
- 真实 Tauri/provider E2E；
- 精确进程清理；
- 失败后修复和永久回归。

### 明显可以减少

1. **超大 plan 没有提前拆分**
   - 4,676 行计划进入一次 100% 执行；
   - Task 8～16 相互依赖，无法独立关闭。
2. **核心旅程测试太晚**
   - 新话题、工具轨迹、中间状态、历史恢复没有在对应 slice 结束时验证；
   - 最后 25h49m 才集中跑昂贵真人矩阵。
3. **全量回归重复过多**
   - 缺少 change-impact → required suites 的机器映射；
   - 为了保险多次重跑大范围 Harness/Frontend/Rust。
4. **没有 canonical timing/evidence ledger**
   - 每次恢复都重新找日志、截图、命令和状态；
   - Task 8/9、13/14、15/16 已无法事后精确拆分。
5. **turn 过长**
   - planning 最长 turn 9.98h；
   - plan-task 多个 turn 超过 4～6h；
   - 82 次上下文压缩说明单 turn/release unit 已超过可审计尺度。
6. **错误 oracle 让一部分测试投入没有保护用户行为**
   - 测试被修改为 same-session 后继续绿色；
   - 花更多时间跑相同 oracle 不会提高正确性。

当前 `plan-task/SKILL.md` 其实已经写着“记录各阶段耗时、复测按 change-impact 路由”。本次
没有形成逐阶段 timing ledger，且多次重复大范围回归，说明问题再次不是缺少一句规则，而是
这条规则没有结构化 producer、validator 和最终 gate。

## 7. “完成后又修”的额外质量成本

原计划在 `2026-07-27 05:47:35` 提交完成。用户真实操作后，随后又产生：

| 时间 | 提交 | 修复 |
|---|---|---|
| 11:59:49 | `991aeaca` | 首次处理新话题/owner inbox |
| 13:05:34 | `806a14f6` | 恢复真正 UUID 新 Session |
| 15:37:24 | `891762a7` | 默认工具轨迹、durable Run Inspector、provider 审计 |
| 16:46:56 | `14fcdca5` | provider scope TTL、历史投影、raw/observed 状态 |

从首次“完成”到最后一笔修复又经过约 10h59m。这不属于原 55.51h plan-task，但属于漏测带来
的实际返工成本。

同时必须保持准确归因：

- same-session 行为能追到明确错误 oracle；
- 其他问题可能是既有或跨模块缺陷，能确认的是原验收没有发现；
- Harness Inspector 是后续增强需求；
- autonomy policy 是此前没有定义清楚的产品行为。

## 8. 应该怎样缩短下一次运行

### 8.1 先改变交付单位

超过以下任一阈值，不允许进入一次性执行：

- MUST AC > 8；
- Task > 10；
- plan > 2,000 行；
- 同时跨 UI、Session、Harness、Provider、权限中的 3 类以上。

应先拆 program plan，再按小型垂直 slice 独立完成。

### 8.2 每个 slice 当场跑核心旅程

例如 Session slice 完成后立即验证：

```text
点击新话题
→ Session ID 改变
→ 新 Session 空态
→ 首条消息和 root Run 落入新 Session
→ 旧 Session 零 mutation
```

不能等所有 17 个 Task 完成后才第一次真人点击。

### 8.3 自动化按影响范围路由

先运行：

```text
changed components
→ required edges
→ required suites
→ required UI journeys
```

只有跨层或最终 program gate 才运行完整全量矩阵，减少无差别重跑。

### 8.4 把 timing 变成强制证据

新的 verification ledger 应自动记录：

```text
slice/task
command/tool
started_at / ended_at
elapsed
wait_reason
retry/abort
test count
runtime identity
evidence hash
```

最终报告直接生成：

- 实现时间；
- 测试时间；
- 等待时间；
- 中断恢复时间；
- 返工时间；
- 用户/额度等待时间。

下一次不应再依靠 Git commit 和 342MB rollout 事后推算。

### 8.5 限制单 turn 和强制 checkpoint

建议将下列规则作为可观测性目标，而不是假装它们已经实现：

- 一个执行 turn 连续 90～120 分钟必须写 checkpoint；
- checkpoint 记录 HEAD、dirty、当前 Task、进程、测试、证据和下一动作；
- compaction 后只从 checkpoint 增量恢复；
- aborted turn 必须记录 `reason + preserved outputs + redo scope`；
- 超过时间预算的 Task 自动拆分，而不是继续延长同一 turn。

## 9. 最终判断

这次运行慢，有一部分来自任务确实庞大、测试和真机纪律确实昂贵；但更重要的是流程把：

```text
需求共创
→ 17 Task 实现
→ 大范围自动化
→ 6 场景真人 E2E
→ 最终审计
```

塞成了一个 release unit。

最终结果是：

- 墙钟 87.08h；
- 可确认/合理估算的运行 51.62～54.64h；
- 原 plan-task 55.51h；
- 原计划完成后又付出约 10h59m 修复窗口；
- 仍然漏掉了用户最容易观察到的关键行为。

所以改造目标不应只是“让它跑得更快”，而应是：

> 更小的 slice、更早的真人核心旅程、更少的无差别全量回归，以及每一分钟都能归因的
> canonical timing/evidence ledger。
