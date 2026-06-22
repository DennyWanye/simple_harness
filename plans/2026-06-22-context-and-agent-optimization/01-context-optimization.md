# 01 — 上下文管理优化（方向一：1A + 1B 展开）

> 父文档：[00-PLAN.md](./00-PLAN.md) §2「方向一」。本文档把 1A（Claude Code 工作环境）与 1B（DeskPet 运行时 compaction）逐项展开到「可直接照做 / 可直接落代码」粒度。
> 调研基线：读码核实（master，2026-06-22）。所有「文件:行号」均为**实际读到的真行号**，未核实处明确标 ⚠️。
> 铁律：1B 全部新增/改动 flag 出厂 OFF（或沿用既有默认）+ 字节级 BC；1A 是工作环境配置，不是 DeskPet 代码改动。

---

## 前置事实校准（已被 git / 读码核实，本文档不推翻）

- **P-B 已修**（2026-06-16 commit `84e4c251`）：压缩窗口已按真实出站模型解析。证据链：
  - `backend/config.py:815` `effective_llm_model(cfg)` / `:840` `effective_llm_model_standalone()` —— 解析有效出站模型。
  - `backend/llm/model_info.py:222` `resolve(model, project_root)` 三层解析 → `ModelContextInfo`（`context_window` / `effective_pct` / `compact_at_pct`）。
  - `backend/agent/context_manager.py:264` `ContextManager.for_session(model=..., project_root=...)` 把 resolve 出的 `ModelContextInfo` 注入 `ContextConfig`。
  - 所以 1B 全是**精修项，不是修 bug**。
- **`compaction_enabled` 默认 True**：`backend/config.py:435`（`FeaturesConfig.compaction_enabled: bool = True`，注释 `:427-434` 说明 WI-6 2026-06-16 翻 True）。
- **WI-1~6（2026-06-16 compaction 升级）已完成**：触发改剩余 buffer（`context_compressor.py:176 trigger_tokens`）/ microcompact（`:664 _microcompact_tool_results`）/ 7 段结构化摘要（`:102 _SUMMARY_SYSTEM`）/ 目标 always-on（`agent_loop.py:683`）/ pre-flush L1（`agent_loop.py:906-936`）。

---

## token 计数现状审计（1B-1 的依据，逐点读码核实）

**统一入口**：`backend/deskpet/agent/tokens.py`
- `count_text_tokens(text)` —— `tokens.py:62`（CJK-aware，可选 tiktoken，启发式回落 `_weighted_chars//4`，`:72`）。
- `count_messages_tokens(messages)` —— `tokens.py:99`。
- `_weighted_chars(s)` —— `tokens.py:51`（CJK×4，ASCII×8/7）。

**已收敛（已走统一入口，无需改）**：
| 文件:行 | 现状 | 状态 |
|---|---|---|
| `backend/agent/token_budget.py:152` `estimate_tokens` | 委托 `count_messages_tokens` | ✅ 已收敛 |
| `backend/deskpet/agent/assembler/budget.py:169` `_count_tokens` | 委托 `count_text_tokens` | ✅ 已收敛 |
| `backend/deskpet/agent/context_compressor.py:700` `_approx_tokens` | 委托 `count_text_tokens` | ✅ 已收敛（仍叫 `_approx_tokens`，但内部已是统一口径，`:322`/`:388` 调用点 OK） |
| `backend/deskpet/agent/assembler/components/persona.py:127` `_approx_tokens` | 委托 `count_text_tokens` | ✅ 已收敛 |
| `backend/deskpet/agent/assembler/components/memory.py:438` `_approx_tokens` | 委托 `count_text_tokens`（注意 `:446-448` 有重复 `if not text` 死代码） | ✅ 已收敛（有微瑕） |
| `backend/deskpet/agent/assembler/components/preference_profile.py:42` `_approx_tokens` | 委托 `count_text_tokens` | ✅ 已收敛 |

**❗ 仍是裸估算、未走统一口径（1B-1 真正要收的点）—— 复核后清单（2026-06-22 逐行读码）**：

> ⚠️ **修正之前的「仅 2 处」误判**：上轮只点了 `main.py` + `metrics` 两处，复核后发现 ① `main.py` 的 `/3.5` 实际是 **3 处**（persona/memory/history 各一），② 还漏了 **skill.py 的 `len//_CHARS_PER_TOKEN`（2–3 处）**。真正残留是 **6 处**（不含已收敛但口径仍 `//4` 的存量）。下表为全口径候选清单，已标注「裸残留 / 已收敛」状态，避免再误判。

