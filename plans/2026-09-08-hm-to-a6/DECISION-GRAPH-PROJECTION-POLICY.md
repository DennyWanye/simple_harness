# 裁决：普通（ordinary）图谱投影口径 —— 争议期该不该有边

- 日期：2026-09-09
- 触发：HM-TO-A6 尝试 5，A6-9「ordinary projection policy 过滤」/ A6-10「relation/endpoint 遗忘 + close/reopen」判 INCONCLUSIVE，
  原生图谱面板在争议 + 遗忘态下显示「13 条记忆，1 条关系」，而 A6 计划期望「争议/遗忘态下普通图谱 edge 数按预期降到 0」。
- 证据：`.local-test-evidence/2026-09-08/native-a6-run5/primary-ui-htxhf38f/`
  （`userdata/data/human_memory_v7.db` + 同名 `-wal`/`-shm`；`cognitive_relations` 2 行、`cognitive_conflict_groups` 1、
  `cognitive_conflict_resolutions` 0、`suppression_directives` 1、`cognitive_memory_heads` 14）
- 装机 SDK：Memory 0.6.31（`.local-test-evidence/2026-09-07/installed-h0710-m0631-s0313`）
- 工作树：`worktree-graph-projection`

> **注意**：只读 `human_memory_v7.db` 会读到 WAL 之前的旧状态（`suppression_directives` = 0）。复算/取证必须把
> `.db` / `.db-wal` / `.db-shm` 三个文件一起复制。

---

## 1. 契约怎么规定的

只读引用 memory-sdk `plans/2026-08-29-human-memory-digital-twin/`：

1. `slices/S3-cognitive-systems-recall.md` Task 6（Display-only digital twin graph）：

   > `twin_builder.py` 从 canonical **active/contested/inferred** records 和 relation rows 生成 node/edge DTO，
   > 节点包含 type/status/confidence/source refs/correction-forget capability；**superseded/suppressed/expired**
   > 按普通 view policy 不展示。

   即：**contested record 是展示素材，不是被过滤对象**；普通 view policy 挡掉的是 superseded / suppressed / expired 三类。
   `slices/S6-ui-and-program-verification.md` Task 3 与之一致：节点详情要显示 `conflict`，「candidate/inferred 视觉明确」，
   「suppression receipt 后立即移除普通节点」——移除的锚点是 suppression，不是 conflict。

2. `acceptance.md` HM-S12 / HM-TO-A6：

   > clean-wheel public API 创建两个 canonical nodes + 一条 relation memory；普通图谱显示一条可追溯 edge；纠正后只显示新
   > active edge，**relation/端点遗忘、争议或 ordinary projection policy 判定不可展示后 edge 退出**，close/reopen 不复活。

   这句话的主语是 HM-S12 场景里那条**由 relation memory 承载的 knowledge 边**（`applies_to`：偏好 claim → 发布检查清单
   procedure）。「争议」指的是**它的端点进入争议**，不是「图上任何边都要消失」。

## 2. 装机 SDK 0.6.31 的实现（与契约一致）

`simple_harness_memory/cognitive/twin_builder.py` + `backends/sqlite_v5.py:1143-1360` 把投影分成两层：

**node 层**（`build_twin_graph_view` + `_record_visible`）

- 展示集合 = 「head revision」∪「未裁决冲突组的成员 revision」，两者都要先过 `_record_visible`
  （非 suppressed、valid-time 内、lifecycle 在该类型的活跃集合内，或 `llm_inference` 的 candidate/draft）。
- 冲突组是**原子**的（builder docstring 逐字写明：*if either exact member is hidden … neither member nor its edge is
  emitted*）：incumbent 与 challenger 要么一起出现（`status="contested"`），要么一起消失。
- `semantic_kind == "relation"` 的 relation memory **从不作为节点**（`sqlite_v5.py:1207-1212`）。
- `redact_content` 由 privacy class / 敏感属性决定，Host `PrimaryCognitiveControls.graph` 再把 `redacted` 节点整条丢掉
  → 普通图谱 node/edge 里不含 redacted 项。

**edge 层**

- **evolution 域**（`amends`/`supersedes`/`contests`/`supports`/`relates_to`）：`_twin_graph_relation_input_unlocked`
  原样放行，最终只由 builder 的「两端 exact revision 都是可见 node」决定。旧 revision 永远不是可见 node，
  所以 `amends`/`supersedes` 血缘边在普通图谱里**结构性不可见**；争议期唯一能出现的 evolution 边就是 `contests`，
  它正是「争议」在图上的可读形式（否则两个 contested 节点会孤立地并排放着，用户看不出谁和谁在争）。
  裁决或抑制任一成员后，成员离图，`contests` 边随之退出。
