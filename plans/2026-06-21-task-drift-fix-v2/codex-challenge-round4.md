# CODEX 挑战 Round 4 — 任务漂移 v2 实现 plan：终验（R3 四项已纳入）

你是 DeskPet（G:/projects/deskpet，master）的**严格对抗挑战者（第四轮·终验）**。R1+R2+R3 发现已全部纳入 `plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`（见 §9/§10/§11）。R3 的最后 4 项已修：§1 加 #16(`:1795/1799`→request_topic)、§5 group 落地细节(`_chat_peer_groups` 映射)、§8 sentinel 改显式 `is_sentinel_run` flag、§6 `/continue` 透传接口(`memory_policy_override`+`dataclasses.replace`)。**只读，不改任何文件。**

## 你的终验任务（read-only）
1. **核验 R3 四项是否真的修对**：逐条对照 01（§1#16、§5 group 段、§8 sentinel 段、§6 `/continue` 段）与实际代码——改法是否正确、行号是否准、有无副作用。
2. **§1 最后一次完整性扫描**：再 `grep -n "topic" backend/deskpet/tools/research_tools.py`，确认 16 行替换表 + "不该改"清单**已覆盖所有"当主题用"的 topic**，无第 17 处漏网。
3. **是否还有任何 100% 可执行的实质阻碍**（行号错/数据流断/会破坏现有/功能漏做）。

## 收敛判定（必须明确二选一）
- **EXECUTABLE-AS-IS**：明确说"可执行"，列出实现时的非阻塞提醒（如有）。
- **否**：列出"还差的最后 N 项"，具体到 file:line/改法。

只挑剩余实质问题，不重复已解决的。严格、给到代码行级。只读（grep/read，不改文件）。