| 文件:行 | 在算什么 | 现状口径 | 状态 |
|---|---|---|---|
| `backend/main.py:3520` `_approx_tokens` | context 环 persona / 总览 | `len/3.5` | ❗ 裸残留 |
| `backend/main.py:3579` | memory 块（`mem_total_chars`） | `mem_total_chars/3.5` | ❗ 裸残留 |
| `backend/main.py:3630` | history（`total_chars`） | `total_chars/3.5` | ❗ 裸残留 |
| `backend/deskpet/agent/assembler/components/skill.py:221,261`（另 `:164`） | skill 正文 / slice | `len//_CHARS_PER_TOKEN`(=`//4`) | ❗ 裸残留 |
| `backend/deskpet/memory/eval/metrics.py:267` | memory eval block（离线指标） | `len//4` | ❗ 裸残留（次要，非热路径） |
| `backend/deskpet/agent/context_compressor.py:700` `_approx_tokens`（`:322`/`:388` 调用） | 压缩输入/输出计费 | 委托 `count_text_tokens` | ✅ 已收敛 |
| `backend/deskpet/agent/assembler/components/persona.py:127` `_approx_tokens` | persona slice | 委托 `count_text_tokens` | ✅ 已收敛 |
| `backend/deskpet/agent/assembler/components/memory.py:438` `_approx_tokens` | memory slice | 委托 `count_text_tokens` | ✅ 已收敛（`:446-448` 有重复 `if not text` 死代码） |
| `backend/deskpet/agent/assembler/components/preference_profile.py:42` `_approx_tokens` | preference slice | 委托 `count_text_tokens` | ✅ 已收敛 |
| `backend/agent/token_budget.py` `estimate_tokens`（`:162-163`） | budget heuristic | 委托 `count_messages_tokens` | ✅ 已收敛（仅 `:21` docstring 还写「`len/4` heuristic」，需同步改文案） |

> **关键事实（口径不一致已是既成事实，不是「都低估」那么简单）**：统一入口 `tokens.py` 用 `_weighted_chars//4`（CJK×4 加权），但 `main.py` 三处用 `len/3.5`、`skill.py`/`metrics` 用 `len//4` —— **三套口径并存**。即使是已收敛点也是 `//4` 加权口径，与 main.py 的 `/3.5` 仍不一致。所以 1B-1 不是「补 2 个漏」，而是**统一口径 + 收尾 6 处裸残留**，工作量从 S 上调到 **M**。

---

# 1A — Claude Code 工作环境 context 优化（回答「圈圈一直满」）

> 这部分**不改 DeskPet 代码**，是优化用户当前敲命令的这个 Claude Code IDE 的 context 环。

## 1A 诊断（已量化，文件系统核实）

实测固定开销（`wc -m`，2026-06-22）：
- 全局 `~/.claude/CLAUDE.md` = **10,858 字符**（≈2.7–3.5K token）
- 项目 `G:\projects\deskpet\CLAUDE.md` = **11,440 字符**（≈2.8–3.6K token）
- `MEMORY.md` = **4,956 字符** + memory 目录共 **27 个条目文件**（recall 时按需注入，但 MEMORY.md 索引本身常驻）
- **最大头（无法 `wc` 但从本会话系统提示可见）**：deferred 工具目录（~250 个工具名，含 Blender 全家桶 ~30、computer-use ~30、windows-mcp ~25、design 插件多组）+ skill 目录（~150 个，含大量 `ecc-*` / `geek-*` / `sp-*` / `anthropic-skills:*`）+ 多个 MCP 服务器的整段使用说明（Blender / computer-use / windows-mcp 各一大段）。

**核心机理（必须讲给用户）**：auto-compact **只压缩对话消息历史**，它**压不动**系统提示、工具/skill 目录、CLAUDE.md/MEMORY.md 这些**固定开销**。所以一开**新会话**圈圈就接近满 ≠ auto-compact 失灵，而是固定开销基数太大。要降圈圈基线，只能**减少固定注入**，不是调 compact。

---

### WI-1A-1 CLAUDE.md 瘦身（全局 + 项目）

**目标节省**：全局 + 项目两份合计 ~22.3K 字符 → 目标砍到 ~9–11K 字符，**省 ~11–13K 字符 ≈ 2.8–3.3K token / 每会话常驻**。

**操作步骤（用户照做）**：
1. 把以下「低频、查得到、参考型」大段从 `~/.claude/CLAUDE.md` **抽走**，迁到一个按需加载的参考文件（如 `~/.claude/knowledge-base/windows-mcp-e2e.md`），主文件只留「触发场景一句话 + 指向参考文件的路径」：
   - **SendInput 圣杯 csharp 片段**（`~/.claude/CLAUDE.md` 中「🛠 已知技术陷阱与 workaround」表下方那段 ~15 行 C# 代码）→ 抽到参考文件。主文件保留：「WebView2 要用 SendInput 而非 mouse_event；完整片段见 knowledge-base/windows-mcp-e2e.md」。
   - **codex 可用模型矩阵表**（`gpt-5.5/5.4/5.4-mini/...` 那张 6 行表 + config 注意段）→ 抽到 `~/.claude/knowledge-base/codex-usage.md`。主文件保留：「写代码子代理默认 `codex exec -m gpt-5.5`；模型矩阵 + config 见 knowledge-base/codex-usage.md」。
   - **「🔒 GUI / 手工测试纪律」整段**（含 ❌ 禁止 6 条 + ✅ 要求 5 条 + 障碍表 + 报告格式 + 自我警觉）—— 这是**铁律不能丢语义**，但可把「障碍表 / SendInput 片段 / 报告格式模板」抽到参考文件，主文件只留：触发词列表 + 「禁止用协议层/脚本/import 当 UI 证据」+ 「每个 case 必须真截图真点击」3 条硬核 + 指向参考文件。