- **knowledge 域**（`applies_to`）：额外套 `twin_graph_record_is_active_visible`（`twin_builder.py:482-495`），
  对 relation memory owner + source + target 三者同时要求 **head revision + 活跃 lifecycle + `conflict_status != contested`
  + 不在任何冲突组 + 未被抑制 + 未过期**。端点一进争议、任一端点或 relation memory 被遗忘，边立刻退出，close/reopen 不复活。

**结论（裁决）**：契约要求的是「**争议期 knowledge 边降到 0，而 `contests` 边正常出现**」，不是「edge 归零」。
Host 与 SDK 的投影都**符合契约，无需修改**。A6 计划里那句「争议/遗忘态下普通图谱 edge 数按预期降到 0」是把 HM-S12 的
knowledge 边规则误抄成了全量规则——本轮修的是验证器，不是产品代码。

## 3. 离线复现：那 13 个 node、1 条 edge 到底是什么

用装机 SDK + Host runtime 直接对证据库副本调 `manager.get_twin_graph_view(principal=…)`（`deskpet.memory.human_memory_v7.HumanMemoryV7Runtime`），
`PrimaryCognitiveControls.graph` 只做「丢 redacted + 按 node_limit/edge_limit 截断 + 丢端点不在选中集合的边」，
本次没有任何截断，所以其输出等于 SDK 视图：

```
喂给 builder 的 records：15 条  = 14 个 head + 1 个未裁决冲突组的 incumbent(rev 2)
  · …49197ba8@1  suppressed=True  ← T23 UI 遗忘的直接目标（suppression_directives 唯一一行）
  · …cabca33a@1  suppressed=True  ← 与上一条共享同一条 USER 证据 14f03603…（duplicate-source alias）
  · 其余 13 条 visible=True，含 …93d47e04@2(incumbent) 与 …93d47e04@3(head)，两者 status=contested
喂给 builder 的 relations：2 条
  · contests  …93d47e04@3 → …93d47e04@2
  · amends    …93d47e04@2 → …93d47e04@1
结果：nodes=13  edges=1（只剩 contests）
```

- **可见的那条边是 `contests`**：两端（rev 3 head、rev 2 incumbent）都是未裁决冲突组成员，都在可见 node 集合里。
- **`amends` 边被过滤**：它指向 rev 1，rev 1 既不是 head 也不是冲突组成员 → 不是可见 node → 边退出。
- **两条被抑制的记忆**：T23 只遗忘了一条语义记忆，但 SDK 按 2026-09-07 产品决定（`sqlite_v5.py:1733-1741` 注释）
  把「与被遗忘记忆共享同一条 USER 证据的记忆」当作重新学到的同源副本一并抑制（`history_source_guard.duplicate_source_matches`），
  于是同一轮用户消息产出的那条 episode（`…cabca33a`）也离图；被抑制的是**记忆**，来源会话证据本身不被隐藏，
  与 CLAUDE.md 记录的「遗忘只针对记忆，不针对会话记录」一致。
- 14 head − 2 抑制 = 12 个记忆身份，加上冲突组多出的 incumbent revision = **13 个 node**，与 UI 的「13 条记忆」逐字吻合。

## 4. 本轮改动

**不改** `backend/deskpet/memory/primary_cognitive_controls.py`（投影正确），**不改** SDK（无需求变更）。

改 `scripts/native/a6_verify.py`：

- 新增 `_ordinary_graph_expectation(ev)`：按上面两层口径，从证据库纯 SQL **复算**普通图谱应有的 node/edge 集合
  （head + 未裁决冲突组原子成员；扣掉 suppressed（含同源副本）/ expired / 非活跃 lifecycle / relation memory / redacted；
  evolution 边看两端 exact revision 是否可见，knowledge 边套严格资格）。
- 新增 `_manual_ui_observations(ev)`：从 `a6-progress.jsonl` 的 `manual_ui` note 里解析驱动脚本记录的人工观测，
  两种在用写法都认：`nodes=13 edges=1` 与 UI 原文「13条记忆，1条关系」。
