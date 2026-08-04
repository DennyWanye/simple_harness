# codex 作业 B2：实现 WI-5（触发式知识注入）

DeskPet 项目（仓库根 `G:\projects\deskpet`，已在此 cwd）。**严格按权威 plan 实现。**

## 第 0 步：读 plan + 摸现状
1. 打开 `plans/2026-06-20-agent-loop-optimization/00-PLAN.md`，读 **§6（WI-5）、§13.5（F1-F4）、§16（如涉及）**。
2. **关键前提（已核实）**：技能正文 inline **已实现**——`backend/deskpet/agent/assembler/components/skill.py` 的 `SkillComponent.provide()` 在触发命中时已调 `loader.read_body(name)` 把正文拼进上下文（约 skill.py:188-206），`backend/deskpet/skills/skill_matcher.py` 已有 trigger 关键词逻辑（`_TRIGGER_SIM=0.95`，命中即强匹配）。所以 WI-5 **不需要**再写 body inline 逻辑，**缩小为**：
   - (a) 新增 2-3 个**知识片段**（背景知识，非用户可调技能），让"用户消息含关键词→该知识块被注入上下文"。
   - (b) flag 门控（默认 off=BC）。
   - (c) compaction 保护：触发注入的知识 slice 标记为受保护/不可压（§13.5 F3）。
   - (d) 测试。

## 第 1 步：摸清并实现
先读懂这些再动手（grep + 读全文）：
- `backend/deskpet/skills/loader.py`（SkillLoader：从哪个目录加载 SKILL.md、frontmatter 字段、user-invocable / user_invocable 字段名）。
- `backend/deskpet/skills/skill_matcher.py`（trigger 匹配）。
- `backend/deskpet/agent/assembler/components/skill.py`（provide 怎么选 skill、怎么拼 body、Slice 的 meta 结构）。
- 找现有 SKILL.md 样例（grep `triggers:` 或 `SKILL.md`）学 frontmatter 格式。

然后：
1. **新增 2-3 个知识片段**（用现有 SKILL.md + frontmatter 格式，放在 loader 会加载的目录；若内置 skill 在 `backend/deskpet/skills/builtin/` 之类，仿照放）：
   - 每个带 `triggers: [关键词...]`、`user-invocable: false`（或该项目实际字段名，以代码为准）。
   - 例：`ppt-tips`（triggers: [ppt, 幻灯片, presentation]，正文写"生成 PPT 的注意事项：先列大纲再渲染、避免长标题竖排…"）；`windows-path-debug`（triggers: [路径, windows, 反斜杠]，正文写 Windows 路径调试要点）。内容简短即可。
2. **flag 门控**：加 `[skills] knowledge_enabled`（默认 false）。**在 skill 组件 / loader 层读**（用 config.raw 或该层已有的 config 访问方式），**不要改 `backend/main.py` 的 build_agent**（那是 WI-4 在改，避免冲突）。flag off → 不加载/不注入知识片段，行为与改动前一致。
3. **compaction 保护**：触发注入的知识 slice 在其 meta 标 `triggered=True`/`protected`；读 `backend/deskpet/agent/context_compressor.py` 的 `_partition`/`should_compress`，让被标记的 slice 不被压（或优先保）。若现有 system/frozen 保护机制已覆盖，复用即可，并在测试里验证。

## 第 2 步：测试
新建 `backend/tests/test_wi5_trigger_inject.py`：
- `test_trigger_keyword_injects_knowledge`：knowledge_enabled=on，注册一个 triggers:[ppt] 的知识片段，user_message 含 "ppt" → assemble/provide 产出的上下文含该正文。
- `test_unrelated_query_no_inject`：无关 query → 不注入该知识。
- `test_flag_off_bc`：knowledge_enabled=off → 不加载/不注入（行为与改动前一致）。
- （可选）`test_triggered_knowledge_protected_from_compaction`：被标记的知识 slice 不被 compressor 压掉。

跑：
```
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_wi5_trigger_inject.py -v
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_deskpet_context_assembler.py backend/tests/test_deskpet_skill_remount_after_compaction.py -q
```
改到新测试全绿 + BC 全绿。

## 约束
- **不要 `git commit` / `git add`**。**不要改 `backend/main.py` 和 `backend/agent/agent_loop.py`**（那是别的作业在改）。只动 skills 相关文件 + context_compressor（如需保护标记）+ 新测试。
- flag 默认 off 保证 BC。
- 完成输出：改了哪些文件、新增哪几个知识片段、测试结果。
- 若发现 WI-5 与现有 skill 自动注入机制高度重叠、(a)只需补片段+flag，则如实说明并最小实现，不要过度造轮子。
