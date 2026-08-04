# Handoff：优化 plan-test skill 的可靠性与执行效率

> 日期：2026-07-27
> 状态：READY FOR SLICE 1A PLANNING
> 目标仓库：`C:\Users\Administrator\.codex\skill-repos\plan-test-skill`
> 当前分支：`main`
> 当前 HEAD：`5a4ea7cbc25af885f4f9bb9dfa016fceed54d3c8`
> 当前 target repo 状态：clean（本 handoff 生成时只读核对）
> 本文用途：直接交给下一位代理继续优化；不是可以跳过设计直接实现全部 P0/P1 的技术规格

## 0. 给下一位代理的任务

优化 `plan-test`、`plan-bs`、`plan-task`，解决两个已经由真实项目证明的问题：

1. 流程可以在 required testcase 仍是 `PARTIAL / NOT_RUN`、输入与证据不一致时，手写出
   `100% COMPLETE / SHIP`；
2. 一个超大 workflow 可以运行 55 小时以上，却没有可靠记录每个阶段耗时，也没有在正确
   时间测试最关键用户旅程。

目标不是继续增加“请认真检查”的 Markdown 提示，而是把已知硬规则做成：

```text
结构化输入
→ canonical ledger
→ deterministic validator
→ 独立 auditor
→ finalize 重新验证
→ gate receipt
→ 只从有效 receipt 渲染交付报告
```

第一步只为 **Slice 1A** 编写独立技术 plan。不要重新做整轮问题复盘，也不要把 1A～3C
重新打包成一个超大实施计划。

本 handoff 当前授权范围是 **规划 Slice 1A**，不是修改 target repo 代码。用户 review
通过并明确要求执行后，才实施 Slice 1A。

## 1. 开工前必须完整阅读

### 复盘与时间证据

1. `F:\projects\deskpet\plans\2026-07-27-plan-test-quality-retrospective\HANDOFF.md`
2. `F:\projects\deskpet\plans\2026-07-27-plan-test-quality-retrospective\TIMING-AUDIT.md`

### 当前 skill 生产事实

3. `C:\Users\Administrator\.codex\skill-repos\plan-test-skill\README.md`
4. `C:\Users\Administrator\.codex\skill-repos\plan-test-skill\skills\plan-test\SKILL.md`
5. `C:\Users\Administrator\.codex\skill-repos\plan-test-skill\skills\plan-bs\SKILL.md`
6. `C:\Users\Administrator\.codex\skill-repos\plan-test-skill\skills\plan-task\SKILL.md`
7. `skills\plan-test\config.md`
8. `skills\plan-test\phase-A-acceptance.md`
9. `skills\plan-test\phase-0-architecture.md`
10. `skills\plan-test\phase-1-plan.md`
11. `skills\plan-test\phase-2-iterate-plan.md`
12. `skills\plan-test\phase-3-execute.md`
13. `skills\plan-test\phase-4-stage-gate.md`
14. `skills\plan-test\phase-5-testcase.md`
15. `skills\plan-test\phase-final-dod.md`

相对路径 7～15 均以 target repo 为根。

### 历史 dogfood

16. `F:\projects\deskpet\testcase\2026-07-24-human-anchored-companion-growth\manual-test.md`
17. `F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\manual-results.md`
18. `F:\projects\deskpet\plans\2026-07-24-human-anchored-companion-growth\evidence\task16-delivery-audit.md`

不需要首先重扫 342MB 主 rollout。时间审计已经保存可复用统计；只有文档证据无法回答具体
问题时，才按 `TIMING-AUDIT.md` 提供的路径做窄查询。

## 2. 当前 skill 的真实基线

target repo 当前只有 Markdown、插件 manifest 和示例配置：

```text
.claude-plugin/
examples/
skills/plan-bs/SKILL.md
skills/plan-task/SKILL.md
skills/plan-test/
  SKILL.md
  config.md
  phase-*.md
  prompts/
  checklists/
  methods/
```

当前不存在：

- machine-readable run schema；
- canonical ledger producer；
- deterministic validator；
- gate receipt；
- renderer；
- validator/self-test fixtures；
- CI 或唯一 finalize command。

当前规则已经要求：