2. 项目 `G:\projects\deskpet\CLAUDE.md` 同法：把「📁 仓库分支与 worktree 拓扑」「端口隔离表」「🧪 跑 last-mile 验收命令块」「📚 关键文档清单」这些**查询型**内容抽到 `plans/` 下已有文档或 `STATUS/`，主文件用一行链接代替。
   - ⚠️ **不能删**：`✅ STATUS 更新纪律`、`🔑 开发期登录测试账号`安全约束、`🚨 项目特有踩过的坑`（尤其坑#7/#8/#9 端口与 backend_launch，这几条是高频救命）。这些保留全文。

**注意事项 / 铁律不能删**：
- 中文优先、MCP 优先、先规划再编码、质量门控、安全护栏（rm -rf / push --force / .env）、手测纪律的**触发词与禁止项**—— 这些是行为约束，删了会让 agent 走捷径，**只能搬细节、不能删约束本身**。
- 抽走的内容要确保 agent 能「按需找回」：在主文件留下确切路径，否则等于丢失。
- **落仓库成可复用资产（决策②方案B，2026-06-22）**：抽出的长段**规范化成按需加载的可复用资产**（落 `~/.claude/knowledge-base/` 或参考 skill，如 `windows-mcp-e2e.md` / `codex-usage.md`），CLAUDE.md 只留一行指针指向该资产；**不只给一次性 SOP**。这样多个会话/项目可复用同一份知识，而非每次重抽。

---

### WI-1A-2 裁剪未用 MCP 服务器 / 插件

**目标节省**：工具目录从 ~250 → 砍到 DeskPet 日常实际用的子集（windows-mcp + computer-use + 少量），**省 ~100–180 个工具名 + 多段 MCP 说明 ≈ 视目录密度，粗估 3–8K token**（工具目录是本会话最大头之一）。

**操作步骤（用户照做）**：
1. 审 `~/.claude/settings.json` / `~/.claude.json` 里的 `mcpServers`，把**当前项目用不到**的服务器临时禁用或移到按需启用：
   - **Blender 全家桶**（`mcp__Blender__*` ~30 个 + `mcp__blender__*` 另一组 ~25 个）—— DeskPet 后端开发**几乎不用**，建议禁用（做 Live2D/3D 资产时再开）。
   - `mcp__Claude_in_Chrome__*`（~25 个）、`mcp__plugin_design_*`（asana/atlassian/figma/intercom/linear/notion/slack 各 2 个）、`mcp__scheduled-tasks__*`、`mcp__Blender__*_for_cli` —— 与 DeskPet 后端开发无关，禁用。
   - **保留**：`windows-mcp`（真机 E2E 命脉）、`computer-use`（备选 GUI）、`context7` / `exa` / `mcp-registry`（MCP 优先搜索铁律要用）。
2. 审 skill 目录：`ecc-*`（~30 个）、`geek-*`（~15 个，A股/高考/天气等与本项目无关）、`design:*` / `stitch-*` / `anthropic-skills:*` 中用不到的——若 harness 支持按 plugin 启停，关掉不相关 plugin 包。
   - ⚠️ **保留**：`sp-*`（codingsys 多 agent / goal / 验证体系）、`deep-research`、`openspec-oneshot` / `opsx:*`、`spec-first` / `auto-verify` / `coding-harness`（全局规范点名要用的）。

**注意事项**：
- 工具/skill 目录是 deferred（按名注入、schema 才 ToolSearch 拉），但**名字本身也占常驻预算**。砍服务器 = 砍掉整批名字 + 整段服务器说明，杠杆最高。
- 禁用是**配置层**（settings.json），随时可恢复；不是删插件文件。改完**重启会话**才生效。

---

### WI-1A-3 配置 auto-compact + 手动 `/compact` SOP

**目标节省**：不直接降基线，但避免「对话历史滚到撑爆触发昂贵自动压缩」+ 在阶段切换点主动压缩保关键上下文。

