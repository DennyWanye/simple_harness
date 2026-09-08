# 事故 M 裁决与修复：有关系/争议时「记忆列表」整页显示「认知记忆条目无效」

2026-09-08。范围：HM-TO-A6 第 4 次尝试（Host `8e9b7c8d`，Memory SDK 0.6.28）。
证据 DB：`.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1/userdata/data/human_memory_v7.db`。
本备忘录只记录裁决与实现，**不修改** `00-PLAN.md`。

---

## 1. 现象

第 20 轮（更正 → revision 2 → `amends` 关系）与第 21 轮（争议 →
`cognitive_conflict_groups` 1 行 → `contests` 关系）之后：

- 记忆面板「记忆列表」页整页只剩一句「认知记忆条目无效」；
- 「关系图」页正常（节点/边都在），且节点详情写着「需要更正或忘记时，请返回记忆
  列表或主对话操作」——**而唯一的忘记入口就在坏掉的那一页**。

## 2. 复原：离线跑真实 DB

把证据 DB 复制出来（只读原件），用已安装 SDK 0.6.28 直接跑
`manager.get_twin_graph_view` 并按 `PrimaryCognitiveControls.list` 的投影逐条比对
前端 `tauri-app/src/primary/cognitiveRequests.ts::page` 的条目规则，结果：

```
view nodes: 14
cognitive-memory-2fa66229…eb00  rev=2  status='contested'  can_forget=False
cognitive-memory-2fa66229…eb00  rev=3  status='contested'  can_forget=True
OFFENDING ITEMS: [["cognitive-memory-2fa66229…eb00", 3, "contested", true, ["duplicate memory_id"]]]
```

**出问题的字段是 `memory_id` 重复**，不是候选清单里猜的 `content_hash`/`can_forget`
为 None、`status` 超 64 或 `label` 超 512。后面这些在 SDK 侧就不可能发生：
`TwinGraphNode.__post_init__` 强制 `content_hash` 是 64 位十六进制摘要、三个
capability 标志必须是 `bool`、`revision` 必须是 ≥1 的 int；关系记忆
（`semantic_kind == "relation"`）在 SDK 的 `get_twin_graph_view` 里就被过滤掉了，
根本不会进 `view.nodes`。

### 根因链

SDK 展示图的节点主键是 `node_id = memory_id@revision`
（`simple_harness_memory/cognitive/twin_builder.py`）。可见集合是：

```python
visible = {
    record.node_id: record
    for record in records
    if _record_visible(record, current_time)
    and (record.revision == record.head_revision or record.conflict_group_id is not None)
}
```

即：**未解决的冲突组会额外放行组内那个非 head 的 incumbent 修订**。DB 里这个组是

| role | memory_id | revision | can_forget |
|---|---|---|---|
| incumbent | `cognitive-memory-2fa66229…eb00` | 2 | False |
| challenger（= head） | `cognitive-memory-2fa66229…eb00` | 3 | True |

`cognitive_conflict_groups` 只有一列 `memory_id`，所以一个组永远落在**同一个记忆
身份**上。于是 `view.nodes` 出现两个 `memory_id` 相同的节点。

Host 的 `primary.memory.list` 直接遍历 `view.nodes`，把两个节点都当成两条 item 发
出去；前端 `page()` 里的 `seen.has(item.memory_id)` 命中，抛
`认知记忆条目无效`——**一条坏条目炸掉整页**。关系图页按 `node_id` 索引，所以毫发
无损。这解释了「图好、列表全废」的不对称。

### 同一根因的第二个缺陷（原任务只要求"确认"，实测是坏的）

`forget` 里旧的目标选择是：

```python
targets = [node for node in view.nodes if node.memory_id == memory_id]
node = targets[0] if len(targets) == 1 else None
```

争议记忆的 `targets` 长度是 2 → `node = None` → 抛
`primary_memory_target_stale`。也就是说**争议记忆此前根本忘不掉**，即使前端把列表
渲染出来也一样。回归测试里已验证：把修复回退后
`test_forget_applies_to_the_contested_head_and_never_to_its_incumbent` 立刻红。