- required 场景未执行必须 BLOCKED；
- testcase、RESULTS、Gate 状态必须一致；
- UI 必须真人测试；
- 记录阶段耗时；
- 复测按 change-impact 路由。

问题是这些要求只有文字，没有不可绕过的执行协议。

## 3. 安装与仓库边界

`plan-test`、`plan-bs`、`plan-task` 都是 junction，指向：

```text
C:\Users\Administrator\.codex\skill-repos\plan-test-skill\skills\...
```

因此修改 target repo 会立即影响三个可见 skill。不要再去修改
`C:\Users\Administrator\.codex\skills\<name>` 的复制品。

Git 在当前 PowerShell 中可能不在 PATH。可使用：

```text
C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\
dependencies\native\git\cmd\git.exe
```

### 开工时重新验证仓库快照

本文顶部的 HEAD、分支和 clean 状态只是 2026-07-27 的交接快照，不能当成下一次开工时的
当前事实。下一位代理在阅读设计材料后、写入任何文件前，必须在 target repo 重新执行：

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe' rev-parse HEAD
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe' branch --show-current
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe' status --short
```

- HEAD 已变化：先检查新提交，更新计划中的 baseline，不能继续照抄本文快照；
- worktree 不 clean：保留所有既有改动，核对所有权和重叠范围；未能确认安全边界前停止直接
  写入并向用户报告，禁止覆盖、丢弃或夹带未知改动；
- 分支不是 `main`：先说明当前拓扑及原因，不要自行把未知工作切走。

### Slice 1A 规划产物固定位置

下一位代理只在 target repo 创建以下规划目录，不把新 plan 留在 DeskPet：

```text
C:\Users\Administrator\.codex\skill-repos\plan-test-skill\
  plans\2026-07-27-plan-test-gate-slice-1a\
    acceptance.md
    plan.md
    fixture-contract.md
    evidence\
      challenger.md
```

DeskPet 只作为 dogfood/evidence source。Slice 1A～1D 不修改 DeskPet 产品代码，也不要把
DeskPet 当前 dirty worktree 的并行改动带入 target repo 提交。

未经用户明确要求，不 push target repo。

## 4. 已确认、不要重新争论的结论

### 4.1 不是“测试数量太少”

Companion 计划运行了大量自动化和真人 E2E，但仍然漏掉明显问题，因为：

- acceptance 把“一个主消息入口”错误扩张成“只能有一个持久 Session”；
- 原 UUID testcase 被同步改成验证 same-session；
- 真人 E2E 点击了“新话题”，却只断言最终回答和最终空闲；
- fresh user-data 没覆盖历史 Session、旧 localStorage 和失败 Run；
- 快速 provider 没跨越 300 秒 TTL；
- 最终审计没有机器核对 frozen input、结果、证据路径和状态。

### 4.2 已存在直接矛盾证据

同一次交付中：

```text
manual-test.md = PARTIAL / BLOCKED / NOT_RUN
manual-results.md = 6/6 PASS
task16-delivery-audit.md = 100% COMPLETE / SHIP
```

S-2 还存在：

- 冻结第四话题输入与 PASS 结果不是同一内容；
- 冻结 evidence path 与最终引用路径不同；
- `manual-results.md` 与 `task16-delivery-audit.md` 相互引用形成循环证据。

### 4.3 运行时间也不可审计

Companion plan-task：

```text
wall clock = 55.51h
terminal-recorded active = 35.39h
active range = 35.39～38.41h
21 task starts / 13 complete / 7 aborted / 1 missing terminal
82 context compactions
```

现有流程要求“记录耗时”，但没有统一 producer，所以事后只能从 rollout、Git 和文件证据
交叉推算。

## 5. 目标架构：保持简单

不要建立第二套 Harness，也不要为 acceptance、testcase、results、auditor 分别维护可写
状态。最小目标只有：

```text
skills/plan-test/schemas/plan-test-run.schema.json
skills/plan-test/scripts/plan_test_gate.py
```

每次 workflow 使用固定目录：

```text
<plan-folder>/verification/<run-id>/
  plan-test-run.json
  artifacts/
  auditor-input.json
  auditor-output.json
  gate-receipt.json
  report.md
