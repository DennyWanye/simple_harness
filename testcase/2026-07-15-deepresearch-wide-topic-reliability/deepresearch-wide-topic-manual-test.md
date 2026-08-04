# DeepResearch 宽主题可靠性与失败重试手工测试

对应计划：`plans/2026-07-15-deepresearch-wide-topic-reliability/plan.md`

## 测试目标

验证用户原始问题“可以帮我调研一下，现在AI 相关的最新最有价值的技术相关的信息吗？”在真实 DeskPet 运行栈中能够生成有时间窗、价值排序、成熟度和有效引用的 DeepResearch v4 报告；同时验证搜索过程的真实计数、逐步结果、诚实失败、后续步骤跳过和一键重试。

## 前置条件

- 使用 `F:\projects\deskpet` 主 checkout，不手动启动 backend 或 Vite，只启动 Tauri。
- 成功用例使用主配置和独立测试 userdata；同一 Tauri/backend 进程连续执行两次原始问题。
- 失败用例只能使用 `scripts/e2e/launch_deepresearch_failure.ps1` 生成的隔离配置、userdata、8116/5186 端口；结束后必须执行配套 stop 脚本。
- 在小米屏幕执行。每个 UI 动作前记录 `坐标=(x,y)|动作=...|期望=...`，并保存动作前后截图。
- 日志证据只能保留 run/request/provider/status/count/elapsed/reason code，不保存原始 query、URL、正文、token 或 secret。

## TC-01：同进程连续两次成功调研

目的：证明宽主题不会因为同一 run 的 provider burst/cooldown 连坐退化为 0 候选，并且第二次运行不会继承错误状态。

1. 仅启动一次 Tauri，确认日志显示 Dev Python backend 路径正确。
2. 在新 Session 输入原始问题并发送，等待 v4 运行进入终态。
3. 在同一应用进程中新建 Session，再次输入完全相同的问题并发送，等待终态。
4. 分别展开两次运行的进度卡和最终报告。

预期：

- 两个 run 均为 `deep_research@v4` 且成功；每个 run 只出现一份最终报告和一张 report ArtifactCard。
- 报告开头明确时间窗和排序口径；Top 技术逐项包含“发生了什么、为什么重要、成熟度、引用”。
- winning item 均有实际支持它的引用，publish gate 不降低。
- 两次运行的搜索阶段至少一条 branch 命中；不存在同 run 把可用 provider 打入 cooldown 后全 run 0 候选。

失败判定：任一次 0 候选、伪完成、缺少引用、第二次被第一次 circuit 状态拖垮，或出现重复交付，均为 FAIL。

## TC-02：进度卡逐步结果与计数语义

目的：用户默认折叠时能看懂总体进度，展开后能看懂每一步做了什么和结果如何。

1. 在运行中观察折叠态标题、当前步骤和整体进度。
2. 展开进度卡，逐项检查已完成、进行中和未开始/跳过步骤。
3. 重点检查搜索步骤的计数标签和每一步结果摘要。
4. 完成后折叠再展开，确认状态稳定。

预期：

- 默认折叠仅显示一张聚合卡，不刷出多条普通聊天气泡。
- 展开后每个已完成步骤都有动作和结果；不能只显示“做到哪一步”。
- 搜索计数明确区分：真实请求、命中、空结果、超时、cooldown 跳过、busy 跳过、排队超时、probe。
- `真实请求 = 命中 + 空结果 + 超时 + blocked + captcha + rate limit + error`；routing decisions 不冒充真实请求。
- 完成态使用可访问的 status live region，折叠按钮有正确 `aria-expanded`。

失败判定：计数混用、只有阶段名没有结果、刷新/折叠后重复步骤，或用原始 query/URL 当诊断文案，均为 FAIL。

## TC-03：隔离网络失败时诚实终止

目的：搜索与一轮救援仍为 0 candidates 时，不继续制造伪报告。

1. 启动前确认 8116/5186 空闲，运行 `scripts/e2e/launch_deepresearch_failure.ps1`。
2. 确认测试 app 是本次新 PID，日志显示 Dev Python backend 且只注册 searxng。
3. 在小米屏幕真实输入原始问题并发送。
4. 等待失败终态，展开进度卡和失败卡。

