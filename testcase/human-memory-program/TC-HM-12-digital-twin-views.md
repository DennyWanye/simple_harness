---
id: TC-HM-12
purpose: Verify bounded TaskScope read views and a traceable display-only digital twin projection
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A2, HM-TO-A6, HM-TO-A7, HM-TO-A8, HM-TO-R3, HM-TO-R5, HM-TO-R7, HM-TO-R9]
tags: [human-memory, digital-twin, knowledge-graph, read-views, display-only]
entrypoint: TaskScope inspector and digital twin view
preconditions:
  - Task 6 exact Memory candidate wheel, SHA-256, source commit, and version are pinned for the SDK lane
  - Package-root public manager/backend evidence, mutation, correction, suppression, close/reopen seed contract is pinned
  - Fresh ignored artifact directory is available
revision: 3
---

# TC-HM-12 rev3 — 六阅读视图、展示型数字孪生体与一等语义关系

## 固定输入与边界

- DTO oracle：`fixtures/twin-graph-v1.json` revision 1，SHA-256
  `02c7e7e306cdccf1cb7e7a6d792b0e7e39a9bf4161db4eeaade7a4f70deaed61`。
- self-check runner：`runners/run_twin_graph_public_consumer.py`，SHA-256
  `c797ce8d87e0738824adada8ed6286155b23b55b6bcbdd2db520104fa3050ae3`。
- relation oracle：`fixtures/semantic-relations-v1.json` revision 1，SHA-256
  `f4aab67c16fee324a6a257b6ede409be02d3477246025d57cb17f4bc6fda0f02`。它在生产实现前冻结 Harness v5 三 operation wire、双 wheel package-root 边界、
  2 nodes + 1 `applies_to` edge、relation node zero、endpoint suppression 与 close/reopen 0 edge。
- `twin-graph-v1.json` 是 rev1 projection regression 数据，包含的历史 edge labels 与 `mem-rel` 普通 node 不是 v5
  relation-memory write contract；v5 knowledge relation kind 只以 relation oracle 的 `applies_to` 为准。
- Memory candidate 固定为 `simple-harness-memory-sdk==0.6.0`、source commit
  `8cdc103c01335849462779f1cf95e86fccb445f7`、wheel SHA-256
  `723bcbc36847f058f3f50c634f008ba12c399a3547b7bb3c2ba7eecf4d8bd182`；两次独立 build byte-identical。
- 唯一 graph read port 名称是 `get_twin_graph_view`。禁止 graph→recall/rank/context conversion，也禁止
  Agent runtime、Recall pipeline 或 Context assembler 把 graph DTO 当 authority。
- 正式 SDK lane 只能从 exact installed wheel 的 package root 取得 public Manager/backend/DTO；必须用公开 evidence、
  mutation、correction、suppression 与 close/reopen API 建立真实 canonical durable state，再通过 Manager/
  `CognitiveMemoryBackend.get_twin_graph_view` 读取。禁止 source checkout、private import、直接 SQL、repository object，
  也禁止把 fixture 的 `canonical_records/relation_rows` 直接传给 graph method。
- Task 6 candidate identity 已冻结；exact public seed contract 仍为
  `PENDING_TASK6_PUBLIC_SEED_API_PIN`。formal runner 必须先核对 wheel bytes/metadata/source pin，再以
  `exact package-root public manager/backend seed contract is not pinned` 返回 `NOT_RUN/BLOCKED`；DTO self-check
  PASS 不是产品 PASS。
- Semantic relation candidate identity 在本 revision 冻结时刻必须是 `PENDING_POST_BUILD_PIN`；Task 0 只冻结行为、DTO、
  transition 和 public choreography。构建后的 Harness/Memory wheel bytes、versions、source commits 与 reproducible hashes
  在单独 repin 阶段填入，不得改写 relation oracle 语义。

## 冻结命令

```bash
python3 testcase/human-memory-program/runners/run_twin_graph_public_consumer.py --self-check
```