**操作步骤（用户照做）**：
1. 确认 auto-compact 开关与阈值：Claude Code 的 auto-compact 默认开启，阈值在设置里（`/config` 查看）。**保持开启**——它压历史是净收益。
2. **手动 `/compact` SOP**（关键，弥补 auto-compact 只在快满时才触发的滞后）：
   - **阶段切换点**主动 `/compact`：探索→实现、实现→验收、一个 plan 收尾转下一个 plan 时。
   - **长调试/长 E2E 跑完后**：截图/日志已抓完、结论已得出，立即 `/compact <一句话保留：当前在做 X，已验证 Y，下一步 Z>`，把冗长 tool_result 历史压掉但锚住任务态。
   - **切换大任务前**：先 `/compact` 再开新主题，避免旧任务残留漂移。
   - 也可考虑 superpowers 的 `ecc-strategic-compact` / `handoff` skill 在逻辑断点做结构化压缩。
3. 若圈圈仍长期偏高且固定开销已瘦身到位 → 考虑**新开会话**而非续压（固定开销不可压，新会话 = 重置对话历史增量）。

**注意事项**：
- `/compact` 会丢失被压区的细节，SOP 里那句「保留 X/Y/Z」很重要——别裸 `/compact`。
- auto-compact 不能替代 1A-1/1A-2：**它压不动固定开销**，圈圈基线靠瘦身降。

---

### WI-1A-4 MEMORY.md / memory 文件治理

**目标节省**：MEMORY.md 索引 ~4,956 字符 + 27 条目。合并冗余、删过时，降 recall 注入量 + 索引常驻量，**粗估省 1–2K 字符 + 减少召回时无关条目注入**。

**操作步骤（用户照做）**：
1. 审 27 个 memory 文件，**合并同主题**：
   - `feedback_real_test.md` / `feedback_manual_e2e_check.md` / `feedback_simulate_manual_test.md` / `feedback_real_e2e_not_script_replay.md` —— 4 条都是「真测 ≠ 脚本回放」同一主题，合并成 1 条。
   - `feedback_bash_cwd_venv.md` / `feedback_powershell_chinese_files.md` / `feedback_toolcall_serialization_corruption.md` —— 都是「工具调用稳健性」，可归一组。
2. **删过时/已结案**：`project_goal_completion_upgrade.md`（已注「当日全部结案」）、`project_backend_orphan_fix.md` / `project_self_update_pipeline.md`（已 ship）等若已沉淀进代码/STATUS，可降级或删。
3. 用 `anthropic-skills:consolidate-memory` skill 做半自动归并。
4. MEMORY.md 索引行：删掉指向已删条目的行，保持一行一主题。

**注意事项 / 不能删**：
- `reference_dev_test_credentials.md`（dev 账号位置）、`feedback_no_sandbox_constraints.md`（不加沙箱）、`feedback_deskpet_branch_strategy.md`（master 开发）、`feedback_commit_untracked_immediately.md`（防沙箱回滚）—— 这些是**高频踩坑/项目铁律**，保留。
- memory 是「召回时注入」，治理收益主要在**召回精度**（少注无关条目）+ 索引常驻量，不是最大头；优先级低于 1A-1/1A-2。

---

# 1B — DeskPet 运行时上下文/compaction 精修

> 全部 flag 出厂 OFF（或沿用既有默认 + 新增项默认不改变行为）+ 字节级 BC。

---

#### WI-1B-1 token 计数 scatter 收敛  [**不挂 flag**（决策③方案B，2026-06-22）：直接统一到 count_text_tokens（中文友好口径）；非字节 BC，靠强回归 + 真机验收兜底 | 优先级 P0 | 工作量 M]

- **现状**：统一入口 `backend/deskpet/agent/tokens.py:62 count_text_tokens` / `:99 count_messages_tokens` 已存在。复核后（见上「token 计数现状审计」表）**残留 6 处裸估算**，分布在三套口径：
  - **`main.py` 三处 `/3.5`**：`:3520` `_approx_tokens`（persona/总览）、`:3579`（`mem_total_chars/3.5`，memory 块）、`:3630`（`total_chars/3.5`，history）。均喂 `_compute_context_breakdown`（`main.py:3523`，调用点 `:3552` 等），驱动前端「context 占用饼图 / 圈圈」。`/3.5` 对 CJK 低估 ~3–4 倍。
  - **`skill.py` 两处（另 `:164`）`len//_CHARS_PER_TOKEN`**（`_CHARS_PER_TOKEN=4`，`:38`）：`:221` skill 正文、`:261` skill slice。裸 `//4`，CJK 低估。
  - **`metrics.py:267` `len(block)//4`**：仅离线 memory eval 指标（`WI-M0.2`/PRD D11 口径），非运行时热路径，影响小但口径不一致。
  - （已收敛、本 WI 不动：context_compressor.py:700 / persona.py:127 / memory.py:438 / preference_profile.py:42 / token_budget.py estimate_tokens —— 这些已委托 `tokens.py`，但内部仍是 `//4` 加权口径。）