预期：

- 搜索步骤显示失败原因和安全 provider 汇总；后续 fetch/score/synth/cite/persist 明确显示“未执行”或 skipped。
- 总体卡为失败，不显示完成。
- Session 中没有 research_report ArtifactCard，也没有伪装成完整报告的最终 Markdown。
- 失败卡显示“重新调研”按钮；诊断不泄露原始 query、URL、正文或 secret。

失败判定：出现报告 Artifact、完整报告 Markdown、后续步骤被标完成、失败卡缺少重试入口，任一即 FAIL。

## TC-04：失败卡一键重试与防重复

目的：验证重试只通过安全服务端 start reference 创建新 run，且 UI 不重复发送。

1. 在 TC-03 的失败卡点击一次“重新调研”。
2. 在按钮 pending 状态再次点击或按 Enter，观察是否重复创建。
3. 等待新 run 出现；折叠/展开旧失败卡，确认旧 run 没有重新交付。
4. 若控制通道发生暂时断连，恢复连接后用同一 retry action 再试。

预期：

- 一次用户动作只创建一个新 run；重复点击被禁用或由相同 retry key 幂等去重。
- 新 run 复用服务端保存的原请求引用，前端 payload/progress 不含原始 query。
- 旧 run 的失败卡、进度和 delivery 不重复；新 run 有独立 run id。
- 断连时不会把 retry 放入普通 chat outbox；结果不确定时保留相同 retry key。

失败判定：一次点击创建多个 run、旧 delivery 重放、前端发送 raw query，或断连后静默丢失/换 key 重试，均为 FAIL。

## TC-05：失败环境清理与主配置完整性

1. 运行 `scripts/e2e/stop_deepresearch_failure.ps1`。
2. 校验 manifest 中 app/CLI PID 已退出，8116/5186 无 listener。
3. 校验主 `config.toml` 未被失败夹具修改；其他 DeskPet 进程未被误杀。

预期：全部清理断言通过，失败配置与 userdata 仅留在 evidence 目录，主配置保持原状。

## 结果汇总

| 用例 | 结果 | 截图/日志证据 | 备注 |
|---|---|---|---|
| TC-01 | PASS | runs `43e851a0...` / `59975eb1...`，`consecutive-success-run1/2.json` | 最新代码、同一 Tauri/backend 进程连续完成；各只有一份 delivery，第二轮仍抓取 17/成功 16，未被 cooldown 连坐 |
| TC-02 | PASS | `success-tauri.stderr.log` + Xiaomi 消息窗 | 13 阶段动作、结果、降级与详情入口可见；最终报告可在同一 Session 查看 |
| TC-03 | PASS | `evidence/manual-failure-4e6d1b0b.json` | 失败无 report/Artifact/final assistant，有安全诊断与 retry |
| TC-04 | PASS | 失败卡真实点击 + workflow service/frontend 幂等回归 | 独立新 run，旧 delivery 不重放 |
| TC-05 | PASS | 独立 Tauri PID/端口与主配置复核 | 主 `config.toml` 未被失败夹具改写 |

内容质量最终验收：

- run `43e851a0d1264bcd8ecfe90f258c5afa` 发布 5 项、5 个可映射引用，当前质量检查 17/17 PASS。
- run `59975eb1a85a409e97e0277dcfe4a705` 发布 5 项、5 个可映射引用，当前质量检查 17/17 PASS。
- 两份报告均包含一页式执行摘要、组合建议、分主题核心变化/价值/成熟度/风险/日期/引用和方法局限。
- 两份报告的弱证据项均在摘要与正文写为“先补齐一手来源与独立来源，再决定是否 PoC”，且无重复标点。
- 旧 run `66720bcf682b46eeb6e6923a11ec02eb` 因同实体重复、同分、英文 claim、串题和 Coverage diagnostics 过重，永久作为失败基线，不得再引用为 PASS。
- 早期改进 runs `37c53ab8...` / `f3e4ee36...` 因跨段 PoC 建议不一致，保留为质量门演进证据，不得再引用为最终 PASS。

TC-01～TC-05、自动化门禁、隐私扫描和两轮真实报告质量门均已 PASS；最终完成度审计结果见 plan results。