```bash
python3 testcase/human-memory-program/runners/run_twin_graph_public_consumer.py \
  --harness-wheel <exact-harness-wheel> \
  --harness-wheel-sha256 <exact-harness-sha256> \
  --harness-source-commit <exact-harness-commit> \
  --memory-wheel /tmp/simple-harness-memory-task6-wheel-one.gVYHGH/simple_harness_memory_sdk-0.6.0-py3-none-any.whl \
  --memory-wheel-sha256 723bcbc36847f058f3f50c634f008ba12c399a3547b7bb3c2ba7eecf4d8bd182 \
  --memory-source-commit 8cdc103c01335849462779f1cf95e86fccb445f7 \
  --manager-entrypoint <pinned-public-only-adapter.py> \
  --artifact-dir .local-test-evidence/<date>/<run>
```

## 前置

- 一个 TaskScope 依次生成 1k/10k/100k canonical events；形成偏好、目标、关系、程序记忆，随后纠正一项并逻辑遗忘一项。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 运行 runner `--self-check`。 | 重算 4 个 graph payload known-answer hash；核对 18 nodes/4 edges、字段/稳定顺序、source refs、status/confidence、correction/forget capability、敏感 canary 和 rebuild byte identity。只标 fixture PASS。 |
| 2 | 在 clean venv 安装 exact Harness/Memory wheels，只从两个 package root 取得公开协议、Manager/backend/evidence API；提交 relation fixture 冻结的 preference CREATE + procedure CREATE + `applies_to` relation CREATE strict-atomic plan。 | 未 post-build pin 或任一 required public symbol 缺失则 `NOT_RUN/BLOCKED`；成功时一个 receipt 原子覆盖三个 operations，禁止 source checkout/private import/SQL。 |
| 2a | 调用公开 `get_twin_graph_view`，从 node DTO 取得一个 endpoint exact ref，再走公开授权 mutation SUPPRESS；close/reopen 同一 durable DB 后重读。 | create 后 exact 2 nodes + 1 directed `applies_to` edge，relation memory 不作为 node；suppression 后 0 edge，reopen 后仍 0 edge且 payload byte/hash 与 suppression 后一致。 |
| 3 | 对 base view 核对 exact node/edge DTO 与 canonical payload SHA-256。 | node type/status/confidence/source refs/correction-forget capability 完整；node/edge 稳定排序；payload hash 可独立重建。 |
| 4 | 经公开 correction authority 创建新 revision，再经公开 forget/suppression authority 逻辑遗忘，close/reopen 后从 canonical state 重建。 | 旧 revision、suppressed/expired/superseded/restricted node 与 incident edge 零展示；新 active revision 可见；rebuild 与当前 ordinary view byte/hash identical。 |
| 5 | 在输入 label/relation/tooltip 放入 SENSITIVE/RESTRICTED/suppressed canary。 | ordinary graph canonical bytes 零 canary；敏感 node 仅允许冻结的最小 redacted label，敏感/restricted/hidden incident edge 不泄露。 |
| 6 | 打开 README/PLAN/STATUS/DECISIONS/RESUME/EVIDENCE 六阅读视图。 | README <=16 KiB、STATUS <=12 KiB、Resume <=24 KiB；超限细节拆到有稳定 ref 的页，canonical facts/evidence refs 不丢。 |
| 7 | 删除物化阅读视图并从 canonical raw/events/state/checkpoints 重建。 | 重建后的语义字段、refs 和 hash oracle 一致；Markdown 不是事实 authority。 |
| 8 | 在 graph UI 关闭/开启、读取前/读取后，对同一 fixed recall/query/tool 运行独立 Host probe。 | RecallDecision bytes/hash、rank order、ContextSnapshot bytes/hash、回答和工具行为完全不变；graph-only marker 零进入。graph callable 自报 `influence_zero` 不构成证据。 |
| 9 | 从 TaskScope/turn 导出 route→invocation→proposal→validation→binding/mutation→recall/projection trace。 | trace 完整、只读、不覆盖 lineage；普通导出遵守 suppression，密封读取需独立 AuditAccessDecision。 |

本 relation 增量只把步骤 1-5、8-9 的 graph/relation 子断言作为 required；步骤 6-7 的通用 README/STATUS/六视图
容量与重建继续保留为 program regression，不重复计入 relation slice 的完成门。

## 决定性证据

- exact candidate identity、public manager/seed calls、graph DTO bytes/hash、node/edge/evidence refs、canary scan、
  correction/forget/reopen receipts、六视图字节数与 page refs、图谱 UI 截图、独立 Host 前后
  Context/decision/rank/answer/tool diff 和 trace export。原始证据只写 ignored `.local-test-evidence/`。