- **问题**：**三套口径并存**（`tokens.py` 的 `_weighted_chars//4` vs `main.py` 的 `/3.5` vs `skill/metrics` 的裸 `//4`），收敛不彻底。`main.py` 三处尤其关键——让**前端圈圈数字**与真实 token 占用偏差大（中文会话尤甚），既误导用户「圈圈满不满」，又与 compaction 触发口径（`tokens.count_messages_tokens`）打架。
- **改法（分两批，按 BC 性质拆 —— 决策③方案B：两批都直接改，不挂 flag）**：
  - **批 1 —— 纯口径统一到 `tokens.count_text_tokens`（会改数字：`/3.5`→中文加权口径，行为会变）→ 直接改 + 强回归测试 + 真机 windows-mcp 确认压缩时机变化无害**：
    - `main.py:3520` `_approx_tokens` 委托统一入口（与 persona.py/memory.py 同模式）；`:3579`/`:3630` 两处把 `mem_total_chars/3.5`、`total_chars/3.5` 改为对各自文本调 `count_text_tokens`（注意 `:3579` 现在按累加 char 数算，需改成对拼好的 mem 文本调用，或对各 cat/subj/val 分别 count 再求和）。
    - `skill.py:221,261`（及 `:164`）把 `len(...)//_CHARS_PER_TOKEN` 改为 `count_text_tokens(...)`；`:218` 的 `max_chars = per_skill_max_tokens * _CHARS_PER_TOKEN` 是反向截断换算，保留 `_CHARS_PER_TOKEN` 做粗略 char 预算即可（截断点会随口径变，见 BC 警示）。
    - `metrics.py:267` 改 `from deskpet.agent.tokens import count_text_tokens; return max(1, count_text_tokens(block))`；同步改 `:243` docstring「1 token ≈ 4 chars」→「委托 tokens.count_text_tokens（CJK-aware）」。
    - **不挂 flag**（决策③方案B）：直接统一到 `count_text_tokens`，不留 `unified_token_count` 开关。非字节级 BC，安全网 = 强回归测试（断言 CJK 不再低估）+ 真机 windows-mcp 验收「压缩触发时机变化无害」。
    - 推荐委托模板（main.py，签名不变 `text: str | None`，调用点零改动）：
      ```python
      def _approx_tokens(text: str | None) -> int:
          if not text:
              return 0
          from deskpet.agent.tokens import count_text_tokens
          return count_text_tokens(text)        # 直接统一口径（决策③方案B，不挂 flag）
      ```
  - **批 2 —— 纯等价重构（同口径 `//4`→`//4` 合并，本就无行为变化）→ 直接做**：把 `skill.py`/`metrics.py` 中尚未并入批1、仍是裸 `len//4` magic number 的散落点收口到单一 helper（仍 `//4`，数值与旧值一致），消除散落。此批本就无行为变化，直接做。
    - ⚠️ 注意：批 2 仅指「不引入 CJK 加权、纯合并 magic number」的等价重构；一旦换成 `count_text_tokens`（含 CJK×4 加权）就属批 1（行为变）。两批不要混。
  - 顺手清 `memory.py:446-448` 的重复 `if not text` 死代码（非功能，可选）。
- **BC 警示（关键）**：**统一口径会改变中文会话的 token 估值数字**（CJK 上升到真实值）→ 这会**影响压缩触发时机**（breakdown 估值变大可能提前/推迟触发判断）、**改变 skill slice 的截断点**（`per_skill_max_tokens` 换算后实际放进的字数变少）。这**不是字节级 BC**。处置（决策③方案B，2026-06-22）：
  - ① **不挂 flag**：批 1 直接统一到 `count_text_tokens`，不留 `unified_token_count` 开关。数字变准是正收益（权威值仍是 relay usage）。
  - ② 非字节级 BC 的安全网 = **强回归测试**（断言 OFF/ON 等价路线取消后，CJK 文本估值接近真实、不再 `/3.5` 低估）+ **真机 windows-mcp 验收**「压缩触发时机变化无害」。批 2 是纯等价重构（`//4`→`//4`），本就字节 BC。
  - ③ 函数签名/调用点不动；权威值始终是 relay 的 `usage.input_tokens`（圈圈/breakdown 只是显示估值）。
- **测试点**：
  - 单测文件 `backend/tests/test_agent_tokens.py`（已存在，`:17` CJK 不可低估、`:38` 断言 compressor `_approx_tokens`）→ 新增**强回归** `test_main_approx_tokens_cjk_aware`（统一后：`main._approx_tokens` 对中文文本接近字符数，不再 `len/3.5` 低估）。
  - 新增**强回归** `test_skill_tokens_cjk_aware` / `test_metrics_l3_tokens_cjk_aware`：断言 skill/metrics 路径中文不再低估到 1/4。（不挂 flag，故无 OFF/ON 双路对照，直接断言统一后口径正确。）
  - 真机 windows-mcp（统一后）：开桌宠 → 中文对话几轮 → 点圈圈 gauge 打开 ContextBreakdownModal → 截图核对「memory/system」段 token 数 vs `last_usage_prompt_tokens` 不再出现「估值远小于真实」的离谱偏差，且压缩触发时机变化无害。
