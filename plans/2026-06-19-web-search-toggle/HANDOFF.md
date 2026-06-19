# HANDOFF — 联网搜索开关（Web Search Toggle）前端落地

> 日期: 2026-06-19 | 状态: **调研完成 + 全部验证，未开始编码**
> 接手人直接从「下一步实现」开始，调研结论已逐项核实，可信。

---

## 0. 任务

给桌宠加一个**用户可见的「联网搜索开关」**：关闭后联网功能（含 DeepResearch）失效。
用户要求：**简单、不要过度设计**。用户已授权「进行你觉得棒的设计」直接实现。

---

## 1. 核心结论（已逐项验证，非推测）

### 1.1 后端 gating 逻辑 —— ✅ 已完整落地 + 有测试

| 验证项 | 结果 | 证据 |
|---|---|---|
| gate 函数 | ✅ | [tool_selector.py:38](../../backend/deskpet/agent/assembler/tool_selector.py) `_web_enabled()` 读 `[tools].web_search`，默认 `True` |
| gate 生效 | ✅ | tool_selector.py:71-72 `if not _web_enabled(): ordered = [c for c in ordered if c != "web"]`（摘掉整个 web category） |
| 主链路活代码 | ✅ | `loop.py:147` → `assemble_bundle()` → [bundle.py:103](../../backend/deskpet/agent/assembler/bundle.py) `select_tools()`；`loop.py:203` `tools=bundle.tool_schemas` 喂给 LLM |
| 三工具同 category | ✅ | `web_search` / `web_fetch` / `research_run` 注册时 category 都是 `"web"`（research_tools.py 末尾 `registry.register("research_run","web",...)`；web_search_tool.py `get_registration()` 返回 `("web_search","web",...)`） |
| config 能读到 | ✅ | [config.py:28](../../backend/config.py) `AppConfig.raw` 保留整个 toml；`raw.get("tools",{}).get("web_search",True)` |
| 测试覆盖 | ✅ | [test_p4s22_web_search.py:20-28](../../backend/tests/test_p4s22_web_search.py)：`_web_enabled=False` 时断言 `web_search`/`research_run`/`web_fetch` 全不在工具列表 |

**结论：后端这半截真完整、能跑、被测过。关掉开关 → DeepResearch 连同 web_search/web_fetch 从 LLM 工具面消失。**

### 1.2 实时生效 —— ✅ 改 config.toml 后**下一轮对话立刻生效，无需重启**

- `load_config()` 缓存键 = `(path, st_mtime_ns, st_size)`（[config.py:31-45](../../backend/config.py)），文件一改 mtime 变 → 缓存失效 → 重新读盘。
- **没有任何模块发布 `config.config` 单例**（全 backend grep `config.config =` / `^config =` 零命中，main.py 也无）。所以 `_web_enabled()` 永远走 `load_config()` 读盘分支。
- `_web_enabled()` 在每轮 `select_tools` 调用时执行（loop.py:147 每个 turn）→ 实时。

### 1.3 缺口 —— ❌ 开关对用户完全不可达（这就是要补的）

| 项 | 状态 |
|---|---|
| config.toml 写了 `[tools].web_search` 键吗 | ❌ 无 `[tools]` 段，靠代码默认 True → 现在恒为开 |
| 后端有写 config 的 IPC 吗 | ❌ Python 侧无 set/update/write_config |
| 前端有联网开关 UI 吗 | ❌ 全 `tauri-app/src` 搜 `web_search`/`联网`/`[tools]`/`setConfig` 零命中 |

---

## 2. 前后端 config 持久化机制（要复用的模板）

**前端改 config 不走 Python，走 Rust Tauri command + `toml_edit` round-trip。**

- 前端: `invoke('get_providers_config')` / `invoke('set_providers_config', {...})`（[SettingsProviders.tsx:35,52](../../tauri-app/src/components/SettingsProviders.tsx)）
- Rust: [config_cmds.rs](../../tauri-app/src-tauri/src/commands/config_cmds.rs) 用 `toml_edit::DocumentMut` 读写 **同一个 config.toml**（Python 也读它），**保留其它段 byte-for-byte**。
- 现有 `read_providers`/`write_providers` 只动 `[[llm.providers]]` + `active_provider`。
- Rust `resolve_config_path()` 与 Python `config.resolve_config_path()` 指向同一文件（providers 功能能正常存读已证明路径一致）。

---

## 3. 下一步实现（3 文件改动，Python 零改动）

### 3.1 Rust `tauri-app/src-tauri/src/commands/config_cmds.rs`
加两个 command，**完全复刻现有 `toml_edit` 模式**：
```rust
#[tauri::command]
pub async fn get_web_search_enabled() -> Result<bool, String> {
    let path = resolve_config_path();
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let doc = text.parse::<toml_edit::DocumentMut>().map_err(|e| e.to_string())?;
    Ok(doc.get("tools").and_then(|t| t.get("web_search"))
        .and_then(|v| v.as_bool()).unwrap_or(true))   // 缺省 true
}

#[tauri::command]
pub async fn set_web_search_enabled(enabled: bool) -> Result<(), String> {
    let path = resolve_config_path();
    let text = std::fs::read_to_string(&path).unwrap_or_default();
    let mut doc = text.parse::<toml_edit::DocumentMut>().unwrap_or_default();
    doc["tools"]["web_search"] = toml_edit::value(enabled);
    std::fs::write(&path, doc.to_string()).map_err(|e| e.to_string())?;
    Ok(())
}
```
> ⚠️ 还没读完 `write_providers` 全文（82-末尾被截断在动手前）。落地前先看一眼它建表的确切写法（是否需要 `doc["tools"].or_insert(table())` 之类），与之对齐风格。`doc["tools"]["web_search"] = ...` 在 toml_edit 里会自动建隐式表，通常够用。

