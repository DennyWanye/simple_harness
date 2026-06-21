# CODEX 挑战 Round 2 — 任务漂移 v2 实现 plan：验证 100% 可执行 + 找剩余漏洞

你是 DeskPet 项目（G:/projects/deskpet，master）的**严格对抗挑战者（第二轮）**。R1 已把方案落成实现细节并被纳入。这一轮**只挑剩余漏洞 + 核验可执行性**，不要重复 R1 已解决的。**只读，不改任何文件。**

## 靶子
读 `plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`（含 file:line + diff 级改点 + 全替换表）。R1 的发现（§9 清单）已纳入。

## 用户硬要求（评判基准）
1. plan 必须 **100% 可被另一个 agent 照着执行**（行号/变量/数据流准确，改完不破坏现有）。
2. **功能只能多做，不可以少做**。
3. 功能要达预期（真机相邻领域不再漂）。

## 你这一轮必须核验的点（逐一表态，read-only 复核代码）

### A. §1 全替换表是否**完整**（最高优先）
- 亲自 `grep -n "topic" backend/deskpet/tools/research_tools.py`，把**所有"把 topic 当主题用"的位置**列出，对照 01 §1 的替换表——**有没有漏掉的 topic 用法**（导致改完仍有一处用漂 topic → 报告/slug/某 fallback 仍漂）？
- 改 `topic→request_topic` 会不会**破坏非主题用途的 topic**（如日志、metrics、cache key、去重 seen 集 `:327`）？哪些 topic 该保留原样？给"该改 vs 不该改"分类。
- `depth`/`brief`/`max_rounds`/`mode` 等参数链路会不会被波及？

### B. §5 T1-1 接入点是否**完整**（防遗漏路径用旧 sid）
- `grep -n "session_id\|_sid\|_msg_sid" backend/main.py backend/pipeline/voice_pipeline.py`，核验 01 列的接入点（5401/6767/6002/voice 493/616）**是否覆盖所有"会用到 sid 的路径"**——有没有遗漏的地方（如历史落库 `add_message`、L3 召回、goal store、activity store、artifact、广播 peers）仍用旧 `_sid` 导致切了 scope 但某处还写/读 default？
- "落库前切 sid"具体在 `main.py:5401~5465` 之间哪一行插最安全？会不会有更早的 append（如 5401 之前）？

### C. §4 voice 补 Fix B 是否正确
- 读 `voice_pipeline.py:600-620`，确认 `:616` 的 `loop.run(...)` 签名、`text` 变量在该作用域是否就是用户原话、补 `loop_user_request=text` 是否即生效。

### D. §6 T1-2 page-in 风险
- `l2_top_k=0` 会不会让某些依赖 L2 的逻辑（如 thinking-mode round-trip、reasoning_content 回填）出错？followup 用 `_starts_with_anaphora` 判断够不够（非代词同任务追问丢 L2 的补偿方案 plan 写了 /continue，但够具体吗）？

### E. 功能"只能多做"补漏复查
- 还有没有**会触发 deepresearch 或会漂移的其它入口**没覆盖（除了 control chat + voice，还有 cron/routine/auto-resume/code 模式/MCP 触发？）？
- 关 Tier2 + T1 之间的过渡期，普通回复漂移有没有兜底空窗？
- fanout 与会话切分的交互（plan §0 说 fanout 已隔离，但 T1-1 切 scope 后 fanout 的 parent_sid 用哪个）？

### F. 测试矩阵是否够
- §7 单测 + 真机是否覆盖所有改点的"正向 + BC + 边界"？漏了哪些必测项？

## 输出
1. **§1 替换表完整性核验**：grep 出的全部 topic 用法 vs 01 表格的差集（漏的/多改的/该保留的）。
2. **§5 接入点完整性核验**：遗漏的 sid 路径清单。
3. **C/D/E/F 逐项表态** + 发现的新漏洞（按严重度）。
4. **收敛判定**：01 是否已 **EXECUTABLE-AS-IS**？若否，列出"还差的最后 N 项"（要具体到 file:line/改法）；若是，明确说"可执行"。

严格、只挑实质问题、给到代码行级。只读（grep/read/pytest --collect-only，**不改文件**）。