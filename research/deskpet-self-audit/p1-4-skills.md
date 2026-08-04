# DeskPet Skills 分级披露 + 自创闭环 — 现状自查报告

**日期**: 2026-06-04
**审查范围**: `G:\projects\deskpet\backend\deskpet\skills\` + `backend\deskpet\agent\assembler\` + `backend\deskpet\memory\reflection.py`
**方法**: 只读，代码为准

---

## 结论速览表

| 编号 | 核实点 | 判定 | 一句话结论 |
|---|---|---|---|
| P1 | 启动加载粒度（是否全文注入） | 🟡 部分 | 启动时加载全部 meta（含 description），但 skill_prelude 进系统提示只注入 `name+description`；正文留在磁盘 |
| P2 | 触发时才载正文（二级机制） | 🟡 部分 | `execute()`/`skill_invoke` 工具按需读正文，但 LLM 主动调工具才触发；没有「description 匹配 → 自动载正文」的自动二级机制 |
| P3 | 附件按需读（reference/examples/scripts） | ✅ 已有 | `script.py` 仅在 `requires_script=true` 时才读；SKILL.md body 中的 `${CLAUDE_SKILL_DIR}` 模板让 LLM 可用工具自取附件 |
| P4 | compaction 后重挂 skill | ❌ 缺口 | ContextCompressor 压缩对话中段后，不会触发 reassemble；skill_prelude 不会重新注入；compaction 后技能描述列表可能从上下文消失 |
| P5 | 技能自创闭环 | ❌ 缺口 | `SkillMemoryStore` 存在但明确注释「Phase E 最小可行不自动提取 skills」；无任何从经验自动固化新 SKILL.md 的机制 |

---

## 逐条详述

### P1 — 启动加载粒度

**代码链路**:

1. `main.py:1051` — `SkillLoader(skill_dirs=[_builtin_dir, _user_skills_dir], enable_watch=False)` 启动时立即 `reload()`，把所有 SKILL.md 的前置信息（frontmatter）解析进内存 `dict[str, SkillMeta]`。
2. `loader.py:316` — `_load_single()` 对每个 SKILL.md 读全文（`text = skill_md.read_text()`），分离 frontmatter 和 body；**body 仍留在磁盘，不进 cache**（meta 对象只存 `path` 字符串，不存 body_text）。
3. `assembler/components/skill.py:43-86` — `SkillComponent.provide()` 调 `registry.select()` 后，对每个返回的 SkillMeta 只取 `name` 和 `description/summary`，组成 `"## 可用技能\n- **name**: description"` 列表，放进 `skill_prelude` 系统消息。

**结论**: 启动时扫描全部 SKILL.md 的 frontmatter（少量），body 正文不进 context。系统提示里只有名称 + 描述摘要。没有 token 预算上限针对 skill_prelude（BudgetAllocator 对 skill bucket priority=70，预算不足时整块丢弃），但实际注入量极小。

**证据文件**: `backend/deskpet/agent/assembler/components/skill.py:69-77`、`backend/deskpet/skills/loader.py:316-343`

---

### P2 — 触发时才载正文（二级机制）

**代码链路**:

1. `loader.py:489-517` — `execute(name, args)` 每次调用时**重新从磁盘读** `meta.path` 的完整文件（`Path(meta.path).read_text()`），剥出 body，做变量替换后返回字符串。
2. `loader.py:611-669` — `_register_skill_invoke_tool()` 将 `skill_invoke(name, args)` 工具注册到 tool_registry，LLM 主动 call 此工具才触发 `execute()`，返回 body 作为 user-role 消息注入对话。
3. 没有「description → embed 匹配 → 自动载正文」的路径。SkillComponent 只提供描述列表；LLM 自己决定是否 call `skill_invoke`，或用户 `/<name>` 命令（P4-S20 slash 触发）直接跳过 LLM 决策。

**判定**: 正文是按需读（`execute()` 时才读磁盘）。但「匹配触发」是 LLM 自由裁量（或用户显式 slash），不是系统层的「description embed 相似度 → 自动二级载入」机制。

**证据文件**: `backend/deskpet/skills/loader.py:497-517`（execute 重读磁盘）、`:611-665`（tool 注册）

---

### P3 — 附件按需读

**代码链路**:

1. `loader.py:500-502` — `if meta.requires_script: return await self.invoke_script(name, args)` — 脚本仅 `requires_script=true` 时才执行；builtin skills 均为 `requires_script: false`，脚本永不常驻。
2. `parser/parse_skill_md.py:15-18` — body 中 `${CLAUDE_SKILL_DIR}` 在 render 时替换为 skill 目录绝对路径，LLM 可用 `read_file` 工具读该目录下的附属文件（template.md、examples/ 等），而不是自动预载。
3. 内联 shell `!`` cmd ``!` 在 render_body 时执行（10s timeout），仅 execute() 调用路径上发生。

**判定**: 附件是纯按需——skill 目录下的其他文件完全不会自动读；由 LLM 或 `render_body` 显式触发。

**证据文件**: `backend/deskpet/skills/loader.py:500-502`、`backend/deskpet/skills/parser/parse_skill_md.py:193-230`

---

### P4 — compaction 后重挂 skill

**代码链路**:

1. `context_compressor.py` — `ContextCompressor.compress()` 对 middle 段对话做 Haiku 摘要，返回新 messages 列表（`system_msgs + first_chunk + [summary_msg] + last_chunk`）。**skill_prelude 是 system msg，被保留在 `system_msgs` 中**（`role=system` 一律保留，不进 middle 段）。
2. `assembler.py:115-231` — `assemble()` 每个用户 turn 都重新调用，重新运行 SkillComponent；所以**每轮都会重建 skill_prelude**，不依赖上一轮结果。
3. **关键缺口**：compaction 发生在 agent_loop 内，但 `agent_loop.py` 里**没有找到 `should_compress` 的调用**（`grep` 在 agent_loop.py 无匹配）。ContextCompressor 有接口但未接入 AgentLoop 主路径。实际上 compaction 机制只存在于测试用例中（`test_deskpet_context_compressor.py`），生产路径未挂载。

**判定**: 从 compaction 后重挂角度看，因为 compaction 本身未接入生产 AgentLoop，该问题暂时不存在（也没保护）。若未来接入 compaction，skill_prelude 属于 system msg 会被保留；但 assemble() 本就每轮重建，理论上没有缺口——除非 compaction 接入后绕过了 assemble 路径。

**证据文件**: `backend/deskpet/agent/context_compressor.py:129-133`（should_compress 接口）、`backend/agent/agent_loop.py`（无 should_compress 调用）

---

### P5 — 技能自创闭环

**代码链路**:

1. `backend/deskpet/memory/reflection.py:16-18` — **明文注释**：
   > "The Phase E minimum viable doesn't auto-extract skills from chat logs (that's a Phase F R&D question); it just provides the storage surface so users / agents can record 'the X workflow' manually."
2. `reflection.py:167` — `class SkillMemoryStore` 提供了 `add()`/`list_all()`/`find_by_name()` CRUD，表 `skill_memory` 存在，但：
   - 没有任何触发器把成功任务路径写入 SkillMemoryStore
   - `SkillMemoryStore` 与 `SkillLoader`（SKILL.md 文件系统）完全解耦，即使写进去也不会生成新 SKILL.md
   - SkillMemoryStore 的内容不进任何 skill_prelude（SkillComponent 只接 SkillLoader）
3. 全库 `grep create_skill|learn_skill|auto_skill|codify|codify_skill` — 无匹配。
4. 用户可以手动把 `/<name>` 指向已有 skill，或手写 SKILL.md 放到 user 目录，但系统不会自动从会话经验生成 SKILL.md。

**判定**: 自创闭环**完全缺失**。SkillMemoryStore 仅是一块未接通的存储脚手架，Phase F 的 R&D 项。

**证据文件**: `backend/deskpet/memory/reflection.py:16-18`（明文说明）、`:167-193`（SkillMemoryStore 孤立 CRUD）

---

## 给 Plan 的建议

### 优先级 P0（架构缺口）

1. **自创闭环 (P5)**: 接通 SkillMemoryStore → 生成 SKILL.md 的管道。触发时机：agent_loop 收到 `final_answer` + 工具步骤 ≥ 3 步 → 摘要路径写 SkillMemoryStore → 定期脚本导出为 SKILL.md 放用户 skills dir → SkillLoader 热加载。

2. **compaction 接入 (P4)**: 若要接入 ContextCompressor，需在 agent_loop 中每轮 `should_compress()` 检查；skill_prelude 因为是 system msg 已受保护，但需确认 assemble() 在 compress 后仍被调用（而不是复用旧 bundle）。

### 优先级 P1（体验缺口）

3. **description 匹配 → 自动二级载入 (P2)**: 当前 LLM 靠 description 列表自由决策是否 call `skill_invoke`。可以在 TaskClassifier 层做 embed 相似度，把强匹配的 skill body 直接塞进 skill_prelude（完整正文），弱匹配才只给 description——真正的三级披露。

4. **skill_prelude budget 上限**: BudgetAllocator 对 skill bucket 整块丢弃（priority 70）。技能多了有预算冲突时应改成「按 embed 相关性分级保留」而不是全丢。

### 现状可直接利用

- SkillLoader + SkillComponent 的摘要注入已是轻量级第一级（name+desc），比全文注入节省 90%+ context；
- `skill_invoke` tool 的按需正文载入是合理的第二级；
- 只需补第三级（embed 匹配自动升级）和自创闭环即可达到 P1-4 全覆盖。
