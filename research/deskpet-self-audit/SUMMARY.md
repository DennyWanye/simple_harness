# DeskPet 现状自查 — 综合小结（写 plan 前的事实校准）

> 4 份分方向自查的综合。目的：把「调研提出的缺口」与「DeskPet 真实代码现状」对齐，
> 区分【真缺口·要新建】/【部分·要增强】/【已有·别重造】。
> 最后更新：2026-06-04 ｜ 证据见同目录 4 份 `p0-*.md` / `p1-*.md`。

---

## 0. 一句话结论

调研定位的方向是对的，但自查修正了**工作量分布**：
**「五路混合检索」「半衰期衰减核心」已经做好了，不用做**；真正的高杠杆缺口高度集中在
**`goal_store.py` 纯内存态 + 抗漂移注入(re-anchoring/handoff) + 结构化自我纠错字段 +
goal/decision/constraint 记忆 + 技能自创闭环** 这几处——而且它们彼此咬合，能串成一条闭环。

---

## 1. 修正后的 Gap 总表（✅已有别重造 / 🟡部分要增强 / ❌真缺口要新建）

| 方向 | 机制 | 自查结论 | 关键证据 |
|---|---|---|---|
| **P0-1 目标持久化** | `/goal` 目标存储 | 🟡→❌ **纯内存态，重启即丢** | `goal_store.py:10-11`（明注 TODO: SessionDB persistence 留 v2） |
| | TODO 结构 | 🟡 有持久化但**无依赖图** | todo JSON + SessionDB 两套，但扁平无依赖 |
| | re-anchoring 抗漂移 | ❌ **真缺口** | `ContextCompressor`/`history_compactor` 压缩时**完全不读 goal_text** |
| | handoff goal checkpoint | ❌ **真缺口** | `spawn_team.py:66-92` Team Charter 只含任务列表，父 `/goal` 未传入 |
| | 中断/恢复接 goal | 🟡 基建有、goal 层未接 | `auto_resume.py:220-225` 能 respawn，但 resume 不注入 goal_text |
| **P0-2 自我纠错** | verify 失败行为 | 🟡 有 nudge+continue 但**默认 off**、最多 2 次文本 nudge、不分析原因 | `agent_loop.py:955-1045`；`ephemeral_subagent` 是 stub |
| | 结构化反思字段 | ❌ **真缺口** | 全库无 `error_analysis/execution_critique/replan`；selfcheck 是自由文本三档 |
| | 自动重试 | ✅ **两层已有** | 工具级 `circuit_breaker.py`；目标级 `auto_resume.py`（均有上限） |
| | 外部/多 persona 验证 | ❌ **真缺口** | GoalChecker 用同一 LLM 自评；无独立 evaluator 语义评分 |
| | verify 重述原目标对照 | 🟡 部分、两分支independent | VerifyGate 只核 receipt 不接 goal_text；GoalChecker 有 goal_text 但独立 if 分支 |
| **P0-3 记忆+人格** | 多路混合检索 | ✅ **已做好（7 路）别重造** | `retriever.py` vec+FTS5+recency+salience 4 路 RRF；`enhanced_retriever.py` +facts+chunk+entity |
| | 人格/偏好画像 | 🟡 抽取了但**不主动注入** | `facts.py` 抽 preference/profile；`preference_memory.py` 存计划/意图；**无 Component 把活跃偏好置顶渲染进 prompt** |
| | 半衰期衰减 | 🟡 **核心已有**、缺 Pin | `facts.py _CATEGORY_DECAY` preference≈200天/profile 永不衰减 + `daily_decay()`；**缺 Pin 钉住；`PreferenceMemory` JSON 无衰减** |
| | 写入分级 | 🟡 有异步批写、**无 light 快路** | 异步 `VectorWorker` 批量；无 `skip_embed` 高频流快路 |
| | goal/decision/constraint 抽取 | ❌ **最大功能缺口** | facts 5 category 无 goal/decision/constraint；extract prompt 倾向过滤掉时效性目标 |
| **P1-4 Skills** | 启动只载摘要（一级） | ✅ **已做** | SkillComponent 启动只注入 name+description 列表 |
| | 触发载正文（二级） | 🟡 靠 LLM 自由裁量、非 embedding 匹配 | `skill_invoke` 工具按需读正文，但不是「相似度匹配→自动载」 |
| | compaction 后重挂 | ❌ **compaction 根本没接** | `ContextCompressor` 接口存在但 `agent_loop.py` 无 `should_compress` 调用 |
| | 技能自创闭环 | ❌ **真缺口** | `SkillMemoryStore` 存在但注释明写「Phase F 才做」，全人工静态注册 |

---

## 2. 自查带来的 plan 调整（相对原 gap 分析）

**删掉 / 降级（已有，别做）：**
- ❌删 P1-3「五路混合检索」——`retriever.py`+`enhanced_retriever.py` 已 7 路 RRF，**完全做好**。
- ⬇️降 P0-3「半衰期衰减」——核心已有，只需补 **Pin 钉住** + 给 `PreferenceMemory`(JSON) 补衰减。

**升格 / 聚焦（高杠杆真缺口）：**
- ⬆️**`goal_store.py` 内存→持久化** 成为**第一块多米诺**：它同时卡住 P0-1（目标持久）、P0-2（verify 接 goal_text）、P0-3（goal/decision/constraint 记忆）。**先做它，后面三条都受益。**
- ⬆️**goal/decision/constraint 记忆抽取**（P0-3 最大功能缺口）与 durable goal store 协同——一个管「当前活跃目标」，一个管「历史决策/约束沉淀」。
- ⬆️**结构化自我纠错字段**（P0-2 真缺口）：把现有自由文本 selfcheck 升级成 `error_analysis/critique/replan` 必填字段，并让 verify 失败真正驱动重规划（而非 2 次文本 nudge）。

**确认是「接线」而非「新建」：**
- P1-4 compaction 没接 AgentLoop → skill 三级披露的「二级 embedding 匹配」「三级重挂」要先把 compaction 接上才有意义。
- verify gate 默认 off → 很多能力是「已建未点亮」，plan 要含「出厂默认值」决策（延续 DeskPet「字节级契约 + flag 渐进点亮」惯例）。

---

## 3. 关键洞察：缺口是「一条断掉的闭环」，不是 4 个独立功能

```
用户说目标 →【goal_store 持久化❌】→ 执行中【re-anchoring 注入❌防漂移】
   → 多 agent【handoff 带 goal❌】→ verify【接 goal_text 对照🟡】
   → 不过则【结构化 replan 重试❌】→ 完成后【goal/decision 沉淀进记忆❌】
   → 下次【人格画像主动注入🟡 + Pin❌】懂你
```
断点（❌/🟡）几乎全在「目标」这条线上穿不起来。**所以 plan 的主线不是「加 4 个功能」，
而是「把 goal 这条线从『一次性内存指令』打通成『持久化、抗漂移、可沉淀、可复用』的完成闭环」。**
