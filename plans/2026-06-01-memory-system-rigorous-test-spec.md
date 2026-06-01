# 记忆系统严格测试方案（spec — 待审，未实现）

> **状态**: SPEC / 待用户审批。审过再写代码。
> **动机**: 现有 131 个记忆测试给的是"系统不会崩"的信心，不是"系统能正确召回"
> 的信心。两轮 Explore 盘点（架构 + 测试覆盖）交叉确认了 4 个系统性问题，最近
> 一次 F1~F4 真机终验又把一个草率 PASS 推翻、挖出 F5（LIKE 整串缺陷）。本方案
> 定义"真正验证记忆系统功能"该补的测试，并明确每条如何排除 confound。
> **关联**: [memory-tools-flag-gating-bugs.md](./2026-05-31-memory-tools-flag-gating-bugs.md)（F3/F4/F5）

---

## 0. 盘点结论（为什么要做）

| # | 系统性问题 | 证据 | 后果 |
|---|---|---|---|
| ① | **Mock embedder 伪装语义测试** | 所有"语义召回"测试用 mock hash 向量（`cos(A,A)=1, cos(A,B)≈0`），只能验"同文本命中"，验不了"语义相似不同文本"。真 BGE-M3 测试全 `@model_required` 默认跳过 | 向量召回"有效性"信心虚假 |
| ② | **LIKE 整串缺陷（F5）系统性** | `facts.search`(facts.py:432) + `workspace.recall`(workspace.py:223) + `find_by_entities`(facts.py:452) 都 `LIKE '%整串%'`。现有测试**全用精确子串 query**（`"oolong"⊆"oolong tea"`）规避了 | 自然语言 query 召回失败，CI 抓不到 |
| ③ | **检索有效性只断言非空** | 4/14 retriever 测试只 `assert hits 非空`，不验"返回的是正确那条" | RRF 权重清零 / 排序倒置类 bug 能蒙混过测 |
| ④ | **工具层 20% 覆盖** | `memory_search/write/read` **零测试**（仅 memory_forget 有 8 个） | 这 3 个工具真不真能用，没人验过 |

**架构风险点（来自架构盘点）**：
- P0 LIKE 整串（已确认 F5）
- P0 flag 互锁只校验 2/12（main.py 仅校验 enhanced_retriever / entity_path 依赖 facts_extract）
- P0 workspace loop binding 二阶陷阱（sync handler 需 rebind_loop）
- P1 facts 召回依赖 embedder，否则降 LIKE

---

## 1. 核心原则（每条测试必须遵守）

1. **断言"正确条目"而非"非空"** — 每个检索测试必须有"应命中 X + 应排除 Y"双向断言。
2. **排除 confound** — 验 workspace_memory 召回时，会话**不带相关对话历史**（否则分不清是召回还是历史）；验向量语义时，query 与目标**文本不同但语义近**。
3. **真假 embedder 双轨** — 凡涉及"语义召回有效性"的，必须有 `@model_required` 的真 BGE-M3 版本；mock 版本只用于"链路通/确定性"断言，**不得**声称验证了语义。
4. **真运行栈优先** — 工具层端到端尽量走真 backend（lifespan bind 后的真实例），而非直接 import 函数自测自。

---

## 2. 测试矩阵（分 6 组，标优先级）

### G1 — 检索有效性（P0，最大缺口）
**目标**：证明检索返回的是**正确**条目，不是随机/任意非空。

| 用例 | 设计（含 confound 排除） | 断言 |
|---|---|---|
| G1.1 RRF 返回相关项排除无关项 | 存 msg"我喜欢红色"(id1) + "我喜欢蓝色"(id2)，query"红色" | `id1 in hits` **且** `id2 not in hits`（双向）|
| G1.2 RRF 权重线性性 | 同数据，改 vec/fts/recency/salience 权重 | 权重变 → 排名**确实变化**（非恒定）|
| G1.3 四路单独一致性 | 分别只开 vec / fts / recency / salience | 每路单独返回符合该路语义的 top（vec 按相似、recency 按时间…）|
| G1.4 排序方向正确 | recency 路 | 新消息 rank < 旧消息 rank（防倒置）|
| G1.5 真语义召回（真 embedder）| `@model_required`：存"我家猫叫旺财"，query"宠物名字"（**文本无重叠**）| 真 BGE-M3 下命中；**mock 下预期不命中**（对照证明 mock 验不了语义）|

