你是实施者。切片 **H1-T：按裁定把「绑定已有目标」与「提出后继任务」降为只解码**（基于 main e8fbafc）。
**先读裁定全文**：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/plans/taskSys2/升级planV1/v1.4/LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md` 末尾「追加裁定（2026-09-19 15:20）」。证据见 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/plans/taskSys2/升级planV1/v1.4/核验留档-2026-09-19/`（独立事实调查 + H1-G 核验报告 P0-1/P0-2）。计划正文 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/plans/taskSys2/升级planV1/v1.4/simpleharness-llm-native-htn-execution-plan-v2.zh-CN.md` 第 12、24–31、44 节的相关行**以该裁定为准**。
不 push；只改白名单内文件；测试一律 `PYTHONPATH=src uv run --offline pytest … -q -p no:cacheprovider`；**不要编造数字，测试尾行与 git 输出原样粘贴**；测试先行；工作树最终必须干净（全部 commit）。最终回复用「## 结果」开头。
要做的：
1. `contracts/planning_decisions.py` 的 `H1_DECISION_ENABLEMENT`：`BIND_EXISTING_GOAL` 与 `REPAIR/PROPOSE_SUCCESSOR` 由可执行改为只解码。**其余条目不动。**
2. 准入检查 `planning/decision_admission.py`：这两种决定现在应在「类型是否启用」那一步回 `DECISION_TYPE_NOT_ENABLED`，且**在该阶段之前的检查仍照常先做**（顺序不变）。相应更新既有用例。
3. 提示词第 8 版 `runtime/role_templates.py`：若文本里教了模型用这两种决定（含示例、类型清单），删掉相关内容，其余文字**尽量不动**；重新登记冻结指纹；同步 `plans/llm-native-htn/H1/prompt-v8.md` 使其与代码逐字一致。若文本本来就没提，写进 journal 说明「无需改动」并给出检索证据。
4. 请求包的 `enabled_decision_types` 由常量派生，应自动跟随；用测试钉死它不再包含这两种类型。
5. 格式文件与样例**不改**：这两种决定仍必须能被解码、能往返、能持久化。若某个反例的期望拒绝码因本次改动而改变（例如原本期望准入层别的码，现在应为 `DECISION_TYPE_NOT_ENABLED`），只改那个 `.expect.json`，并在 journal 逐个列出改了哪些、为什么。
测试至少覆盖：两种类型在准入层被拒且拒绝码正确；解码仍成功、往返一致；请求包启用清单不含它们；提示词不再诱导它们；其余 8 种类型的启用状态一条不变（逐条断言）。
完成标准：全部相关测试全绿；`tests/orchestrator/full_target` 与旧模式回归全绿；ruff 无告警；commit `feat(h1-t): demote bind-existing-goal and propose-successor to decode-only per the 2026-09-19 ruling`。