```

其中：

- `plan-test-run.json` 是唯一交付状态 authority；
- artifacts、auditor input/output 是不可变输入或证据；
- `gate-receipt.json` 是 finalize 的派生凭证；
- `report.md` 只是人读视图；
- 所有 status/projection 必须由 validator 从 facts 重新计算。

## 6. Canonical CLI

预期命令形态：

```text
init
record-run
attach-evidence
audit
finalize --check-only
finalize
render
invalidate
```

阶段语义：

1. `finalize --check-only`
   - 检查除 auditor/receipt 外的输入和测试完整性；
   - 成功只输出 `READY_FOR_AUDIT`。
2. `finalize`
   - 要求 auditor PASS；
   - 重新验证全部 hash、HEAD、dirty、runtime；
   - 成功才生成 receipt。
3. `render`
   - 重新运行相同 validator；
   - 重算全部输入 hash 和 receipt digest；
   - receipt 无效时不得输出 `SHIPPABLE`。

`plan-task` 最终结论只能采用 canonical finalize 的 exit code 和结构化 stdout。手写
`SHIP` 没有有效 receipt 时必须失败。

## 7. 必须稳定输出的错误码

基础错误码至少包括：

```text
REQUIRED_SCENARIO_NOT_RUN
STATUS_CONFLICT
DELIVERY_VERDICT_CONTRADICTS_LEDGER
EVIDENCE_HASH_MISMATCH
EVIDENCE_DEPENDENCY_CYCLE
TESTED_RUNTIME_MISMATCH
RECEIPT_STALE
BEHAVIOR_APPROVAL_REQUIRED
FROZEN_ORACLE_CHANGED
AUDITOR_INPUT_STALE
RISK_CLOSURE_MISSING
RELEASE_UNIT_TOO_LARGE
```

同一 fixture 重跑必须得到相同、有序 diagnostic，不允许只返回模糊的
`MANIFEST_MISSING`。

## 8. Program 切片顺序

### Slice 1A：schema、run-dir 与 fixture contract

只定义：

- schema version；
- normative run directory；
- facts 与 derived projection；
- required AC/scenario；
- primary/derived evidence；
- diagnostic contract；
- revision/CAS 与并发写入规则；
- timing facts 的字段、producer、authority、单位与时钟语义；
- legacy normalized fixture；
- 最小 PASS/FAIL fixture。

Slice 1A 不宣称已经有可信 SHIP gate。

### Slice 1B：ledger CLI 与 deterministic validator

- `init/record-run/attach-evidence/audit`；
- required rows 自动初始化为 `NOT_RUN`；
- status 完全重算；
- 文件锁、CAS、原子 replace；
- hash、状态冲突和 evidence dependency 检查。

### Slice 1C：renderer、receipt 与 stale

- `finalize/render/invalidate`；
- receipt digest；
- auditor/HEAD/dirty/policy/runtime/hash 绑定；
- verification 生成物排除规则；
- stale detection；
- 幂等 content digest。

### Slice 1D：skill/plan-task 接入与 dogfood

- 最终步骤强制 canonical command；
- self-test/CI 使用同一路径；
- legacy Companion fixture 必须得到三个具体错误码；
- 一份小型合法 fixture 必须 PASS。

### Slice 2A：behavior contract 与批准 artifact

- 原始用户 message/event/hash；
- glossary 与实体关系；
- before/after；
- 保留、删除、改变；
- exact scope/expiry 的批准。

### Slice 2B：testcase lock 与 mutation validator

- frozen black-box oracle；
- 任意 byte 变化默认 FAIL；
- exact old/new approval；
- repo internal test mutation 只能作报告，不能替代 frozen oracle。

### Slice 3A：runtime adapter protocol

```text
project attest command
→ components[]
→ source root
→ process command/env
→ loaded artifact/source digest
→ health/build identity
```

adapter 缺失或 identity 为 UNKNOWN 时，真人 E2E 必须 BLOCKED。

### Slice 3B：project policy 与 change-impact

- 项目路径到核心旅程的映射；
- risk manifest；
- required lanes、edges、sequences、sample budget。

### Slice 3C：lane execution 与 closure delta

- fresh；
- history/upgrade；
- temporal/fault；
- exploratory；
- risk-based closure；
- audit 后最小必要复测。

## 9. Slice 1A 必须回答的设计问题

下一位代理在提交 Slice 1A plan 前，必须明确：

1. schema 的 producer、consumer 和 version migration；
2. ledger 是单文件 facts + projection，还是内部 event list；
3. 哪些字段调用者可写，哪些只能由 validator 派生；
4. 文件锁、原子 replace、revision/CAS 如何实现；
5. primary evidence 与 derived report 如何区分；
6. required AC/scenario 从哪里导入；
7. legacy documents 如何规范化且保留 provenance/hash；
8. diagnostic 的排序、结构化 stdout 和 exit code；
9. Windows/macOS/Linux 路径规范；
10. 不新增第三方依赖时是否足够；若需要依赖，如何安装与版本锁定；
11. 如何确保当前三个入口 skill 兼容；
12. timing contract 如何表示 phase/slice/task、RFC 3339 UTC 起止时间、单调时钟测得的
    `elapsed_ms`、activity class、wait reason、retry 和 abort；哪些值由 1B 的 canonical
    CLI 产生，哪些值允许调用者声明；
13. 如何回滚 Slice 1A。

这些问题不能留到编码时由执行者临场决定。

## 10. Fixture 与 dogfood 的阶段边界

### Slice 1A 当前只冻结 contract

Slice 1A 只定义两份 normalized fixture 的数据格式、来源和 expected diagnostics：

1. 一份最小 PASS fixture，用来证明 schema 能表达 required rows、primary evidence、
   auditor、HEAD/runtime identity 和预期 receipt 输入，但本 slice 不执行 finalize；
2. 一份由 Companion 历史材料规范化得到的 FAIL fixture，必须冻结：
   - 原始来源路径；
   - 每个来源文件的 hash；
   - normalized representation；
   - 以下有序 diagnostics：

```text
REQUIRED_SCENARIO_NOT_RUN
STATUS_CONFLICT
DELIVERY_VERDICT_CONTRADICTS_LEDGER
```

Slice 1A 不实现 importer、validator、finalize 或 renderer，也不声称已经实际运行了
dogfood。不能为了让历史计划变绿而修改历史证据。

### 后续 slice fixture backlog

| 待验证场景 | 归属 slice |
|---|---|
| required scenario=`NOT_RUN` | 1B |
| evidence 缺失、hash 不符、dependency cycle | 1B |
| input 与 result 不是同一 testcase | 1B |
| full-audit 后修改结果、audit 后新增 production commit | 1C |
| frozen oracle 变化但没有批准 | 2B |
| wrong worktree / old binary attestation | 3A |
| required history/upgrade lane 未执行 | 3B / 3C |
| 非确定性场景 1/3 成功却声称 PASS | 3B / 3C |

这些场景在 Slice 1A 只登记格式需求和 owner，不得伪装成已经实现、已经执行或已经 PASS。

### 真实 dogfood 属于 Slice 1D

Slice 1D 才实现一次性 importer/adapter，并对冻结的 Companion 历史材料运行 canonical
validator，断言得到上面三个具体错误码，而不是模糊的 `MANIFEST_MISSING`。同一 slice
再执行一份小型合法 workflow，验证 finalize 幂等、receipt digest 稳定、render 只从有效
receipt 输出 `SHIPPABLE`。

## 11. 时间与成本必须成为一等证据

Slice 1A 必须冻结 timing fact contract；Slice 1B 才实现采集。contract 至少包括：

```text
phase / slice / task
command / tool
started_at / ended_at
elapsed_ms
activity_class
wait_reason
retry / abort
test count
runtime identity
evidence hash
```

`started_at` / `ended_at` 使用 RFC 3339 UTC，供人类和跨进程关联；`elapsed_ms` 是非负整数，
必须由执行进程的 monotonic clock 测量，不能靠两个 wall-clock 时间相减。1B 的 canonical
CLI 是 timing fact producer，canonical ledger 是 authority；调用者只可声明
phase/slice/task、activity class 和受控 wait reason，不能覆写测得的起止时间或耗时。

最终报告必须区分：

- implementation；
- automated test；
- manual E2E；
- provider/tool wait；
- user/quota wait；
- interruption recovery；
- rework。

建议把“连续 90～120 分钟写 checkpoint”作为可观测目标。checkpoint 至少包含 HEAD、dirty、
当前 slice、活动进程、测试状态、证据和下一动作。

## 12. 明确禁止

- 不要只增加新的 prompt/challenger，然后宣称问题已解决；
- 不要允许代理在任务末尾回忆着手写一份 PASS JSON；
- 不要维护多个可独立写状态的 manifest；
- 不要让 renderer 只相信现存 receipt；
- 不要用 internal unit test 的新期望替换 frozen black-box oracle；
- 不要把 fresh lane PASS 当成 history/upgrade PASS；
- 不要把一次 retry 成功覆盖前面的无解释失败；
- 不要在 Slice 1 修改 DeskPet 产品代码；
- 不要把 1A～3C 合成一个新的超大 plan；
- 不要未经用户要求 push。

## 13. Slice 1A 的完成标准

Slice 1A plan 只有满足以下条件才可定稿：

- 范围只覆盖 schema/run-dir/fixture contract；
- 规划产物位于 target repo 固定的
  `plans/2026-07-27-plan-test-gate-slice-1a/`；
- 每个字段都有 producer、consumer 和 authority；
- 并发、原子写入、versioning、migration 有明确方案；
- timing fact 的字段、producer、authority、单位和时钟语义已冻结，采集实现仍归 1B；
- 最小 PASS fixture 与 Companion normalized FAIL fixture 的格式及 expected diagnostics
  已冻结；
- Companion fixture 保留来源路径和 hash；
- 其他 fixture 只进入 backlog，并明确归属 slice；
- 明确 1B 的接口，但不提前实现 1B；
- 明确 importer 与真实 dogfood 执行归 1D，不在 1A 伪造运行结果；
- 有独立 challenger `VERDICT: PASS`；
- 用户 review 后才进入实现。

## 14. 给下一位代理的直接指令

```text
请优化本机 plan-test skill。