## 3. 裁决：修在哪一层

用户授权直接裁决，不回问。结论是**两层都改，各自承担各自的职责**。

### 3.1 后端（主修复）：列表/忘记是"记忆身份"面，先折叠到 head 修订

理由：

1. **语义**：`primary_cognitive_controls.py` 的模块 docstring 本来就写着
   "Forgetting addresses the whole memory identity"。同一身份出两行、其中一行还按
   不下按钮，是不可操作的噪音行。
2. **分页**：游标就是 `memory_id`（`node.memory_id > cursor`）。`memory_id` 不唯一
   时游标语义直接坏掉——翻页会漏掉重复对里的第二个节点。
3. **前端契约**：面板本来就 `key={item.memory_id}`，S6 的条目契约也按身份去重。
4. **展示分工**：多出来的那个修订是"为了画出 `contests` 边"才存在的展示节点，它
   的归属是 `primary.memory.graph`（关系图），不是列表。关系图那句「请返回记忆列表
   操作」指向的正是折叠后的 head 行（`status="contested"`、`can_forget=True`），
   信息没有丢。

被否决的替代方案：

- **只让前端跳过重复项**：能止血，但游标语义仍然是坏的，`forget` 对争议 head 依旧
  报 stale，属于"把后端的错误当成数据噪音容忍"。
- **列表里保留两行、给非 head 行置灰**：暴露 revision/hash 这类内部标识，违反
  UI-CONTRACT 里「面板只显示 label，不显示 ID/修订/哈希/SDK 状态串」。
- **按 `can_forget` 过滤非 head 行**：`can_forget` 恰好等于 `revision == head_revision`
  是 0.6.28 的实现细节，不是契约；用它当 head 谓词会把授权语义和版本语义耦死。

实现：新增 `_head_nodes(nodes)`，按 `memory_id` 取 `revision` 最大者（组内
challenger 修订必然大于 incumbent 修订；非组节点本来就是 head）。`list` 与 `forget`
共用它，保证**列表提供什么、忘记就能作用于什么**。折叠时**不**先过滤
`redacted`，避免"head 被脱敏 → 回落到更旧的可见修订"这种降级泄露；折叠之后再由
调用方丢弃 redacted head（列表整条不显示，忘记报 stale）。

### 3.2 前端（纵深防御）：单条降级，绝不因一条坏条目清空整页

页面级形状仍然是硬失败（`primary_ref` 不符、`items` 不是数组、超过 50 条、
`next_cursor` 既不是 null 也不是合法 ID、或 `next_cursor` 不等于服务端实际发出的
最后一条的 `memory_id`）——这些说明响应本身不可信。

条目级改为**跳过 + 计数 + 可见提示**：`CognitiveSnapshot.skipped` 记录本页被丢弃的
条数，面板渲染 `role="alert"` 的「有 N 条记忆条目无效，已跳过；其余条目仍可操作。」
其余行照常渲染、照常可忘记。

一个必须注意的连带修正：原来的游标不变量是
`next_cursor === items.at(-1).memory_id`。跳过条目后"最后一条显示项"可能不再是服务
端发的最后一条，会把合法页误判成 `记忆分页无效`。因此不变量改为对齐**服务端实际
发出的最后一条**（`tail`）；在没有任何条目被跳过时，两者等价，旧语义完全保留。

## 4. 改动清单

后端（1 个文件）

- `backend/deskpet/memory/primary_cognitive_controls.py`
  - 新增模块级 `_head_nodes()`；
  - `list` 改为遍历 `_head_nodes(view.nodes).values()`；
  - `forget` 的目标选择改为 `_head_nodes(view.nodes).get(memory_id)`，CAS
    （`expected_revision` / `expected_content_hash`）与 `redacted` / `can_forget`
    检查一字未动。

前端（2 个文件，**需要重新打包 Tauri 前端 bundle 才会在验证 App 里生效**）

- `tauri-app/src/primary/cognitiveRequests.ts`：抽出 `valid()`；`page()` 改为逐条
  跳过并返回 `skipped`；游标不变量对齐服务端最后一条；`CognitiveSnapshot` 增加
  `skipped`，`clearRead()` 一并清零。