- **A6-9** 由「恒 INCONCLUSIVE」改为「复算值 vs 最后一次人工观测」逐一对齐：一致 PASS、不一致 FAIL；
  只有在表/列缺失、存在 subject/evidence/entity 作用域抑制（SQL 复算不到）、或 note 里没有可解析计数时才 INCONCLUSIVE。
- **A6-10** 由「恒 INCONCLUSIVE」改为三条都算：(a) `cognitive_relations` 不得物理减少；
  (b) 遗忘后的观测必须等于 post-suppression 复算值，且复算必须显示确有节点因抑制离图；
  (c) T23（遗忘后）与 T24（关表重开）两次观测必须逐字相同。任一不满足 FAIL。
  本次证据无 knowledge 关系行，reason 里显式写明「relation memory 自身被遗忘」子例由 A6-6 承载（本次 A6-6 FAIL，
  原因是该后端早于分析协议 v8，未产出 `applies_to`）——不靠 A6-10 的 PASS 掩盖 A6-6 的覆盖缺口。

## 5. 尝试 5 证据上的重跑结果

`python scripts/native/a6_verify.py --evidence .local-test-evidence/2026-09-08/native-a6-run5/primary-ui-htxhf38f --installed-target .local-test-evidence/2026-09-07/installed-h0710-m0631-s0313`

| 项 | 原判定 | 新判定 | 依据 |
|---|---|---|---|
| A6-9 | INCONCLUSIVE | **PASS** | 复算 nodes=13 / edges=1，T24 人工观测 nodes=13 / edges=1 一致；被抑制 1 条（含同源副本 1 条）、redacted 0、expired 0、relation memory 节点 0；可见边 kind=`['contests']`，被过滤边 kind=`['amends']` |
| A6-10 | INCONCLUSIVE（人工核 PASS） | **PASS** | 关系行 2 未减少（峰值 2）；遗忘使 2 个节点离图；T23 观测与复算一致；T24 关表重开逐字相同，未复活 |

整体：**PASS 13 / FAIL 1 / BLOCKED 1 / INCONCLUSIVE 3**（原为 PASS 11 / FAIL 1 / BLOCKED 1 / INCONCLUSIVE 5；
仅 A6-9、A6-10 两项判定变化，其余项一字未动）。唯一的 FAIL 仍是 A6-6（该后端早于分析协议 v8，未产出 `applies_to` 关系）。

## 6. 控制（测试）

- 新增 `backend/tests/native/test_a6_verify_a6_9_a6_10.py`（17 项）：争议期保留 `contests`、隐藏 `amends` 血缘；
  遗忘 + 同源副本离图；观测与复算不符判 FAIL；无观测降 INCONCLUSIVE；redacted/expired/restricted 永不入图；
  knowledge `applies_to` 在端点争议时退出、端点干净时出现且 relation memory 不作节点；冲突组裁决后 incumbent 与
  `contests` 边一起消失；非 memory 作用域抑制判不可复算；revoke 后不再隐藏；两种 note 写法都解析；
  close/reopen 复活判 FAIL；关系行物理减少优先判 FAIL；只有一次观测降 INCONCLUSIVE。
- `tests/native/`（含既有 `test_a6_verify_a6_7.py`）+ `tests/memory/test_primary_memory_graph.py`
  + `tests/memory/test_primary_cognitive_controls.py`：**48 passed**（无既有红）。
- `a6_verify.py --selftest`：18 个判定项全部可执行。

## 7. 后续（followup）

- **F-G1**：A6-6 未产出 knowledge `applies_to` 行 ⇒ HM-S12 主线（knowledge 边随端点争议/遗忘退出）在原生跑上仍未被真实覆盖。
  下一次原生跑接入分析协议 v8 后需重跑 A6-6/A6-9/A6-10。
- **F-G2**：`a6_driver.sh` 的手工 UI 观测目前靠人打字。建议在 `@UI@` 步骤旁加一条只读的 `primary.memory.graph` 调用把
  `nodes/edges` 直接写进 `a6-progress.jsonl`，让 A6-9/A6-10 完全脱离人工转录。
- **F-G3**：`_ordinary_graph_expectation` 的同源副本判定用「共享同一条 `user_message` 证据」近似 SDK 的
  `exact_user_key`（同一 /text 画像的文本相等）。若将来出现「不同 evidence_id 但文本逐字相同」的重复学习，复算会偏高；
  届时需要把 `evidence_envelopes.sanitized_payload` 的 /text 也纳入 key。