目标仓库：
C:\Users\Administrator\.codex\skill-repos\plan-test-skill

执行前完整阅读：
1. F:\projects\deskpet\plans\2026-07-27-plan-test-quality-retrospective\OPTIMIZATION-HANDOFF.md
2. F:\projects\deskpet\plans\2026-07-27-plan-test-quality-retrospective\HANDOFF.md
3. F:\projects\deskpet\plans\2026-07-27-plan-test-quality-retrospective\TIMING-AUDIT.md
4. target repo 的 README、三个 SKILL.md、config.md 和全部 phase 文档。

写入前重新执行 rev-parse HEAD、branch --show-current 和 status --short。HEAD 漂移时先检查
新提交并更新 baseline；worktree dirty 时保护未知改动，在确认安全边界前停止直接写入。

不要重新做整轮复盘。当前只为 Slice 1A：
“schema、run-dir 与 fixture contract”
编写一份小型、代码级可执行 plan，并做独立 challenger 审计。

规划产物固定写入：
C:\Users\Administrator\.codex\skill-repos\plan-test-skill\plans\2026-07-27-plan-test-gate-slice-1a\
其中包含 acceptance.md、plan.md、fixture-contract.md 和 evidence\challenger.md。

用户 review 前不要实现任何代码；后续获得执行授权时也只能先实现 Slice 1A。
不要实现 1B～3C，不要修改 DeskPet 产品代码，不要 push。
计划必须关闭 OPTIMIZATION-HANDOFF.md 第 9、13 节；第 10 节在 1A 只冻结最小 PASS 与
Companion normalized FAIL fixture 的来源 hash、normalized representation 和 expected
diagnostics，其他 fixture 只登记后续 slice owner。真实 importer 和实际 dogfood 执行归
Slice 1D，不能提前声称完成。
完成后把 plan、acceptance、fixture contract 和 challenger 结果交给用户 review。
```

## 15. 交付说明

本 handoff 已把：

- 质量根因；
- 时间根因；
- target repo 事实；
- 简单目标架构；
- program slices；
- 第一 slice 边界；
- dogfood；
- 测试和禁止项

集中到一份可转交文档。下一位代理应从 Slice 1A 规划开始，而不是再次讨论“是不是应该多写
几个测试提示词”。