### 3.2 Rust `tauri-app/src-tauri/src/lib.rs`
`generate_handler![]` 里 line 105（`set_providers_config,` 之后）加两行：
```rust
        get_web_search_enabled,
        set_web_search_enabled,
```

### 3.3 前端 — 新组件 `tauri-app/src/components/WebSearchToggle.tsx`
仿 SettingsProviders 模式：mount 时 `invoke<boolean>('get_web_search_enabled')` 读初值；toggle 时 `invoke('set_web_search_enabled', { enabled })`。用 `@tauri-apps/api/core` 的 `invoke`。

### 3.4 前端 `tauri-app/src/components/SettingsPanel.tsx`
`general` tab 现在是 `comingSoon` 占位（[SettingsPanel.tsx:53-58](../../tauri-app/src/components/SettingsPanel.tsx)）。把 `<p className="settings-hint">{t('settings.general.comingSoon')}</p>` 换成 `<WebSearchToggle />`（import 之）。

### 3.5 i18n `tauri-app/src/i18n/index.ts`
en/zh 的 `settings.general`（en line 11 / zh line 25）下加文案，例如：
```
webSearch: { label: '联网搜索', desc: '关闭后桌宠将无法联网（含网页搜索与深度调研）' }
```
（en 给英文）。结构: `general: { title, comingSoon, webSearch: {...} }`。

---

## 4. 验证（项目硬约束 — 不可省）

CLAUDE.md：改代码必须 **windows-mcp 真机 E2E + 截图 + 抓日志**，不能只跑单测。

1. **编译**：Rust 改了 → 重新 `npx tauri dev`（注意 CLAUDE.md 坑#7/#9：不要手动起 backend/vite，让 tauri 自管；worktree 测要设 `DESKPET_BACKEND_DIR`）。
2. **真机 case**：
   - 开设置 → general tab → 看到联网开关（截图）
   - 关闭开关 → 确认写进 config.toml（`[tools] web_search = false`）
   - 对话触发「帮我深度调研 X」→ **抓 tauri dev log（backend structlog 走 stderr 进 tauri log）确认这一轮 LLM 工具列表里没有 research_run/web_search**（这才是真证据，不是 import 查 registry）
   - 重新打开 → 同样对话 → 确认 research_run 回到工具列表
3. **单测兜底**：`backend/.venv/Scripts/python.exe -m pytest tests/test_p4s22_web_search.py -v`（已存在，应绿）。

---

## 5. 关键文件索引（绝对路径）

- gate: `G:\projects\deskpet\backend\deskpet\agent\assembler\tool_selector.py`（`_web_enabled` @38, `select_tools` @52）
- 组装: `G:\projects\deskpet\backend\deskpet\agent\assembler\bundle.py`（`assemble_bundle` @89, select_tools @103）
- 主循环: `G:\projects\deskpet\backend\deskpet\agent\loop.py`（@147 assemble, @203 tool_schemas）
- config: `G:\projects\deskpet\backend\config.py`（cache @31, load_config @34, raw @28）
- 测试: `G:\projects\deskpet\backend\tests\test_p4s22_web_search.py`
- Rust cmd: `G:\projects\deskpet\tauri-app\src-tauri\src\commands\config_cmds.rs`
- Rust 注册: `G:\projects\deskpet\tauri-app\src-tauri\src\lib.rs`（generate_handler @~104-105）
- 前端面板: `G:\projects\deskpet\tauri-app\src\components\SettingsPanel.tsx`（general tab @53-58）
- 前端模板: `G:\projects\deskpet\tauri-app\src\components\SettingsProviders.tsx`（invoke 模式 @35,52）
- i18n: `G:\projects\deskpet\tauri-app\src\i18n\index.ts`（general en@11 zh@25）

---

## 6. DeepResearch 与开关的关系（用户最初的问题）

- DeepResearch = `research_run` 工具（[research_tools.py](../../backend/deskpet/tools/research_tools.py)），靠用户说「深度调研/调研报告/技术选型」等触发词让 LLM 自己决定调用；入口 skill = [deep-research/SKILL.md](../../backend/deskpet/skills/builtin/deep-research/SKILL.md)。
- `research_run` 属于 `"web"` category，与 `web_search`/`web_fetch` 同闸。
- **所以联网开关 = 联网能力总闸；关掉 → DeepResearch 一起下线。后端已是这么设计，顺着做即可。**

---

## 7. 完成后必做（STATUS 纪律 — HARD）

跑通真机 E2E 后更新 `G:\projects\deskpet\STATUS\status.md`（§3 模块完成度 + 顶部日期）。新文件记得 `git add`（memory: 未跟踪文件会被沙箱回滚）。