- `tauri-app/src/components/PrimaryMemoryPanel.tsx`：列表页渲染跳过提示。

文档

- `tauri-app/src/primary/UI-CONTRACT.md`：新增 “Cognitive list degradation (S6
  Task3/4, 2026-09-08 incident M)” 一节。
- `plans/2026-09-05-primary-cognitive-controls/API.md`：`primary.memory.list` 条目
  补「每个记忆身份一条」的投影规则。
- `docs/memory-sdk-cognitive-architecture.md` §7：补 Host 展示投影约束。
  （`ARCHITECTURE.md` 是部署总览、该文档 §7 原文描述的还是旧 fact 模型，本次只在
  §7 后追加 Host 侧约束，不改写旧设计正文。）

测试

- `backend/tests/memory/test_primary_cognitive_controls.py`：+4 条。
- `tauri-app/src/primary/cognitiveRequests.test.ts`：+12 条（含 9 条参数化）。
- `tauri-app/src/components/PrimaryMemoryPanel.test.tsx`：+1 条。

## 5. 验证

**离线复现 → 修复后**（同一份证据 DB 拷贝，已安装 SDK 0.6.28）：

```
修复前: view nodes 14 → 14 items，1 条 duplicate memory_id  → 前端整页失效
修复后: view nodes 14 → 13 items，全部满足前端条目契约，next_cursor=None
        争议记忆只剩一行: rev=3, status=contested, can_forget=True
```

**后端**：`tests/memory/test_primary_cognitive_controls.py` 11 passed。
把修复回退后，4 条新测里 3 条转红（另 1 条是"普通 supersede 链"的反向对照，本来
就该绿）：

- `test_contested_chain_lists_one_contract_valid_head_per_memory`（重复 memory_id）
- `test_forget_applies_to_the_contested_head_and_never_to_its_incumbent`（争议 head
  被误判 stale）
- `test_redacted_head_never_falls_back_to_an_older_visible_revision`（脱敏回落）

受本次改动影响的全部后端套件（凡引用 `primary.memory.list/forget` 或
`PrimaryCognitiveControls` 的文件）：`test_primary_cognitive_controls.py`(11) +
`test_primary_cognitive_evidence.py` / `test_primary_memory_graph.py` /
`test_cognitive_typed_barrier.py`(19) 全绿。

整个 `tests/memory` 做了**基线对拍**（同一台机、同一 venv、`-p no:randomly`）：

| | 结果 |
|---|---|
| 基线 `3694730a`（回退本改动与新测） | `62 failed, 620 passed, 2 deselected` |
| 本分支 | `62 failed, 624 passed, 2 deselected` |

失败集合逐条 diff **完全一致**（`sort` 后无差异），多出来的 4 条 passed 就是本次新
增的后端测试——**零回归**，62 条全部是既有红（short-index embedder、s5b/s5c cutover、
prospective ack、wemm、trusted disclosure 等）。其中与本面相关且任务已列明/已核验为
既有红的有：`test_primary_runtime_api_integration.py` 的 4 条
`test_memory_only_forget_filters_real_history_and_next_outbound_after_reopen[*]`
（单独在基线上复跑同样红）、
`test_primary_history_outbound.py::…[sent_unknown]`、
`test_primary_visibility.py::test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite`。

**前端**：UI-CONTRACT 里那条聚合命令再加上三个记忆面板套件 →
20 files / **182 passed**；`tsc -b --noEmit` 干净；改动的 4 个前端文件 eslint 干净。
把 `cognitiveRequests.ts` 回退后，12 条新测全红（另有面板测 1 条同样红）。

## 6. 遗留

- 本次没有跑真人 UI 复验；证据 DB 的离线投影 + 单测是本轮的验证边界。
- 前端两个文件属于 Tauri 前端 bundle，**验证 App 必须重新构建前端产物**，仅重启后
  端不会带上跳过提示与去重后的分页行为（去重本身在后端，重启后端即可生效）。
