# 挑战第 1 轮 · synthesis（2026-09-09）

> Primary breadth challenger（Opus 5）输出 12 条 findings、5 个 cluster；原始 JSON 见 `challenge-round-1-primary.json`。主 agent 对每条 finding 的关键事实做了独立复核（见「复核」列），据此裁决。记账方式：LEAN（无机器账本）。

## 复核与裁决

| ID | 级别 | 复核结果 | 裁决 | 落到哪 |
|---|---|---|---|---|
| primary-contradiction-misidentified | P0 | 复核成立：交付版 `execution-v6.sqlite3` 的 `provider_invocations` 43 条请求，`workflow_spawn` 0 次、catalog 提示词 0 次，tools 名字集合 14 个不含它 | 采纳。主要矛盾重述为「删掉 `workflow_spawn` 的定义、清单条目与提示词之后，主流程还能不能原样跑通」，主要方面 = 清单五处守卫同步 + 不误伤 ReAct 机制；最小验证动作改为「守卫自检 + 冷启动 + 一轮 COMPLETED + provider 请求断言」（秒级到分钟级），10 轮冒烟保留为 AC-1 的决定性深测而不是里程碑 | plan 矛盾分析、Task 5 拆成 5a（里程碑）/ 5b（深测）；acceptance 矛盾分析 |
| ac2-runtime-oracle-unsatisfiable | P0 | 复核成立：`operation-audit.db` 的 130 次命中全是五张 SDK 表名（`workflow_spawn_*`），裸工具名 0 次；`execution-v6.sqlite3` 命中为 checkpoint 字段名 | 采纳。AC-2③ 改为对 `provider_invocations.request_json` 的断言：tools 数组名字集合不含 `workflow_spawn`、请求体不含 catalog 提示词；明写这是 before/after 皆 0 的不回归断言，鉴别力来自 AC-2①②④ 的静态断言；`grep -a` 类锚点全部删除 | acceptance AC-2；plan Task 5a oracle |
| product-tool-names-count-wrong | P1 | 复核成立：`PRODUCT_TOOL_NAMES` 84 = 清单 77 + `HOST_COMPOSED_TOOL_NAMES` 7 | 采纳。去名后 83；断言写成 `set(P) == set(manifest.tool_names) \| set(HOST_COMPOSED_TOOL_NAMES)` | plan Task 2；acceptance AC-6 |
| exclusive-async-mechanism-test-gutted | P1 | 复核成立：`tests/test_deskpet_agent_loop.py:216-220` 的三元条件按名字分发 | 采纳。参数化改 `["capability_build", "tool_activate"]`，条件改 `exclusive_name == "capability_build"`；机制覆盖不变 | plan Task 6 |
| task6-test-site-drift | P1 | 复核成立：74 在 `:283` 与 `:296` 成对；`:183`；`:446`；第二个函数名 `test_each_of_64_...` | 采纳，逐处改写 Task 6 | plan Task 6 |
| residual-grep-whitelist-contradicts-do-not-touch | P1 | 复核成立：`turn_preparer.py:543` 注释、`assembler/components/skill.py:41` 注释、`harness/profiles.py:5` docstring | 采纳。三处注释/docstring 改措辞（注释不是行为，不违反「deny_selectors 不动」；只改注释行，不动代码）；三个文件进影响清单 | plan 文件影响清单、Task 2 |
| fail3-not-exercisable-by-available-userdata | P1 | 复核成立：交付版 userdata 无 ticket 行、无含该名字的快照 | 采纳。FAIL-3 收窄为「旧 userdata 重启失败或主对话不就绪」；删掉 plan 数据流小节里「旧快照含 workflow_spawn 名字」的错误前提；旧 userdata 段保留为「启动兼容」取证并如实标注它证明不了 ticket 行场景 | acceptance 非功能/边界、assurance；plan 数据流 |
| reserved-name-removal-is-a-loosening | P2 | 复核成立：`CORE_RESERVED_TOOL_NAMES`、两处 `_SKILL_SCOPE_WIDENING_CONTROLS` 是拒绝集 | 采纳（与 deny_selectors 同一原则）：**三个拒绝集本轮不动**，与 F-WF-1 一并在编排大改时决定；`test_skill_runtime_snapshot.py` 随之无需改 | plan 文件影响清单、Task 2；acceptance 范围与 AC-4 白名单 |
| manifest-resign-roundtrip-newline | P2 | 复核成立：`dumps(indent=2, ensure_ascii=False) + "\n"` 与原文逐字节相同；hash 前必须先 pop `manifest_sha256` | 采纳。Task 1 步骤固化：pop → 改内容 → `canonical_hash` → 以 `manifest_sha256` 为首键重建 dict → dump + 换行；「等式成立说明其他内容没动」措辞修正 | plan Task 1 |
| ac7-baseline-scope-gap | P2 | 复核：三个门外文件基线实测 24 passed | 采纳。baseline.md 扩到七文件（2 failed / 89 passed）；四个相对断言型测试文件记为「已核不入门」 | baseline.md；acceptance AC-7 |
| smoke-known-deviations-not-carried-into-oracle | P2 | 复核：SMOKE 记录明写 T16 不落盘、T22 幻答两条已知偏差 | 采纳。Task 5b oracle 登记两条已知偏差为期望值 | plan Task 5b |
| ac5-sdk-status-oracle-ambiguous | P2 | 复核：SDK 仓起点 ` M .gitignore` | 采纳。AC-5 改为相对 baseline 的断言 | acceptance AC-5 |

## Cluster 处置

- `cluster-value-oracle-discriminating-power`（required）：所需证据主 agent 已亲自取得（provider_invocations 结构与 43 条统计、词形拆解、旧 userdata 触发条件、SMOKE 已知偏差），裁决已落到 plan/acceptance；**不再派 specialist**，理由：问题边界已被原始证据完全闭合，剩余是措辞修订，由 closure diff review 复核。
- `cluster-name-set-semantics-and-counts`（required）：七个集合逐一定性——登记/暴露集：`PRODUCT_TOOL_NAMES`、`SDK_DIRECT_TOOL_KERNEL`、`_CONTROL_TOOLS`、`core_names`（去名 = 删除）；拒绝集：`CORE_RESERVED_TOOL_NAMES`、`_SKILL_SCOPE_WIDENING_CONTROLS` ×2（本轮不动）。基数：清单 77→76，`PRODUCT_TOOL_NAMES` 84→83，`SDK_DIRECT_TOOL_KERNEL` 20→19（closure 轮实跑纠正，第 1 轮 synthesis 误记 24→23）。不派 specialist，closure 复核。
- `cluster-test-realignment-fidelity`（required）：Task 6 已按真实行号重写；不派 specialist，closure 复核 + Task 6 实跑即证。
- `cluster-residual-surface-boundary`（required）：裁决「注释/docstring 改措辞进影响清单」；不派 specialist。
- `cluster-manifest-resign-mechanics`（not required）：证据齐全，Task 1 固化。

> 跳过四个 required specialist 的留痕理由：每个 cluster 的 `required_evidence` 都已由主 agent 用真实命令取得并写入本文；specialist 能增加的只是对同一证据的再叙述。若 closure 轮发现新的结构根因，再补派。
