# Task 16 — 最终交付与 100% DoD 审计

> 日期：2026-07-27
> 状态：`COMPLETE`
> 结论：`DECISION: SHIP`

## 1. 主要矛盾与验收范围

- 主要矛盾已解决：桌宠可以在唯一主 Session/Harness 上，从用户真实消息中由模型判断成长
  语义，积累长期偏好、生成/修改能力、独立评测、按风险激活、失败后吸取原因重新规划，并让
  用户查看、回滚和遗忘；没有第二套 Driver 或 code mode。
- AC-01～AC-16 均有生产代码与自动化证据；输入语义敏感的 S-1～S-5、S-8 已按 required
  策略完成真实 Tauri 主消息页 E2E，S-6/S-7/S-9 完成确定性自动化。
- 真实 positive-value 场景为 S-1、S-2、S-3、S-8；均得到非空业务结果并通过人工
  quality bar。没有用负向安全行为替代正向 AC。

## 2. 最终自动化门

| 范围 | 结果 |
|---|---:|
| Companion 全量 | `639 passed in 130.81s` |
| Skill / Memory | `128 passed in 11.33s` |
| Harness（逐文件隔离执行） | `636 passed, 4 xfailed` |
| Capability + Workflow | `308 passed in 38.41s` |
| Frontend Vitest | `93 files / 853 tests passed` |
| TypeScript | `tsc --noEmit` PASS |
| Rust check | `cargo check` PASS |
| Rust test | `78 passed` |
| Harness 漂移门聚焦 | `6 passed in 135.17s` |
| Deterministic smoke | `639 + 40 passed`，`DECISION: SHIP` |

Harness 合并运行在 Windows 上出现测试进程 handle/resource 累积，因此按同一既定文件集合
逐文件隔离执行并汇总；没有缩小文件清单。authority audit 保持 DML owner=1、
transaction starters=53、legacy survivor=0；ProductTurn mapping 仍为 141 项，只刷新
22 个 current callsite；public operations=6、unknown=0。R4.5
raw/adjusted/core/Kernel=`140270/139761/45032/1277`，均在
`140300/139800/45050/1300` 的有界预算内，AgentLoop AST 恰为 3800。

仓库级 `cargo fmt --check` 仍命中计划开始前已存在的 Rust 树大范围格式漂移。本计划没有
Rust 生产代码修改，因此没有批量格式化并提交无关文件来制造绿色结果；`cargo check` 与
全部 78 项 Rust 测试均通过。

## 3. 真人 E2E

| 场景集合 | 结果 | 证据 |
|---|---:|---|
| S-1～S-5、S-8 required 真人场景 | `6/6 PASS` | [manual-results.md](./manual-results.md) |
| S-6/S-7/S-9 确定性场景 | `3/3 PASS` | [manual-results.md](./manual-results.md) |

真人 E2E 均从桌宠点击“消息”打开主消息页，使用真实输入和真实 provider；没有协议直注、
脚本回放或直接调用 backend 冒充。日志确认每次测试使用当前源码 backend，并由 Tauri
自管唯一 backend/Vite。用户最终 Alt+F4 关闭的是已经完成取证的消息页面，不影响 durable
业务状态或证据。

## 4. 失败吸收与安全边界复审

- evaluation failure 的 report、candidate invalidation、reservation release、GrowthEvent
  和下一 reflection job 由一个 Store writer 原子提交；重放先恢复已冻结 report，不重新采用
  新模型输出或实时目录重算 payload。
- genesis reservation 可用匹配 version 从 released 原子 CAS 回 held；attempt 严格为非负
  整数且最多两轮。forget/用户撤销造成的 invalidated 与本评测精确重放分开，迟到评测不能
  复活候选。
- high-risk candidate 即使 Auto=ON 也只能进入 activation confirmation；外部发送还需要
  原 durable call 的一次性 action decision。S-5 确认前 dispatcher/effect/send 均为 0。
- 模型语义字段和 response-quality 结果都由 host 做 typed 校验；前者不改变 Driver/Profile
  authority，后者不建立第二套执行状态机。

## 5. Worktree、dirty scope 与提交边界

- 最终 `git worktree list --porcelain` 只有 `F:/projects/deskpet`。
- `F:\projects\deskpet-wt-cap-store`、`F:\projects\deskpet-wt-os-runtime`、
  `F:\projects\deskpet-wt-ui-godot` 已逐一核对：不再挂载且路径不存在；没有直接丢弃未知改动。
- 用户原有且不属于本计划的
  `ARCHITECTURE/DeepResearch.md`、
  `plans/2026-07-20-deepresearch-enumeration-quality/`、
  `plans/2026-07-20-deepresearch-topn-quality-followup.md`
  在 Companion 提交中保持未暂存；用户随后明确要求“全部提交”后，作为独立文档提交纳入，
  未与 Companion 功能提交混合。
- authority、migration mapping、execution build manifest 与 R4.5 budget 均按正式生成/
  审计路径校准；未写入 `STATUS/` 正文。
- 两次提交前均执行 `git diff --check` 并核对 staged name set；最终工作区干净，未 push。

## 6. 进程终审

所有 pytest、benchmark、smoke 与 Tauri 实例均按精确命令行、worktree/config、PID/create-time、
端口和 Job Object 识别并清理，没有按进程名广杀。最后收口还发现并清理了 03:38 遗留的
Companion pytest 树以及当次 smoke 树，分别纳入 exact root/descendant 审计；合计释放
`1,855,430,656` bytes private memory，survivor=0。

此前五个 Windows Harness 隔离树分别释放
`1,596,235,776`、`990,089,216`、`919,678,976`、`900,579,328`、
`905,928,704` bytes，全部 survivor=0。真人场景资源释放见
[manual-results.md](./manual-results.md)。

## 7. DoD 逐项结论

- [x] 主要矛盾对应 AC 已单独确认真实达成。
- [x] 原始核心路径已完成整机可用性实测。
- [x] 执行期回炉项（模型语义信号、response quality、S5 failure/replan）已重新通过门禁。
- [x] AC-01～AC-16 全部必须条款有代码、自动化或真人证据。
- [x] 无功能回归；唯一 `cargo fmt` 红项是已记录的 pre-existing baseline drift。
- [x] UI 走真人点击，逻辑走自动化，没有测试策略降级。
- [x] 幂等、重放、竞态、forget、nonce、effect 与外发边界已逐项复审。
- [x] AC ↔ Task ↔ code ↔ testcase ↔ scenario ↔ root run ↔ evidence ↔ terminal 可追溯。
- [x] testcase/结果账本、架构事实源、PROJECT_STATUS 与正式回归套件已同步。
- [x] required 真人场景无 PENDING/PARTIAL/NOT RUN，distinct count=`6/6`。
- [x] worktree、dirty/staged scope、进程 survivor=0 和不 push 约束已审计。

最终判定：**100% COMPLETE，DECISION: SHIP**。