- **依赖**：无。

---

#### WI-1B-2 压缩可观测性增强  [flag: ctx_observability 默认 False | 优先级 P0]

- **现状**：压缩命中已落两条结构化日志：
  - `backend/deskpet/agent/context_compressor.py:422` `logger.info("context_compacted", middle_tokens_in=, summary_tokens_out=, reduction=, summarized_msgs=, kept_head=, kept_tail=, model=, window=, threshold_pct=, summary_preview=)` —— 字段已相当全。
  - `backend/agent/agent_loop.py:950` `logger.info("p1_4_compaction_fired sid= tid= iter= reduction=")`。
  - microcompact-only 路径 `context_compressor.py:237` `logger.info("context_microcompact_only", tool_results_pruned=, window=)`。
  - 前端「圈圈」饼图由 `main.py:3523 _compute_context_breakdown` 驱动（独立估值，非压缩事件）。
- **问题**：①日志只进 stderr→tauri dev log，**前端无可视化**「这次压了多少 / 何时压的」；②`context_compacted` 与前端 breakdown 是**两套口径**（前者真实压缩、后者估值），用户看不到「刚刚压缩省了 X token」的反馈；③无聚合 metrics（多次压缩的累计节省）。
- **改法**：
  - 复用已有 metrics 事件通道（已核实：metrics endpoint = `backend/main.py:4137 post_metrics_event`（`/metrics/event`）+ `:4171 metrics`；compressor 内复用该 endpoint 的 sink，或最简方案只落 structlog 不走 endpoint）。在 `context_compressor.py:422` 落 `context_compacted` 日志的**同一处**，可选 emit 一条 metrics 事件（flag `ctx_observability` ON 时）：`{event:"context_compacted", reduction, in, out, model, ts}`。
  - 在 `agent_loop.py` 压缩成功分支（`:941` `if getattr(_cresult,"compressed",...)`）后，flag ON 时 yield 一个**新的轻量 AgentEvent**（如 `ContextCompactedEvent`，字段 = reduction/in/out），由 `main.py` 事件转发层（`STATUS/AgentLoop.md §7`，`async for ev in _agent.run`）转成 WS 消息 → 前端在圈圈附近浮一条 toast「已压缩，省 N token」。
  - flag OFF：不 emit metrics、不 yield 新事件，仅保留现有 `logger.info`（= 现状字节一致）。
- **BC 保证**：OFF 时 §422/§237/§950 的 `logger.info` 不变、不新增任何 yield 事件、不调 metrics endpoint → 与现状字节一致。新 AgentEvent 类型只在 flag ON 路径构造，OFF 路径不 import/不实例化。
- **测试点**：
  - 单测 `backend/tests/test_compaction_bestpractice_upgrade.py`（已存在）→ 新增 `test_observability_off_no_extra_event`（flag OFF：run 一轮触发压缩，断言事件流里无 `ContextCompactedEvent`、无 metrics 调用）+ `test_observability_on_emits_event`（ON：断言 yield 出该事件且字段 = reduction/in/out）。
  - 真机 windows-mcp：flag ON → 桌宠跑一个长 agentic 任务（多工具）触发压缩 → 截图前端 toast「已压缩省 N token」+ grep tauri dev log 的 `context_compacted` 对账数字一致。
- **依赖**：无（但若复用 metrics endpoint，需先核实其 emit helper 名 ⚠️）。

---

#### WI-1B-3 自适应 compact_at_pct（按任务性质微调）  [flag: adaptive_compact_pct 默认 False | 优先级 P3]

- **现状**：`compact_at_pct` 是 **per-model 固定值**：`backend/llm/model_info.py:90+` BUILTIN 表逐模型写死（gpt-5.5=0.80 `:94`、deepseek-v4-pro=0.75 `:102`、claude-sonnet=0.83 `:110`、_default=0.80 `:144`）。`context_manager.py:169 compact_at_tokens` = `window × compact_at_pct`（`:178`）。不随单次会话的「任务性质」变。
- **问题**：纯对话（companion 闲聊）和多工具 agentic 任务对「何时该压」需求不同——agentic 任务 tool_result 噪声多、值钱内容稀，可更早压（更低 pct）；纯对话内容密、压缩损失大，可更晚压（更高 pct）。固定值是折中，两头不最优。
- **改法**：
  - 在 `ContextConfig`（`context_manager.py:99`）加字段 `adaptive_compact_pct: bool = False` + `agentic_pct_delta: float = -0.05` / `chat_pct_delta: float = +0.03`（默认值在 OFF 时不读）。
  - 把 `compact_at_tokens`（`:169`）的 `@property` 改为可接收一个「最近 N 轮是否多工具」的信号：新增方法 `compact_at_tokens_for(agentic: bool) -> int`，OFF → 等价旧 `compact_at_tokens`；ON → `window × clamp(compact_at_pct + (agentic_delta if agentic else chat_delta), 0.6, 0.95)`。
  - agentic 判定信号源：agent_loop 已有 tool_path 记录 / 本 run 工具调用计数（`STATUS/AgentLoop.md §6c` tool_path 录制）。在 `agent_loop.py:888` 读 `compact_at_tokens` 处改读 `compact_at_tokens_for(agentic=<本run工具数≥阈值>)`（仅 flag ON）。