### G2 — LIKE 整串缺陷 F5 专项（P0，回归 + 修复验证）
**目标**：钉死 F5 现状，修复后转为正向验证。

| 用例 | 设计 | 断言（修复前/后）|
|---|---|---|
| G2.1 facts.search 自然语言 query | 存 fact value="我家猫叫旺财，三岁橘猫"，query"宠物" | **修复前**：0 命中（钉死缺陷）；**修复后**：命中 |
| G2.2 workspace.recall 自然语言 query | 存 path=README.md action=read，query"刚读的文档" | **修复前**：0 命中；**修复后**：命中 |
| G2.3 find_by_entities 多实体 | value 含多实体，query NER 出多词 | 分词后任一实体命中即返回 |
| G2.4 精确子串仍工作 | 回归：`"oolong"` 搜 `"oolong tea"` | 始终命中（不能因修复 F5 破坏子串路）|

### G3 — 工具层端到端（P0，覆盖 20%→全）
**目标**：memory_search/write/read 真能调用、真返回正确结果。走真 backend bind 后的实例。

| 用例 | 设计 | 断言 |
|---|---|---|
| G3.1 memory_write → memory_read 闭环 | write 一条 fact → read 回来 | 返回的 value/key 与写入一致 |
| G3.2 memory_write → memory_search 闭环 | write → search（关键词命中）| 搜到刚写的，count≥1，内容正确 |
| G3.3 memory_search bound 状态 | 默认配置（memory_forget=False）| `ok:true` 非 `not bound`（F3 回归）|
| G3.4 workspace_recall read 端闭环 | file_read → workspace_recall | 取回刚读的 path（F4 read 端，**当前 F5 阻断**）|
| G3.5 工具错误处理 | embedder 缺失 / db 锁 | 优雅降级，不抛未捕获异常 |

### G4 — Flag 矩阵（P1）
**目标**：flag 开关行为差异 + 字节级契约。

| 用例 | 设计 | 断言 |
|---|---|---|
| G4.1 字节级回归（已有 test_byte_level_consistency，扩展）| 全 flag False | 召回结果与 gen-1 逐字节一致 |
| G4.2 flag 依赖校验 | enhanced_retriever=True 但 facts_extract=False | 不崩，降级为 base（main.py 已校验，补测）|
| G4.3 未校验 flag 的安全性 | 单开 chunking / query_rewrite 无 facts_extract | 不崩溃 |
| G4.4 多 flag 同开交互 | enhanced_retriever + entity_path + rerank 全开 | 召回质量 ≥ 单开，无冲突 |
| G4.5 workspace_memory on/off 效果 | flag True vs False | True：workspace_state 写入 + 组件有内容；False：store=None 静默 |

### G5 — 真 embedder vs mock 对照（P1，戳破 confound ①）
**目标**：量化 mock 与真 BGE-M3 的召回差异，证明哪些测试 mock 验不了。

| 用例 | 设计 | 断言 |
|---|---|---|
| G5.1 语义句对召回 | 标注"我喜欢乌龙茶"vs"我爱喝黑茶"（语义近文本异）| 真 BGE-M3 hit@5 命中；mock 不命中 |
| G5.2 eval_gate 真 embedder 跑一遍 | zh_fixture 用真 BGE-M3 | 记录真 hit@5 vs mock hit@5 差距（暴露 fixture 是否虚高）|

