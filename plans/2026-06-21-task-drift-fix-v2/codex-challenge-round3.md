# CODEX 挑战 Round 3 — 任务漂移 v2 实现 plan：终验是否 EXECUTABLE-AS-IS

你是 DeskPet（G:/projects/deskpet，master）的**严格对抗挑战者（第三轮·终验）**。R1+R2 发现已纳入 `plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`（见其 §9/§10）。这一轮目标：**判定是否已可被另一个 agent 100% 照着执行**，只挑**剩余**实质阻碍，不重复 R1/R2 已解决的。**只读，不改任何文件。**

## 用户硬要求
plan 必须 100% 可执行（行号/数据流准）、功能只能多做不能少做、真机相邻领域不再漂。

## 你必须做的终验（read-only 复核代码后逐项表态）
1. **§1 替换表是否现在真完整**：再 `grep -n "topic" backend/deskpet/tools/research_tools.py`，对照 01 §1 的 15 行表 + "不该改"清单——**还有没有第 16 处漏网的"当主题用"的 topic**？"不该改"清单有没有误把"该改的"列进去？
2. **§5 接入点是否现在真完整**：再 `grep -n "session_id\|_sid\|_msg_sid" backend/main.py backend/pipeline/voice_pipeline.py`，对照 01 §5 表——还有没有遗漏的"切 scope 后仍读/写旧 sid"的路径（如 L3 召回、goal_store、activity_store、artifact 落盘、`add_message`）？
3. **R2 新增项可行性**：(a) §5 `_broadcast_default_chat_peers` 改"group 语义"——具体怎么实现 group？会不会引入新复杂度/回归？给可落地改法或指出风险。(b) §8 auto-resume sentinel 采"禁止 sentinel 轮触发 deepresearch"——dispatch 处怎么判断"本轮是 sentinel"？`loop_user_request is None` 这个信号够不够（普通轮会不会也 None）？(c) §6 `/continue` 强制 `l2_page_in=always` 的透传链路（resolve→组装层）现实可行吗？
4. **新角落**：有没有 R1/R2 都没碰的会漂/会因切 scope 出问题的入口（cron/routine、code 模式 deepresearch、MCP 工具、artifact/receipt、记忆 facts 抽取按 sid）？
5. **测试矩阵**是否覆盖所有改点的正向+BC+边界。

## 输出
1. §1/§5 完整性终验结论（漏网项清单，若无则明说"完整"）。
2. R2 新增项可行性逐条表态（可落地/有风险+改法）。
3. 新发现的漏洞（按严重度；若无则明说）。
4. **收敛判定（必须明确）**：01 是否 **EXECUTABLE-AS-IS**？
   - 若**是**：明确说"可执行"，并给"实现时仅需注意的非阻塞提醒"（如有）。
   - 若**否**：列出"还差的最后 N 项"，具体到 file:line/改法。
严格、只挑实质、给到代码行级。只读（grep/read/pytest --collect-only，不改文件）。