- **BC 保证**：OFF → `adaptive_compact_pct=False` → `compact_at_tokens_for` 直接 `return self.compact_at_tokens`（原 property 不动），`agent_loop.py` 也走原 `getattr(_ctx_cfg,"compact_at_tokens")` 分支 → 字节一致。
- **测试点**：
  - 单测 `backend/tests/test_token_budget_per_model.py`（已有 per-model 阈值测试）→ 新增 `test_adaptive_pct_off_equals_fixed`（OFF：`compact_at_tokens_for(True)==compact_at_tokens_for(False)==compact_at_tokens`）+ `test_adaptive_pct_on_agentic_lower`（ON：agentic 阈值 < 纯对话阈值，且都 clamp 在 [0.6,0.95]×window）。
  - 真机 windows-mcp：flag ON → 同一会话先纯对话（观察晚压）再切多工具任务（观察早压），grep `context_compacted` 的触发 token 水位差异。
- **依赖**：无（建议在 1B-2 可观测落地后做，便于真机验证阈值差异）。

---

#### WI-1B-4 摘要质量回路（检测「刚才在干嘛」→ 重摘）  [flag: summary_quality_loop 默认 False | 优先级 P3]

- **现状**：摘要质量护栏只有**反射检测**（`context_compressor.py:621 _looks_reflective` + `:378` 落地闸 + `:632 _extract_prior_summary` 不把反射当 prior）。无「用户事后表达困惑 → 判摘要丢了关键 → 重摘」的回路。
- **问题**：摘要可能丢了用户在意的上下文（haiku 偶发漏），用户下一句问「我们刚才在弄什么 / 你忘了我说的 X 了吗」时，系统无感知、不补救。
- **改法**：
  - 新建轻量检测：在 `main.py` 用户消息预处理链（`STATUS/AgentLoop.md §7 _run_chat`）里，flag ON 时对用户消息跑一个**词法**信号匹配（如 `刚才|之前说的|你忘了|我们在弄|上一个`），命中且**本 session 最近发生过压缩**（读 `context_compacted` 落的 latch / session 标记）→ 标记 `summary_quality_suspect=True`。
  - 命中后的补救（二选一，建议轻量版）：从 ref-store / L1 file_memory（pre-flush 写的任务态快照，`agent_loop.py:925`）把任务态重新注入一条 system 提示，**不**立刻重跑昂贵的整段重摘（重摘版可作 P3+ 增量）。
  - flag OFF → 不跑检测、不注入。
- **BC 保证**：OFF → 预处理链不加任何分支（`if not summary_quality_loop: 原路`）→ 字节一致。检测与补救代码只在 flag ON 闭包内。
- **测试点**：
  - 单测（新文件 `backend/tests/test_summary_quality_loop.py`）：`test_off_no_detection`（OFF：困惑措辞不触发任何注入）+ `test_on_detects_and_reinjects`（ON：发生过压缩 + 用户问「刚才在干嘛」→ 断言注入了任务态 system 消息，内容含 pre-flush 快照）。
  - ⚠️ **真机难自动化**：需人造「压缩后困惑」场景。真机 windows-mcp：长任务触发压缩 → 人工问「我们刚才在做什么」→ 截图桌宠回答是否准确召回任务态。
- **依赖**：依赖 WI-4b pre-flush（已存在，`agent_loop.py:906-936`）写 L1 任务态作为补救数据源。

---

#### WI-1B-5 microcompact 触发精修（启发式只清旧轮）  [flag: 沿用 microcompact_keep_tools（已存在）| 优先级 P3]

- **现状**：microcompact 已是「保护最近 N 个 tool_result、清更早的」启发式——**不是**全局清。证据：`context_compressor.py:664 _microcompact_tool_results(messages, keep_recent_tools)`，`:681` `protected = set(tool_idxs[-keep_recent_tools:])`，只对 `i not in protected` 的旧 tool 换占位（`:684-694`）。`keep_recent_tools` 由 `ContextCompressor.__init__` 的 `microcompact_keep_tools=3`（`:135`/`:151`）控制。
- **问题**：00-PLAN.md 把此项描述为「从『全局清旧 tool_result』改启发式」——但**读码发现现状已经是按『最近 N 个』的启发式**，所以此 WI 不是「改架构」，而是**精修保护策略**：当前只按「tool 消息条数」保护最近 N 个，未考虑①最近 N 个里若有超大 tool_result 仍占满窗口；②不区分 tool_result 大小/新鲜度；③`keep_recent_tools` 固定 3，不随窗口自适应。
- **改法**（精修，非重写）：
  - 让 `microcompact_keep_tools` 可随窗口自适应：在构造 `ContextCompressor` 处（已核实：非测试代码无直接 `ContextCompressor(` 调用 → 生产经 service_context/factory 在 lifespan 构造，见 `STATUS/AgentLoop.md §7`；实施时在该 factory 处取 `model_info`）按 `model_info.context_window` 给更大窗口更大的 keep（如 `clamp(window//100_000+2, 3, 8)`）。或加 flag `microcompact_size_aware` 默认 False：ON 时保护策略从「最近 N 条」改「最近 N 条 + 累计字节 ≤ M」，避免最近 N 条里有巨型结果仍爆。
  - 保持 `_microcompact_tool_results` 纯函数签名（`messages, keep_recent_tools`）不变，新增 size-aware 走新参数或新函数，避免破坏现有断言。