### G6 — 写入闭环边界（P2，现有 95% 补边界）
| 用例 | 断言 |
|---|---|
| G6.1 facts upsert embedding 真计算 | 写入后 embedding 列非空且维度对 |
| G6.2 workspace 大文件 / hash 碰撞边界 | 截断正确、hash 稳定 |
| G6.3 并发 facts 抽取 / 检索竞态 | 无丢失、无死锁 |

---

## 3. 真 embedder 策略（关键决策点，需你定）

现状：真 BGE-M3 测试全 `@pytest.mark.model_required` 默认跳过，CI 不跑。
**问题**：不跑真 embedder → 语义召回的信心永远是虚的。

**三个选项**（写代码前需定）：
- **A**：保持 `@model_required` 默认跳，但**新增**几个关键语义测试（G1.5/G5），文档明确"发布前本地手动 `pytest -m model_required` 跑一遍"。CI 仍跳。
- **B**：CI 也跑真 embedder（需 CI 有 GPU/权重，成本高，这机器是单机开发不一定适用）。
- **C**：本地 pre-release gate 脚本里强制跑 `-m model_required`，纳入 STATUS 验收纪律。

> 倾向 **A + C**：新增真测但默认跳，靠发布前 gate 纪律保证真跑。不阻塞日常 CI。

---

## 4. 实现计划（审批后）

1. **Phase 1（P0）**：G1 检索有效性 + G3 工具端到端 + G2 F5 回归（钉死现状）
2. **Phase 2**：修 F5（LIKE 整串 → 分词/FTS/向量），G2 转正向验证
3. **Phase 3（P1）**：G4 flag 矩阵 + G5 真 embedder 对照
4. **Phase 4（P2）**：G6 边界 + 性能基线

每 Phase 跑完更新本文档勾选 + STATUS。

---

## 5. 决策（已定稿 2026-06-01）

| 决策 | 选定 |
|---|---|
| **范围** | ✅ 全 4 Phase（P0→P1→P2 全覆盖）|
| **F5** | ✅ 连带修复（LIKE 整串 → 分词/FTS/向量），G2 钉死现状后 Phase 2 转正向 |
| **真 embedder** | ✅ CI 也跑真 BGE-M3（见下方可行性核实）|
| **放哪** | 待定（worktree 继续 / 新分支）— 倾向**新开分支** `feat/memory-rigorous-tests`，因这是独立大工程，不再挂 F1/F2 那个分支 |

## 6. 真 embedder 可行性核实（2026-06-01 实测）

| 项 | 结论 |
|---|---|
| BGE-M3 权重 | ✅ 在 `C:/Users/24378/AppData/Local/deskpet/models/bge-m3-int8/` |
| 真向量产出 | ✅ 通过 `Embedder`（子进程 worker）实测 `dim=1024` 真向量 |
| ⚠️ 裸 import 陷阱 | **直接 `import torch + FlagEmbedding` 主进程段错误**（CLAUDE.md CUDA DLL 坑）→ 测试**必须走 Embedder 子进程封装**，不能裸 import |
| 现有真测 | `tests/test_deskpet_embedder.py::test_real_bge_m3_encode_and_self_similarity`（已有 1 个 model_required 真测，框架通）|

**CI 跑真 embedder 的前置修复**（写测试前必做）：
- **pytest 配置缺 `testpaths`** → 默认全仓扫，撞 `dist-portable/`(打包产物)+`temp/` 的
  PermissionError（15 errors）。`pytest tests/` 明确目录则干净。**修法**：pyproject.toml
  `[tool.pytest.ini_options]` 加 `testpaths = ["tests"]`。
- CI 真 embedder 需 GPU + 权重落盘；本机是单机开发，CI 实际就是本地 gate。
  真 embedder 测试用 `model_required` mark，CI gate 脚本显式 `pytest tests/ -m model_required`。

## 7. 实现前置任务（Phase 0）
1. 修 pytest `testpaths`（消除 collection 15 errors）
2. 建分支 `feat/memory-rigorous-tests`（若选新分支）
3. 确认真 embedder 测试 helper（统一用 Embedder 子进程封装的 fixture，禁裸 import）
