---
id: TC-HM-12
purpose: Verify bounded TaskScope read views and a traceable display-only digital twin projection
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A6, HM-TO-A7, HM-TO-R3, HM-TO-R5, HM-TO-R7]
tags: [human-memory, digital-twin, knowledge-graph, read-views, display-only]
entrypoint: TaskScope inspector and digital twin view
preconditions:
  - Task 6 exact Memory candidate wheel, SHA-256, source commit, and version are pinned for the SDK lane
  - Package-root public manager/backend evidence, mutation, correction, suppression, close/reopen seed contract is pinned
  - Fresh ignored artifact directory is available
revision: 2
---

# TC-HM-12 rev2 — 六阅读视图与展示型数字孪生体

## 固定输入与边界

- DTO oracle：`fixtures/twin-graph-v1.json` revision 1，SHA-256
  `b79932d076e38dee4f97bca782444751a2c5c8f714771e1d47c99aae3c810f4b`。
- self-check runner：`runners/run_twin_graph_public_consumer.py`，SHA-256
  `c370d6731e9be5254591fdfba163c14a9452a63d0c44468a3ec7555c08850ed2`。
- 唯一 graph read port 名称是 `get_twin_graph_view`。禁止 graph→recall/rank/context conversion，也禁止
  Agent runtime、Recall pipeline 或 Context assembler 把 graph DTO 当 authority。
- 正式 SDK lane 只能从 exact installed wheel 的 package root 取得 public Manager/backend/DTO；必须用公开 evidence、
  mutation、correction、suppression 与 close/reopen API 建立真实 canonical durable state，再通过 Manager/
  `CognitiveMemoryBackend.get_twin_graph_view` 读取。禁止 source checkout、private import、直接 SQL、repository object，
  也禁止把 fixture 的 `canonical_records/relation_rows` 直接传给 graph method。
- Task 6 candidate identity 和 exact public seed contract 尚未冻结时，正式 SDK lane 必须
  `NOT_RUN/BLOCKED`；DTO self-check PASS 不是产品 PASS。

## 前置

- 一个 TaskScope 依次生成 1k/10k/100k canonical events；形成偏好、目标、关系、程序记忆，随后纠正一项并逻辑遗忘一项。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 运行 runner `--self-check`。 | 重算 4 个 graph payload known-answer hash；核对 18 nodes/4 edges、字段/稳定顺序、source refs、status/confidence、correction/forget capability、敏感 canary 和 rebuild byte identity。只标 fixture PASS。 |
| 2 | 用 exact candidate wheel 的 public Manager/backend 与公开 authority API 建立 fixture 指定的 canonical active/contested/inferred、relation、suppressed/superseded/expired/restricted state，再调用 Manager port `get_twin_graph_view`。 | 若 exact identity、公开入口或公开 seed contract 缺失则 `NOT_RUN/BLOCKED`；不得用纯 projection helper、echo、private import/SQL 替代。 |
| 3 | 对 base view 核对 exact node/edge DTO 与 canonical payload SHA-256。 | node type/status/confidence/source refs/correction-forget capability 完整；node/edge 稳定排序；payload hash 可独立重建。 |
| 4 | 经公开 correction authority 创建新 revision，再经公开 forget/suppression authority 逻辑遗忘，close/reopen 后从 canonical state 重建。 | 旧 revision、suppressed/expired/superseded/restricted node 与 incident edge 零展示；新 active revision 可见；rebuild 与当前 ordinary view byte/hash identical。 |
| 5 | 在输入 label/relation/tooltip 放入 SENSITIVE/RESTRICTED/suppressed canary。 | ordinary graph canonical bytes 零 canary；敏感 node 仅允许冻结的最小 redacted label，敏感/restricted/hidden incident edge 不泄露。 |
| 6 | 打开 README/PLAN/STATUS/DECISIONS/RESUME/EVIDENCE 六阅读视图。 | README <=16 KiB、STATUS <=12 KiB、Resume <=24 KiB；超限细节拆到有稳定 ref 的页，canonical facts/evidence refs 不丢。 |
| 7 | 删除物化阅读视图并从 canonical raw/events/state/checkpoints 重建。 | 重建后的语义字段、refs 和 hash oracle 一致；Markdown 不是事实 authority。 |
| 8 | 在 graph UI 关闭/开启、读取前/读取后，对同一 fixed recall/query/tool 运行独立 Host probe。 | RecallDecision bytes/hash、rank order、ContextSnapshot bytes/hash、回答和工具行为完全不变；graph-only marker 零进入。graph callable 自报 `influence_zero` 不构成证据。 |
| 9 | 从 TaskScope/turn 导出 route→invocation→proposal→validation→binding/mutation→recall/projection trace。 | trace 完整、只读、不覆盖 lineage；普通导出遵守 suppression，密封读取需独立 AuditAccessDecision。 |

## 决定性证据

- exact candidate identity、public manager/seed calls、graph DTO bytes/hash、node/edge/evidence refs、canary scan、
  correction/forget/reopen receipts、六视图字节数与 page refs、图谱 UI 截图、独立 Host 前后
  Context/decision/rank/answer/tool diff 和 trace export。原始证据只写 ignored `.local-test-evidence/`。
