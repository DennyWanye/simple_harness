---
id: TC-HM-10
purpose: Verify the five routing outcomes distinguish conversation memory, active work, resume, and new work
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A3, HM-TO-A4, HM-TO-R3, HM-TO-R7, S5A-TO-ROUTE, S5A-TO-RECALL]
tags: [human-memory, taskscope, routing, resume, no-recall]
entrypoint: context_route, task_scope_search, and task_scope_open
revision: 2
---

# TC-HM-10 — 五路分流、任务发现与恢复

## 输入类别

按顺序运行标准术语、自然口语、中英混合/简写、无实现关键词表达：简单改写、询问用户偏好、继续当前任务、恢复久远同名任务、创建多步骤文件任务，并在 active task 中插入无关闲聊。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 输入简单改写请求。 | `direct_standalone`；不建 TaskScope、不查长期记忆、不改变 active cursor。 |
| 2 | 询问或使用用户偏好。 | `memory_standalone`；可类型化召回用户记忆，但不建 TaskScope。 |
| 3 | 在 active TaskScope 中说“接着刚才的做”。 | `continue_active`；使用 exact current Scope/revision，route barrier 后才允许 effect。 |
| 4 | 用自然口语请求继续一个久远、名称相似的任务。 | `task_scope_search` 只返回候选；歧义时向用户确认，确认后用 exact ID `task_scope_open`，搜索命中不直接授权。 |
| 5 | 请求创建新的多步骤文件任务。 | `create_new`；建立可信 Scope/binding 后才执行项目 effect。 |
| 6 | 在 active task 中插入无关闲聊和一个不需记忆的问题。 | 分别 standalone；active cursor 不被惯性污染，no-recall 路径记录 `outcome=no_recall` 且不查询 Memory。 |

## S4 Host 子 lane 边界（non-gating）

- S4 只重跑步骤 4 的 typed Host API 部分：permission-first candidate search 不改 cursor/binding/tool authority，exact ID open 才返回 bounded ResumePackage。
- 五路自然语言分流、主模型 route 质量和 no-recall 语义仍属 S5/S6，不作为 S4 required 成功声明。S4 所需的 search/open 权威断言由 `TC-HM-02` rev2 承载，避免重复计门。

## 决定性证据

- 每轮 route proposal/validation/outcome、search candidates、exact open、active cursor、RecallDecision、Memory 查询计数和首个 effect 时序。
