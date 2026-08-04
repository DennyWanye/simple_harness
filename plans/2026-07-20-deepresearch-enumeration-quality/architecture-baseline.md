# 架构基线：DeepResearch 枚举完整性与业务终态

> 基线 HEAD：`961c7d34`（2026-07-20）
> 验收事实源：[`acceptance.md`](acceptance.md)

## 主要矛盾

v7 已把执行流程简化成“拆题 → 独立子代理 → 有界补救 → 统一综合 → 交付”，但当前质量判定只验证“有正文、有引用、脚注合法”，没有保留用户显式要求的对象数量。因此工作流可以如实完成、部分子方向也可通过，却仍生成没有列出所要求 N 个命名对象的报告；与此同时 UI 只显示引擎 `completed/100%`，未显示报告自己的 `partial`。

## 典型调用链（解剖麻雀）

1. `normalize_handler` 只规范 topic/mode/max questions，并未抽取显式数量交付约束（`backend/deskpet/workflows/definitions/v7/deep_research.py:88`）。v7 已发布且 immutable，所以该语义变更必须进入新 v8，而不是原地修改 v7。
2. `plan_handler` 直接使用通用 `_PLAN_PROMPT` 并接受 2–6 个问题，不保证存在承担 N 项清单的子方向（同文件 `:112`；通用 prompt 在 `backend/deskpet/tools/research_tools.py:674`）。
3. `collect_subagent_research` 每个方向最多两次；`_subreport_quality` 目前只检查正文、citation 与 cite-check，因此“有两篇来源但没列齐十个对象”仍可成为 valid（`research_tools.py:1383`、`:1480`）。
4. `_fanout_synthesize` 统一合并子报告；prompt 要求统一结论和引用，但没有结构化 N 项清单契约（`research_tools.py:750`、`:1328`）。
5. `synth_handler` 的业务状态只看是否有子报告和是否存在 insufficient child，不审计最终枚举数量/逐项引用（`deep_research.py:202`）。
6. `finalize_handler` 把 `business_status` 放进 report/final-assistant intent，但没有写入 `terminal_public`；native engine 生成的 `workflow.final` 因而只有引擎状态（`deep_research.py:291`、`backend/deskpet/workflows/native.py:554`）。
7. terminal projection registry 仅注册 v4/v5，没有 v7 的安全公开业务终态 schema（`backend/deskpet/workflows/terminal_projection.py:355`）。
8. 前端 store 对 v5 的 `delivery_status` 有特例，但 v7 summary 没有业务终态字段；`WorkflowProgressGroup` 的主徽标和 100% 只由引擎 `workflow_status` 驱动（`tauri-app/src/stores/sessionsStore.ts:1020`、`tauri-app/src/components/workflow/WorkflowProgressGroup.tsx:226`）。

## 当前可靠边界

- v7 六节点 immutable graph、2–6 子方向、每方向最多 2 次、sibling failure isolation、durable outbox 和既有 `DeepResearch/` 文件保存链已经通过上一 slice 的自动化与真实 UI 验证；本次复制为 v8 并只增加质量契约，不改写 v7。
- `persist_handler` 对任何有 citations 且有正文的 completed/partial 报告继续保存；`insufficient_evidence` 不发布 report/artifact intent。该行为满足本次兼容目标。
- children snapshot 使用独立 seq 合并，terminal 后仍可接受较新的 children snapshot；新增业务终态必须沿用同样的乱序/终态幂等原则。

## 结构判断

- 数量解析与 Markdown 枚举审计属于 research domain，应放在 `research_tools.py` 的纯函数边界，供 v8 child 和 manager 共用；不应在 React 中重新推断报告质量。
- v8 manager 负责把该 domain audit 转成 `business_status` 和 coverage；native engine 只通过版本化 `terminal_public` projector 公开有界结果，不读取研究私有 state。
- 前端只消费服务端投影的业务终态并呈现，不重复执行质量判断。
- 修复综合仅允许一次且只基于现有子报告/引用；这样保留简化流程，同时让 manager 真正处理可修复的完整性问题。
- 版本边界：新增 `definitions/v8`、schema/version/implementation identity 和默认配置；v1–v7 注册与 checkpoint 语义不变。

## 待闭环不确定项

- 不同 Markdown 写法下的稳定条目识别：用纯函数单测覆盖表格、编号列表、编号标题、脚注定义排除和普通年份误判。
- LLM 是否能在一次有界修复内形成逐项引用的 N 项表：先用 fake LLM 自动化验证控制流，再用 S-1/S-2 真实 UI run 验证实际模型表现。
- 业务终态投影乱序：用 sessions store 的 final-before-children 与重复 final 测试闭环。

## 目录与索引核对

- `ARCHITECTURE/ARCHITECTURE.md` 有 `last-calibrated: 2e71e9a1`，当前 HEAD 至该锚点的改动集中在已完成的 DeepResearch v7/UI/file slice，相关当前事实已写入文档头部与 `ARCHITECTURE/DeepResearch.md`。
- `ARCHITECTURE/index.md` 已包含 DeepResearch/current native workflow 入口。
- 根 `README.md` 已明确指向 `ARCHITECTURE/index.md` 与 `ARCHITECTURE/DeepResearch.md`。
