# P0 / 1A — Claude Code 工作环境瘦身（执行记录）

> 日期：2026-06-22 · 执行：Lead（Claude Opus 4.8）直接做（非 DeskPet 代码改动）

## 已完成

### WI-1A-1 CLAUDE.md 瘦身 ✅
- **全局** `~/.claude/CLAUDE.md`：10,858 → **6,365** 字符（省 **4,493**）。
- **项目** `G:\projects\deskpet\CLAUDE.md`：11,440 → **8,303** 字符（省 **3,137**）。
- **合计省 ~7,630 字符 ≈ 1.9–2.5K token / 每会话常驻。**
- 抽出的细节落 3 个按需加载资产（铁律语义全保留，主文件留触发词 + 硬核 + 指针）：
  - `~/.claude/knowledge-base/windows-mcp-e2e.md`（GUI 真测纪律全文：禁止清单 6 条 / 真测要求 5 条 / SendInput 圣杯 / 中文 IME workaround / 障碍表 / 报告格式 / 案例索引）
  - `~/.claude/knowledge-base/codex-usage.md`（codex 调用方式 + 模型矩阵 + config + 环境陷阱）
  - `~/.claude/knowledge-base/context-compact-sop.md`（圈圈机理 + 手动 /compact SOP，见 1A-3）
- **铁律未删**：中文优先 / MCP 优先 / spec-first / 质量门控 / 安全护栏 / 手测触发词与禁止项 / STATUS 纪律 / 登录账号安全约束 / 踩坑 #7/#8/#9（端口与 backend_launch）全文保留。

### WI-1A-3 auto-compact SOP ✅
- 落 `~/.claude/knowledge-base/context-compact-sop.md`：讲清「圈圈满≠不压缩，是固定开销大」机理 + 阶段切换点/长任务后/切大任务前主动 `/compact <保留X/Y/Z>` 的 SOP + 降基线优先级。

### WI-1A-4 MEMORY 治理（核心合并）✅
- 4 条「真测」同主题记忆合并为 1 条权威记忆 `feedback_real_test_discipline.md`（保留全部 4 个踩坑要点），删除原 4 个文件，MEMORY.md 索引同步（27 → 24 条）。

## 待用户照做（WI-1A-2，圈圈最大头，需重启 + 交互面板）

**为何我没直接改**：圈圈最大头是**工具/skill 目录**（~250 工具名 + ~150 skill + 多段 MCP 说明）。这些 MCP 服务器（Blender 大写 / computer-use / Claude_in_Chrome / plugin_design_* / scheduled-tasks 等）**不在** `.claude.json` 顶层 `mcpServers`（那里只有小写 `blender`），而是来自**自动安装的 marketplace 插件**。禁用它们要走**交互式 `/plugin` 和 `/mcp` 管理面板**（本会话无法驱动交互终端面板）+ **重启会话**才生效。硬改全局 `.claude.json`（38KB 含全部项目历史 + state）风险高，故留给你照做。

**照做步骤（预估再省 3–8K token / 会话）**：
1. 终端跑 `claude`，用 `/plugin` 关掉 DeskPet 后端开发用不到的插件包：
   - **Blender 全家桶**（`mcp__Blender__*` ~30 + `mcp__blender__*` ~25）— 做 Live2D/3D 资产时再开。
   - `mcp__Claude_in_Chrome__*`（~25）、`mcp__plugin_design_*`（asana/atlassian/figma/intercom/linear/notion/slack）、`mcp__scheduled-tasks__*`。
2. **保留**：`windows-mcp`（真测命脉）、`computer-use`、`context7`/`exa`/`mcp-registry`（MCP 优先铁律要用）、`superpowers`、`sp-*`/`deep-research`/`openspec-oneshot`/`spec-first`/`auto-verify`。
3. 用 `/mcp` 确认禁用项已消失，**重启会话**后对比圈圈基线。

## 验证（1A 是环境配置，非 DeskPet GUI 功能，无 windows-mcp 真测项）
- ✅ 字符节省已量化（`wc -m` 实测，见上）。
- ✅ 铁律保留可由子代理逐条核对（禁止项/触发词/STATUS/坑#7-9）。
- ✅ knowledge-base 资产文件可读、指针路径有效。
- 圈圈基线下降需用户重启会话后肉眼对比（1A-2 生效后最明显）。
