---
id: TC-HM-12
purpose: Verify bounded TaskScope read views and a traceable display-only digital twin projection
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A2, HM-TO-A6, HM-TO-A7, HM-TO-A8, HM-TO-R3, HM-TO-R5, HM-TO-R7, HM-TO-R9, HM-S4-TO-VIEWS]
tags: [human-memory, digital-twin, knowledge-graph, read-views, display-only]
entrypoint: TaskScope inspector and digital twin view
preconditions:
  - Task 6 exact Memory candidate wheel, SHA-256, source commit, and version are pinned for the SDK lane
  - Package-root public manager/backend evidence, mutation, correction, suppression, close/reopen seed contract is pinned
  - Fresh ignored artifact directory is available
revision: 4
---

# TC-HM-12 rev4 — 六阅读视图、展示型数字孪生体与一等语义关系

## 固定输入与边界

- DTO oracle：`fixtures/twin-graph-v1.json` revision 1，SHA-256
  `02c7e7e306cdccf1cb7e7a6d792b0e7e39a9bf4161db4eeaade7a4f70deaed61`。
- self-check runner：`runners/run_twin_graph_public_consumer.py`，SHA-256
  `8a8d4bdc169f3cbc2e254f8b888d350e8e9245c749bad564ef8affa19ce3e1b0`。
- relation oracle：`fixtures/semantic-relations-v1.json` revision 1，SHA-256
  `224d8f864eb52f285d96f957cd60a455b37eaa97e18fe1f36f8674d055b8f439`。它在生产实现前冻结 Harness v5 三 operation wire、双 wheel package-root 边界、
  2 nodes + 1 `applies_to` edge、relation node zero、endpoint suppression 与 close/reopen 0 edge。
- `twin-graph-v1.json` 是 rev1 projection regression 数据，包含的历史 edge labels 与 `mem-rel` 普通 node 不是 v5
  relation-memory write contract；v5 knowledge relation kind 只以 relation oracle 的 `applies_to` 为准。
- 候选身份固定为 Harness `simple-harness-sdk==0.7.0`、source commit
  `3e7a71af1dfea2e065530208225ac13fc5f17300`、wheel SHA-256
  `d241052d4bb7397971da8a99f680e397288bbbefc0d9304fa97f059941dd93bd`，以及 Memory
  `simple-harness-memory-sdk==0.6.0`、source commit `64284059f9ee82d886d85151a95c542660d09c1a`、wheel
  SHA-256 `844cbabaddb33b6ed48d1104ba1427335dcc267a33f10ff93f13c8fec5d06d5e`；两仓两次独立 build
  均 byte-identical。public adapter SHA-256 为
  `21209b70eb38de4e77635e1fb9edc4adeb1acdfc03cdb9e64f84f75f6492684e`。
- 唯一 graph read port 名称是 `get_twin_graph_view`。禁止 graph→recall/rank/context conversion，也禁止
  Agent runtime、Recall pipeline 或 Context assembler 把 graph DTO 当 authority。
- 正式 SDK lane 只能从 exact installed wheel 的 package root 取得 public Manager/backend/DTO；必须用公开 evidence、
  mutation、correction、suppression 与 close/reopen API 建立真实 canonical durable state，再通过 Manager/
  `CognitiveMemoryBackend.get_twin_graph_view` 读取。禁止 source checkout、private import、直接 SQL、repository object，
  也禁止把 fixture 的 `canonical_records/relation_rows` 直接传给 graph method。
- exact public seed contract 已固定为 package-root evidence admission、strict atomic mutation、owned committed receipt view、
  graph read、authorized endpoint suppression 与 close/reopen。formal runner 必须先核对 wheel bytes/metadata/source pin 和
  required public symbols；DTO self-check PASS 仍不是产品 PASS。

## 冻结命令

```bash
python testcase/human-memory-program/runners/run_twin_graph_public_consumer.py --self-check
```

```bash
python testcase/human-memory-program/runners/run_twin_graph_public_consumer.py \
  --harness-wheel <exact-harness-wheel> \
  --harness-wheel-sha256 <exact-harness-sha256> \
  --harness-source-commit <exact-harness-commit> \
  --memory-wheel <exact-memory-wheel> \
  --memory-wheel-sha256 844cbabaddb33b6ed48d1104ba1427335dcc267a33f10ff93f13c8fec5d06d5e \
  --memory-source-commit 64284059f9ee82d886d85151a95c542660d09c1a \
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

## S4 Host 六视图自动化子 lane（required）

| 步骤 | 公开 Host 动作 | 预期结果 |
|---:|---|---|
| S4-1 | 对同一 TaskScope 通过公开 API 依次建立 1k、10k、100k canonical event revision，其中含 Unicode、超长字段、cancel reason、next action、checkpoint 和 evidence refs。 | 每个 revision 均获得 immutable revision/ref/hash；旧 revision 重读不变。 |
| S4-2 | 逐 revision 读取 README/PLAN/STATUS/DECISIONS/RESUME/EVIDENCE 及全部 page index。 | README <=16 KiB、STATUS <=12 KiB、ResumePackage <=24 KiB、单 page <=32 KiB；EVIDENCE 每 500 events 一页；顶层大小不随 1k→10k→100k 无界增长。 |
| S4-3 | 从 page 0 遍历稳定 page refs，独立重算每页 bytes/hash 并汇总 canonical field/ref coverage。 | page ID 稳定绑定 scope/revision/kind/index/hash；全部 canonical 字段与 evidence refs 可恢复，无重复/丢页。 |
| S4-4 | 删除可重建 projection/search cache，重建并冷重启；同时比较冷重启前后 checkpoint 与 caller live probe。 | 同一 canonical revision 的六视图/page bytes 与 hash byte-identical；Markdown/cache 不是 authority；drift 只作显式 report，不改 canonical checkpoint。 |
| S4-5 | 在 projection worker commit 前后故障并重放 lost ACK。 | canonical facts 不回滚、不重复；projection receipt/ref/hash 唯一且可恢复。 |

本 S4 子 lane 不运行 graph/LLM/UI 步骤，原始输出只写 ignored `.local-test-evidence/`。

本 relation 增量只把步骤 1-5、8-9 的 graph/relation 子断言作为 required；步骤 6-7 的通用 README/STATUS/六视图
容量与重建继续保留为 program regression，不重复计入 relation slice 的完成门。

## 决定性证据

- exact candidate identity、public manager/seed calls、graph DTO bytes/hash、node/edge/evidence refs、canary scan、
  correction/forget/reopen receipts、六视图字节数与 page refs、图谱 UI 截图、独立 Host 前后
  Context/decision/rank/answer/tool diff 和 trace export。原始证据只写 ignored `.local-test-evidence/`。
