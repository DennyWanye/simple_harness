你是实施者。切片 **H1-G：适配层**（基于提交 5804dc3 = 主干 + 尚在核验中的准入检查；其中 `planning/decision_admission.py` 的 `AdmittedPlanningDecision` 是你的输入类型，**不要改它**；若它之后因核验有小改，编排者负责变基）。权威规格（冲突时后者优先）：V2 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/plans/taskSys2/升级planV1/v1.4/simpleharness-llm-native-htn-execution-plan-v2.zh-CN.md` 与补遗 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/plans/taskSys2/升级planV1/v1.4/LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md`（含末尾追加裁定）。源码定位参考 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/plans/taskSys2/升级planV1/v1.4/H1-V2对照源码冲突检查-2026-09-18.zh-CN.md`（§1 符号对照表）。线上字段名、枚举值、版本号不得自行改动；规格没覆盖的新问题写 `plans/llm-native-htn/H1/BLOCKER-<片名>.md` 并停在那一点。 重点读 V2 第 24–30、44 节，以及现网的旧提案类型与解析（`contracts/htn.py` 里的 `PlanProposal` / `RefineOperation` / retire / `bind_shared_goal` / `propose_successor` 等操作、`planning/htn/planner.py` 的 `parse_plan_proposal` 产物形状）。不 push；只改白名单内文件；测试一律 `PYTHONPATH=src uv run --offline pytest … -q -p no:cacheprovider`；**不要编造数字，测试尾行与 git 输出原样粘贴**；测试先行（先写会失败的测试并提交，再实现）；工作树最终必须干净（全部 commit，含 journal）。最终回复用「## 结果」开头。
只允许新增 `src/agent_orchestrator/planning/decision_adapter.py`、`tests/orchestrator/full_target/test_planning_decision_adapter.py`、`plans/llm-native-htn/H1/journal-G.md`。不改 contracts/、planner.py、event_handler.py、hierarchical_dispatch.py、存储层。
**要做的东西：一个纯函数模块**，把「已通过准入的决定」翻译成现有主链能直接消费的东西（V2 第 44 节）：
- `adapt_admitted_decision(admitted, *, context) -> AdaptedPlanningOutcome`（frozen）。`AdaptedPlanningOutcome` 二选一：`proposal`（一个**现网原样的** `PlanProposal`，后续照旧走 ground → compile → commit，H1 不换编译器）或 `durable_only`（只落库、不改计划：WAIT / NO_CHANGE；以及 DECLARE_BLOCKED 交给现网"无可用方法/停滞"逻辑所需的最小信号，按第 28、44 节）。
- 映射表严格按第 44 节：展开 → 现有展开操作；修复/替换方法 → 退役该方法实例 + 新展开 + 「先停再对账」标记；修复/提后继 → 现有提后继操作；绑定已有目标 → 现有共享目标绑定（两种模式按第 27 节）；声明受阻 → 现网逻辑；等待/不改 → 仅持久化。
- `expected_plan_revision` 等旧提案需要、而模型不再提交的系统字段，一律由适配层从 `context`（请求绑定的 `base_plan_revision` 等）填入；**模型给的任何值都不得流入系统字段**。
- 不直接执行 SQL、不发事件、不调用编译/提交。
- **等价性是本片的核心验收**：对每一种可执行类型，构造「旧协议下模型会写的 `<plan_revision_proposal>` 文本」与「新协议下等价的决定」，断言 `parse_plan_proposal(旧文本)` 得到的提案与 `adapt(...)` 得到的提案**规范 JSON 逐字节相同**（或列出有意的差异并逐项说明理由）。
测试另须覆盖：每种类型正例；系统字段只来自 context（给决定里塞同名值也不生效——这在准入层已拒，这里再钉一层）；修复类必须带「先停再对账」；durable_only 不产生提案；对未启用类型直接抛编程错误（准入层不该放行）。
完成标准：新测试全绿；`tests/orchestrator/full_target` 全绿；ruff 无告警；commit `feat(h1-g): adapter from admitted planning decisions to the existing proposal chain`。

---

**接手说明（编排者 2026-09-19 16:15 追加，务必先读）**：本分支已有你上一轮的实现（2 个提交），并已**变基到新主干**。上一轮独立核验判「不可合」，两个阻塞问题（共享绑定提案、提后继提案都过不了现网编译器）**已由计划裁定解决，不用你改代码去迁就编译器**：

- 裁定原文见 `plans/taskSys2/升级planV1/v1.4/LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md` 末尾「追加裁定（2026-09-19 15:20）」。
- 结论：`BIND_EXISTING_GOAL` 与 `REPAIR/PROPOSE_SUCCESSOR` 在本阶段**降为只解码不执行**，准入层已经会回 `DECISION_TYPE_NOT_ENABLED`（这一改动已在新主干里）。

因此本轮你要做的是：
1. 先 `git log --oneline` 与 `git diff <主干>..HEAD` 回看自己上一轮的实现。
2. **删掉适配层里这两种类型的映射与相关测试**，并加一条断言：若准入层因故放行了这两种类型，适配层必须抛编程错误（不得静默产出提案）。V2 第 44 节这两行本阶段不生效。
3. 修上一轮核验的两条必修项：
   - `WAIT` 的 durable 信号缺少有鉴别力的断言（把 `wait_for` 改成空元组时测试仍全绿）——补断言使其与决定载荷逐项相等。
   - durable-only 结果没有携带 `canonical_hash`，而输入里明明有——补上并加断言，否则后续持久化接线拿不到它。
4. 等价性验收照旧：对**仍然可执行**的类型（展开、修复/替换方法、声明受阻、等待、不改），断言旧写法解析出的提案与新决定翻译出的提案规范 JSON 逐字节相同。
5. 更新 `plans/llm-native-htn/H1/journal-G.md`：列出上一轮提交、本轮删改内容、裁定的影响。
全部提交，工作树保持干净。