- **BC 保证**：不改 `microcompact_keep_tools` 默认值（3）→ 现有行为字节一致。size-aware 走新 flag（默认 False）。窗口自适应若改默认 keep 值，需谨慎——建议也藏 flag，OFF 时 keep=3 不变。
- **测试点**：
  - 单测 `backend/tests/test_compaction_bestpractice_upgrade.py`（含 WI-2 microcompact 用例）→ 新增 `test_microcompact_keep_default_unchanged`（默认 keep=3 行为不变）+ `test_microcompact_size_aware`（ON：最近 N 条里有巨型 tool_result 时也被纳入压缩判断）。
  - 真机 windows-mcp：长 agentic 任务（多个大 web_fetch 结果）→ grep `context_microcompact_only` 的 `tool_results_pruned` 数 → 验证大结果会话不再因「最近 3 条恰好全是大结果」而压不动。
- **依赖**：无。

---

## 残留风险 / 需 Lead 定夺

1. ✅ **1B-2 metrics endpoint 已核实**：= `backend/main.py:4137 post_metrics_event`（`/metrics/event`）+ `:4171 metrics`。compressor 内复用该 sink，或最简方案只落 structlog 不走 endpoint。已无待核。

2. ✅ **1B-5 ContextCompressor 构造点已核实**：非测试代码无直接 `ContextCompressor(` 调用 → 生产经 service_context/factory 在 lifespan 构造（`STATUS/AgentLoop.md §7`）。实施时在该 factory 处取 `model_info` 给自适应 keep。已无待核。

3. **1B-1 的 BC 性质（决策③已定方案B，2026-06-22）**：1B-1 **会改变估值数字**（CJK 上升到真实值），严格说**不是字节级 BC**——且影响**压缩触发时机 + skill slice 截断点**，不只是显示。**决策已定方案B：直接统一到 `count_text_tokens`，不挂 flag（`unified_token_count` 取消）**。非字节级 BC 靠**强回归测试 + 真机 windows-mcp 验收**兜底（断言中文不再 `/3.5` 低估、压缩触发时机变化无害）。批1（会改数字）直接改 + 回归 + 真机；批2（`//4`→`//4` 纯等价重构）本无行为变化直接做。**已无 Lead 待决项。**

4. **1B-1 范围已修正（之前严重低估）**：上轮误判「仅 2 处残留」。复核读码（2026-06-22）发现真实残留 **6 处**：`main.py` 三处 `/3.5`（`:3520`/`:3579`/`:3630`）+ `skill.py` 两处 `len//4`（`:221`/`:261`，另 `:164`）+ `metrics.py:267`。且口径是**三套并存**（`tokens.py` 的 `//4` 加权 vs `main.py` 的 `/3.5` vs `skill/metrics` 的裸 `//4`），收敛不彻底。**工作量从 S 上调到 M**。00-PLAN.md §2 的描述需相应校正（不是「仅 2 处」）。

5. **1A 落仓库（决策②已定方案B，2026-06-22）**：1A 是用户工作环境配置（CLAUDE.md 瘦身 / MCP 裁剪 / memory 治理），**不进 DeskPet 代码**。**决策已定方案B：把抽出来的参考文件（windows-mcp-e2e.md / codex-usage.md）规范化成 `~/.claude/knowledge-base/` 下（或参考 skill）的按需加载可复用资产，CLAUDE.md 只留一行指针；不只给一次性 SOP。** 已无待决项。

6. **1A 工具目录 token 节省量是粗估**：~250 工具/~150 skill 的常驻 token 占用无法精确 `wc`（取决于 harness 如何序列化 deferred 目录）。「省 3–8K token」是基于「每工具名 + 部分服务器说明」的量级估算，实际需用户在 settings 改完后对比圈圈基线验证。

7. **1B-3/1B-4 真机验证难度**：自适应阈值（1B-3）和摘要质量回路（1B-4）的真机 E2E 都需人造特定场景（任务性质切换 / 压缩后困惑），自动化弱，主要靠单测 + 人工真机抽测。P3 优先级合理。
