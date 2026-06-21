# 优化 Plan — 技能携带「可执行 Function Call」（会话自创 + 用户自创）

> **状态**: **v1.0 LOCKED — 经子代理 R1/R2/R3 对抗挑战收敛，R3 终判 EXECUTABLE-AS-IS ✅**
> **作者**: Claude (Lead)
> **日期**: 2026-06-22
> **配套调研档**: [`STATUS/AgentLoop.md`](../../STATUS/AgentLoop.md) · [`STATUS/status.md`](../../STATUS/status.md) §3 技能系统行
> **基线**: 读码核实（master）+ 6 个子代理结构化调研/对抗

---

## 0. TL;DR

桌宠现在的「技能(skill)」= 一份 `SKILL.md`（frontmatter + Markdown body），被当作 **prompt 注入**（`skill_invoke` 工具或 `/slash` 把 body 作为 user 消息塞进上下文），可选被 `SkillMatcher` 语义自动披露。**它不是 LLM 能直接 `tool_call` 的 function call**——`requires_script` 虽存在但 codifier 永远硬编码 `false`，且无任何「生成/创作 executable 技能」路径。

本 plan 补这条断链，落地两需求：

1. **会话自创**：用户在 session 里**完成任务**后，系统除（现状）提炼 Markdown 技能外，还能**额外生成一个 LLM 可直接调用的 function call**（带参数 schema + 可复现执行体），用户确认后注册进 `ToolRegistry`。
2. **用户自创**：用户在桌宠 UI 里**手动创建**自己的技能 + function call。

**核心**：引入「可执行技能」= `SKILL.md`（不变）+ `tool.json`（function 契约）+ 执行体（`recipe.json` 声明式步骤回放，**默认**；`script.py` 沙箱脚本 opt-in）。两需求共享「契约 + 动态注册 + 安全门 + 执行器 + 可见性」核心。

**全程 flag 默认 OFF = 字节级 BC**（遵循 `auto_disclosure.enabled=False` / `codify.enabled=False` 惯例）。

### 0.1 ⚠️ recipe 模式能力边界（诚实声明）

recipe 模式的「function」表达力 = **已注册工具的线性编排（无分支/循环/自定义计算）**+ step 间变量传递。需要图灵完备逻辑必须开 `script` 模式（默认永久 OFF，危险）。「用户创建自己的 function call」在默认形态下即「白名单工具流水线」，这是安全权衡，不是 bug——本声明防止执行后被判「function 名不副实」。

### 0.2 关键架构事实（R1 纠正，实现者必读）

> **chat 模式当前把整个 registry 的工具全量暴露给 LLM**：`main.py` chat 调用 `_agent.run(...)` **不传 `tools_filter`**（[agent_loop.py:605](../../backend/agent/agent_loop.py)）→ `schemas(enabled_toolsets=None)`（[agent_loop.py:625](../../backend/agent/agent_loop.py)）→ 全表暴露（[web_tools.py:300](../../backend/deskpet/tools/web_tools.py) 注释明示 "Chat mode passes tools_filter=None so it sees all tools"）。
> **assembler 的 `bundle.tool_schemas` 只喂 `build_messages`（prompt 装配），从不决定 LLM 真实工具列表**。`components/skill.py` 只产 prompt 文本 prelude，**不碰 enabled_toolsets / tool_schemas**。
> ⇒ 可见性控制的**唯一真实落点是 `agent_loop.run()` 的工具过滤**（见 §2.6/WI-4），**不是** assembler。本 plan v0.1 在此处错位，v0.2 已重写。

---

## 1. 现状与缺口（读码核实）

### 1.1 技能系统现状

| 组件 | 文件 | 现状 |
|---|---|---|
| 磁盘格式 | `<user_data>/skills/{built-in,user}/<slug>/SKILL.md` | frontmatter + Markdown body |
| 加载/热重载 | `backend/deskpet/skills/loader.py` (`SkillLoader`) | 扫两目录、解析、watchdog 1s debounce 热重载（**后台 threading.Timer 线程**，[loader.py:779](../../backend/deskpet/skills/loader.py)）、`reload()` 原子交换 `_skills`；`start()`(251) 顺序= `reload()`→`_register_skill_invoke_tool()`→watchdog；`__init__` 已含 `tool_registry` 参数 |
| 调用 | `skill_tools.py` (`skill_invoke`) | **单一工具** `skill_invoke(skill_name, arguments)` 把 body 当 user 消息注入。`/slash` 走同路径。⚠️ 注意 `loader.py:666 _register_skill_invoke_tool` 与 `skill_tools.py:116` 历史上都注册过 `skill_invoke`，**以 skill_tools.py 为现行**（实现期 grep 确认无双注册） |
| 脚本执行 | `loader.py::invoke_script`(574-661) | `requires_script:true` 路径。⚠️ **R1 实测：当前 `args` 形参并未真正注入子进程**（`subprocess -c full_source`，preamble 无 sys.argv），且 14 builtin 全 `requires_script:false` → **此路径实质闲置**（故 WI-10 改 JSON 入参几乎无真实 BC 包袱） |
| 自动披露 | `skill_matcher.py` + `assembler/components/skill.py` | flag `[skills.auto_disclosure].enabled`(默认 False)；trigger 词法 + BGE-M3 语义混合（短中文 query 区分度差，阈值 0.55，skill.py 注释记此坑）；**只产 prompt prelude 文本** |
| 自创闭环 | `skill_codifier.py` (WI-4.3) | `detect_trigger`→LLM 提候选 `{name,description,trigger_pattern,steps}`→pending 表(`_DDL_PENDING` 用 `CREATE TABLE IF NOT EXISTS`)→前端确认卡→`confirm(accept)` 落 SKILL.md。`render_skill_md`(146) 硬编码 `requires_script:false`。⚠️ `_maybe_codify_skill`(main.py~760) 构造 `SkillCodifier`(~781) **不传任何 function flag**；`max_candidates_per_day` **全仓无强制点（定义而未用）** |
| Marketplace | `marketplace/{installer,safety}.py` | GitHub URL→clone→`validate_manifest`(name/desc/tools 白名单/permission 白名单)→reload；`skill_install` 权限门 |
| 工具注册表 | `tools/registry.py` | `ToolSpec`(frozen，字段 `source`/`dangerous`/`concurrency_safe`/`replace_allowed`/`timeout_seconds` 等)；运行时 `register`(284)/`unregister`(405)；`execute_tool`(618) 全链路(权限+熔断+超时+receipt)；**async handler 支持**(731-737 `iscoroutinefunction` 分流)；`schemas(enabled_toolsets)`(416) **仅按 toolset 过滤**；`to_openai_schema(names=)`(558)/`to_anthropic_schema(names=)`(585) **支持 per-name 过滤**；`list_tools(source=)`(1041) |
| 权限 | `types/skill_platform.py:21-30` `PermissionCategory` Literal(**8 值**含 skill_install)；`permissions/gate.py` `_VALID_CATEGORIES=set(get_args(...))`(63) **自动纳入新值**；`_DEFAULT_ALLOW={"read_file"}`(58)；`_summarize`(355) 有按 category if 链(新值落 fallback) |
| 前端 | `SkillStorePanel.tsx`(Installed/Marketplace/Add-by-URL) + `code-panel/{InputBar,SlashDropdown,ws}.tsx`；候选卡 = `store.push_message(sid,{role:"skill_candidate"})`([ws.ts:376](../../tauri-app/src/code-panel/ws.ts))**纯内存**，`MessageBubble`(157) 渲染 `SkillCandidateCard`；状态在 `messages.ts`/`sessionsStore.ts`；slash 数据源 `/api/commands/help`(main.py:3747 枚举 `list_skills()`) | 无「创建技能」入口；**无任何「列出已注册工具」的 WS/REST**（recipe 构建器需新建） |

### 1.2 关键缺口

| # | 缺口 | 证据 |
|---|---|---|
| G1 | 技能无法成为 LLM 直接可调用的 function call | `skill_tools.py` 只注册 1 个 `skill_invoke`；loader 不向 registry 注册 per-skill 工具 |
| G2 | codifier 只产 Markdown，不产 function call | `skill_codifier.py:146-183` 硬编码 `requires_script:false` |
| G3 | ToolPath 不记录 args，无法参数化工作流 | `tool_path.py` `ToolStep` 无 args；`agent_loop.py:2173` record 不传参 |
| G4 | 无 UI 创作技能/function | `SkillStorePanel.tsx` 仅安装类 tab |
| G5 | 无「可执行技能」契约/动态注册/校验/执行管线 | 全仓无 tool.json/recipe.json 概念 |
| G6 | 无「列出已注册工具」给前端的接口 | grep `tool_list` 仅命中注释；recipe 构建器无数据源 |

---

## 2. 设计总览

```
                 ┌──────────────────────────────────────────────┐
  需求1 会话自创 ─┤ ① 契约 tool.json+recipe.json (FunctionSpec)   │
  (codifier 扩展)│ ② SkillFunctionExecutor (recipe 回放)         ├─→ ToolRegistry
                 │ ③ 动态注册 (loader.reload→register ToolSpec)   │  (execute_tool 全链路)
  需求2 用户自创 ─┤ ④ 安全校验 + 递归守门                          │      ↑
  (UI 创作)      │ ⑤ 可见性 (agent_loop.run skill_fn 过滤)        ├──────┘ LLM tool_call
                 │ ⑥ tool_list_request (recipe 构建器数据源)      │
                 └──────────────────────────────────────────────┘
```

### 2.1 磁盘格式（扩展，非破坏）

可执行技能在原目录**新增**文件（原目录结构不变）：
```
<user_data>/skills/user/<slug>/
  SKILL.md       # 不变：frontmatter + body（缺省/无 tool.json = 现状纯 Markdown 技能，字节不变）
  tool.json      # 【新】function 契约
  recipe.json    # 【新·recipe 模式】参数化工具序列
  script.py      # 【新·script 模式·opt-in】
```
- **有无 `tool.json` 决定是否成为 function call**。无 = 行为字节不变。
- `SKILL.md` frontmatter 加**可选** `has_function: true`（仅标记，真相以 tool.json 为准）。
- `SkillMeta.to_dict()`（[loader.py:99](../../backend/deskpet/skills/loader.py)）须加 `has_function` 字段，供前端 Installed tab 区分「知识技能 vs 可执行技能」（见 WI-8）。

### 2.2 `tool.json` 契约（v1）

```jsonc
{
  "schema_version": 1,
  "name": "weather-ppt-report",          // 对齐 slug；注册名 skill_fn__<slug>
  "description": "查询城市天气并生成 PPT 报告",
  "parameters": {                         // 标准 JSON Schema
    "type": "object",
    "properties": {
      "city": {"type": "string", "description": "城市名"},
      "days": {"type": "integer", "description": "预报天数", "default": 3}
    },
    "required": ["city"]
  },
  "impl": "recipe",                       // "recipe"(默认) | "script"
  "permission_category": "skill_call",    // recipe→skill_call；script→强制 shell
  "recipe_ref": "recipe.json",
  "script_ref": null,
  "source": "codified" | "user",
  "version": "1.0.0"
}
```

### 2.3 `recipe.json` 契约 + 变量语义（v1）

```jsonc
{
  "schema_version": 1,
  "steps": [
    {"tool": "web_search", "args": {"query": "{city} 天气 未来{days}天"}, "save_as": "weather"},
    {"tool": "ppt_create", "args": {"topic": "{city}天气报告", "context": "{weather.result}"}}
  ],
  "return": "last"                        // "last" | "all"
}
```

**变量与替换语义（精确，R1-C2/C4）**：
- `{param}`：来自 function call 入参（按 tool.json.parameters）。
- `{save_as}` / `{save_as.path}`：来自前序 step 结果。每个工具返回 **v2 envelope** `{"ok":bool,"result":str,...}`（[registry.py:771](../../backend/deskpet/tools/registry.py)）。`save_as` 存的是**解析后的 result**：执行器对 `envelope["result"]` 尝试 `json.loads`；成功→存解析对象，失败→存原始字符串。
- 取值：`{weather}` = 整个 result（对象则 `json.dumps` 序列化后内联）；`{weather.temp}` = 点路径取字段（对象/字典逐级 `.get`，越界→空串）。
- 仅做**字符串模板替换**，禁止任意表达式求值。非字符串值内联时统一 `json.dumps(ensure_ascii=False)`。
- ⚠️ recipe 用 `{name}`；SKILL.md body 仍用 `${args[N]}`（[loader.py:157](../../backend/deskpet/skills/loader.py)），**两套独立**，UI 文案与 docs 须说明，避免混淆。

**执行语义（`SkillFunctionExecutor`）**：每 step 走 `registry.execute_tool`（复用权限/熔断/超时/receipt）；任一步 `ok=False` → 立即 `{"ok":false,"error":"step N (<tool>) failed: ...","result":null}`（**不吞错**）；按 `return` 组织返回。

### 2.4 `ToolStep` 扩展（填 G3）

`backend/deskpet/agent/tool_path.py`：
```python
@dataclass
class ToolStep:
    name: str
    ok: bool = True
    corrected: bool = False
    recovered: bool = False
    args: dict[str, Any] = field(default_factory=dict)   # 【新】脱敏入参快照
    result_digest: str = ""                              # 【新】结果摘要(<=200字)
```
- 仅 `codify.enabled` 或 `functions.enabled` 任一开时填充（否则空 dict，零开销 + BC）。
- **脱敏** `_sanitize_args`（模块级）：过滤键名匹配 `(?i)key|token|secret|password|api[_-]?key`，值长 >500 截断。

### 2.5 权限分类

新增 `PermissionCategory = "skill_call"`（`types/skill_platform.py` Literal + 前端 `tauri-app/src/types/skillPlatform.ts` 镜像）：
- gate 自动纳入新值（`_VALID_CATEGORIES`），**但**须给 `permissions/gate.py::_summarize`(355) 加 `skill_call` 分支（否则弹窗文案丑，R1-#8）。
- **recipe** function → `skill_call`（首次按 params 形状哈希弹一次，同形免重复）；**内层每 step 仍各自走自己工具权限门**（双层）。
- **script** function → 强制 `shell` + `dangerous=True`，受 `allow_script_impl`(默认 False) + `dangerous_tools_allowlist` 双 gating。
- recipe 多 step 首跑可能多次弹窗 → 依赖 `permission_auto_mode` 或形状缓存；真机 TC-3 验证弹窗次数可接受。

### 2.6 LLM 可见性（重写，唯一真实落点 = agent_loop 工具过滤）

所有 skill_fn 注册到新 toolset `"skill_function"`。可见性**不经 assembler**，而在 `agent_loop.run()` 出参处理：

- `agent_loop.run()` 新增可选参 `skill_fn_allowlist: set[str] | None = None`。计算 `tool_schemas` 后（[agent_loop.py:625](../../backend/agent/agent_loop.py)），若 `skill_fn_allowlist is not None`：**剔除** function name 以 `skill_fn__` 开头且 ∉ allowlist 的 schema（轻量后过滤，不动 tools_filter 机制）。
- `main.py` chat 调用 `_agent.run(...)`（~6398）处计算并传入：
  - `functions.enabled=False` → 传 `None`（且根本无 skill_fn 注册）→ **schemas 字节不变（BC）**。
  - `expose_mode="all"`（**阶段1 默认**，R1 推荐：架构最自然）→ 传 `None` → 所有 skill_fn 随 `tools_filter=None` 全暴露。
  - `expose_mode="matched"` → 复用 `_run_chat` 里已有的 `SkillMatcher` 调用结果，收集命中且带 function 的技能 → `allowlist={f"skill_fn__{slug}"}` 传入（其余 skill_fn 被剔除，非 skill_fn 工具不受影响）。
- `tool_search` 元工具始终能搜到全表 skill_fn（registry 全表可搜），是 matched 模式下的兜底发现路径。
- chat 入口（调 `_agent.run` 前）同时 `_SKILL_FN_SESSION.set(session_id)`（供 WI-3 executor 内层 step 取 session 维度，见 WI-3）。

> 此设计**不改** chat「看到全部常规工具」的现有行为（BC），只对 `skill_fn__*` 这个**全新前缀**做增量过滤。

**R2 已核实的两点**：
- **code/voice venue 同样 `tools_filter=None`**（全栈 grep 无任一处传非 None tools_filter；voice 走 [voice_pipeline.py:653](../../backend/pipeline/voice_pipeline.py) 亦不传）→ skill_fn 在**所有 venue 默认可见**，无需为 code 模式单独处理。
- **matched 模式数据源未坐实**：`SkillMatcher` 命中结果经 assembler 产 prelude，其命中列表**未必**在 `_agent.run` 调用作用域内可直接取。**实现期须 grep `_run_chat` 确认 matcher 命中列表可取性**；取不到则 matched 降级为 follow-up，**阶段1 只交付 `expose_mode="all"`**（真机 TC-1/2 本就规定在 all 下跑，不阻塞）。

---

## 3. 工作项（WI）分解

> 依赖序：WI-0 → WI-1 → (WI-2,3,4,5,6′ 共享核心) → WI-7/8(需求1) ‖ WI-9/10/11(需求2) → WI-12(script) → WI-13(收尾)。**不可少做任一 WI。**

### WI-0 — 配置开关（default OFF = BC）

**文件**: `backend/config.py`（`SkillsConfig` 区 ~354-456）
```python
@dataclass
class SkillsFunctionsConfig:           # [skills.functions]
    enabled: bool = False
    allow_script_impl: bool = False
    expose_mode: str = "all"           # 阶段1 默认 all（matched 见 §2.6）
    max_recipe_steps: int = 12
    call_timeout_seconds: float = 300.0

@dataclass
class SkillsConfig:
    ...
    functions: SkillsFunctionsConfig = field(default_factory=SkillsFunctionsConfig)

@dataclass
class SkillsCodifyConfig:
    enabled: bool = False
    max_candidates_per_day: int = 3
    emit_function_call: bool = False   # 【新】
```
**关键（R2 纠正落点）**：config 加载**不自动递归** —— skills 段在 [`config.py:1146-1157`](../../backend/config.py) **手工** pop+构造（`_load_section` 平铺不递归）。WI-0 必须在此处**手加三步**（同现有 `codify` 模式）：`raw_fn = dict(raw_skills.pop("functions", {}) or {})` → `fn = _load_section(SkillsFunctionsConfig, raw_fn)` → `SkillsConfig(..., functions=fn)`；`emit_function_call` 随 `codify` 段已有解析自动带上（确认 `_load_section(SkillsCodifyConfig, ...)` 覆盖新字段）。（FP-5 踩过 `[skills.codify]` 漏解析，教训同源。）

**验收 TG-0**：① 未设段 → 全 default（False）；② **config.toml 显式写 `[skills.functions] enabled=true` 能被读成 True**（防漏解析回归，必测）；③ 字节 BC。

---

### WI-1 — `ToolStep`+recorder 捕获 args（填 G3）

**文件**:
- `tool_path.py`：`ToolStep` 加 `args`/`result_digest`（§2.4）；`record_tool` 增可选 `args`/`result_digest`（默认 None→空，BC）；加模块级 `_sanitize_args`。
- `agent/agent_loop.py`（record_tool 调用点 **~2173-2175**）：flag 开时传 **`tc.arguments`**（⚠️ 字段名是 `arguments` 不是 `args`，全 plan 已改）脱敏值 + 结果摘要。flag 标志经 build_agent 构造期布尔传入。

**验收 TG-1**：不传→空(BC)；敏感键过滤；长值截断；flag OFF→agent_loop 不传（mock 断言）。

---

### WI-2 — 契约 + parser

**文件（新）**: `backend/deskpet/skills/function_spec.py`
- `@dataclass(frozen=True) FunctionSpec` / `RecipeStep` / `Recipe`（映射 §2.2/2.3）。
- `load_function_spec(skill_dir)->FunctionSpec|None`：无 tool.json→None；有则解析+调 WI-5 `validate_function_spec`，失败 raise `FunctionSpecError`。
- `render_tool_json(...)` / `render_recipe_json(...)`（codifier/UI 共用）。

**文件**: `loader.py`：`SkillMeta` 加 `function_spec: FunctionSpec|None=None` + `has_function: bool`；扫描目录时调 `load_function_spec` 挂上；解析失败 → warn+skip function（**不影响** Markdown 技能加载，soft-fail）。

**验收 TG-2**：往返渲染/解析；缺 tool.json→None；坏 JSON→抛错且不污染其余技能。

---

### WI-3 — `SkillFunctionExecutor`（recipe 回放）

> ⚠️ **R2 纠正**：`registry.execute_tool` 真实签名 = `(name, params, session_id, task_id="")` 且**返回 dict（非 JSON 字符串）**（[registry.py:618,875](../../backend/deskpet/tools/registry.py)），handler 固定以 `handler(merged_params, task_id)` 两参调用（[registry.py:733](../../backend/deskpet/tools/registry.py)）——**无法**给 handler 透传 `_recursion_guard`/`session_id` 形参。改 execute_tool 签名会波及全部 30+ handler，违 BC。⇒ 递归守门与 session_id 改走 **`contextvars`**（同一 async task 链跨 await 自动继承），零侵入 execute_tool。

**文件（新）**: `backend/deskpet/skills/skill_function_executor.py`
```python
import contextvars
# 模块级：当前递归链上的 slug 集 + 当前会话 id（main.py chat 入口 set，executor 读）
_RECURSION_GUARD: contextvars.ContextVar[frozenset[str]] = \
    contextvars.ContextVar("skill_fn_recursion_guard", default=frozenset())
_SKILL_FN_SESSION: contextvars.ContextVar[str] = \
    contextvars.ContextVar("skill_fn_session_id", default="")

class SkillFunctionExecutor:
    def __init__(self, *, registry, skill_loader, cfg): ...
    async def execute(self, slug: str, params: dict, task_id: str) -> str:   # 返回 JSON 字符串 envelope
        # 1. 取 function_spec；无→ok=False
        # 2. flag：functions.enabled? impl=script 还要 allow_script_impl
        # 3. 参数校验：required 齐全 + 按 parameters 浅类型校验
        # 4. impl=recipe:
        guard = _RECURSION_GUARD.get()
        if slug in guard:                                  # 递归守门（contextvar）
            return json.dumps({"ok": False, "error": f"recursion: {slug}", "result": None})
        token = _RECURSION_GUARD.set(guard | {slug})
        session_id = _SKILL_FN_SESSION.get() or task_id    # session 维度兜底（见下注）
        try:
            vars_ = {}
            for i, step in enumerate(recipe.steps):
                if i >= cfg.functions.max_recipe_steps:    # 步数上限
                    return json.dumps({"ok": False, "error": "recipe too long", "result": None})
                if step.tool in _FORBIDDEN_IN_RECIPE:      # WI-5 来源（registry 实查）
                    return json.dumps({"ok": False, "error": f"forbidden tool: {step.tool}", "result": None})
                rendered = _render_args(step.args, params, vars_)            # §2.3 语义
                env = await self.registry.execute_tool(step.tool, rendered, session_id, task_id)  # dict，不 json.loads
                if not env.get("ok"):
                    return json.dumps({"ok": False, "error": f"step {i} ({step.tool}) failed: {env.get('error')}", "result": None})
                if step.save_as:
                    vars_[step.save_as] = _parse_result(env.get("result"))  # envelope.result→json.loads 容错
            # 整体 asyncio.wait_for(cfg.functions.call_timeout_seconds) 包裹上面循环
            # return 按 recipe.return 组织
        finally:
            _RECURSION_GUARD.reset(token)
        # 5. impl=script → skill_loader.invoke_script(slug, params)（WI-12）
```
- **递归守门**靠 `_RECURSION_GUARD` contextvar：内层 step 经 `execute_tool` 再触发某 `skill_fn__` handler 时，handler 调 `executor.execute()` 读到的 guard 已含外层 slug → 拒。**前提：skill_fn handler 必须是 `async def`**（WI-4 工厂）——async handler 走 `await spec.handler(...)`（[registry.py:733](../../backend/deskpet/tools/registry.py)）contextvar 正常继承；sync handler 走 `run_in_executor` 会丢 contextvar，故**禁止** sync handler。
- **session_id 缺口**：handler 拿不到 session_id（execute_tool 不传给 handler）。方案：main.py chat 入口（调 `_agent.run` 前）`_SKILL_FN_SESSION.set(session_id)`，executor 读；读不到则用 `task_id` 兜底。**声明**：内层 step 的权限缓存/熔断按此 session 维度，不可错位。
- `_FORBIDDEN_IN_RECIPE` 来源见 WI-5（registry 实查，非写死）。

**验收 TG-3**：单步成功；多步 save_as（含 `.path` 取值、非字符串序列化）；中间 fail 立即返回且后续不跑；缺必填→ok=False；递归被拦；超 max_steps/超时报错；flag OFF→ok=False。

---

### WI-4 — 动态注册 + 可见性（填 G1，重写）

**文件（新）**: `backend/deskpet/skills/skill_function_registrar.py`
- `register_skill_functions(registry, skill_loader, executor, cfg) -> list[str]`（**纯 sync**，可在 watchdog timer 线程安全调用）：遍历带 function_spec 技能，为每个：
  - `name=f"skill_fn__{slug}"`、`toolset="skill_function"`、`source="skill_function"`
  - `schema={"type":"function","function":{"name":name,"description":...,"parameters":spec.parameters}}`
  - **handler = async 工厂闭包**（**标准 `(dict,str)` 签名，禁用 `asyncio.run`、禁 sync handler**，R1-MAJOR-3 + R2）：
    ```python
    def _make_handler(slug, executor):
        async def _h(args: dict, task_id: str) -> str:        # 必须 async：保 contextvar 递归守门透传（见 WI-3）
            return await executor.execute(slug, args, task_id)
        return _h
    ```
    （registry `execute_tool` 对 coroutine handler 直接 await，[registry.py:733](../../backend/deskpet/tools/registry.py)；递归守门/session_id 走 contextvar，不经 handler 形参）
  - `permission_category=spec.permission_category`（script→shell）、`dangerous=(impl=="script")`、`replace_allowed=True`、`timeout_seconds=cfg.functions.call_timeout_seconds`、`concurrency_safe=False`
  - 注册前 `registry.unregister(name)`（幂等）
- `unregister_all_skill_functions(registry)`：用 `list_tools(source="skill_function")`（[registry.py:1041](../../backend/deskpet/tools/registry.py)）枚举 unregister（比前缀枚举稳，R1-MAJOR-5）。

**文件**: `loader.py`
- `__init__` 扩 `executor`/`cfg`（可选，None→跳过 = BC）；**枚举并同步所有 SkillLoader 构造点**（grep `SkillLoader(`：main.py lifespan、测试 fixture 等）。
- `reload()` 末尾（原子交换后）：`if cfg.skills.functions.enabled: unregister_all_skill_functions(registry); register_skill_functions(...)`。**注册纯 sync，watchdog timer 线程安全**（不跑 event loop）。
- `start()` 内 `reload()` 已含注册 → boot 即注册。

**文件**: `agent/agent_loop.py`：`run()` 加 `skill_fn_allowlist: set[str]|None=None`；`tool_schemas` 算出后按 §2.6 剔除。`execute_tool` 调用链透传 `_recursion_guard`（agent_loop dispatch→execute_tool→skill_fn handler）。

**文件**: `main.py`
- lifespan：`functions.enabled` 时构造 `SkillFunctionExecutor` 注入 `SkillLoader`；start() 后已注册。
- chat 调用 `_agent.run()`（~6398）：按 §2.6 计算 `skill_fn_allowlist` 传入。

**验收 TG-4**：① registrar 产正确 ToolSpec、reload 幂等不抛 conflict、unregister 清场；flag OFF→不注册。② 可见性：`expose_mode=all` 时 chat schemas 含 `skill_fn__*`；`matched` 仅命中后含；`functions.enabled=False` → schemas **字节不变**（BC 专测）。③ 集成 `test_skill_function_e2e_dispatch.py`：`execute_tool("skill_fn__x",...)` 走通权限/熔断/receipt 全链路。④ 注册在 sync 上下文（无 loop）不炸。

---

### WI-5 — 安全校验 + 递归守门

**文件（新）**: `backend/deskpet/skills/safety_function.py`（复用 `marketplace/safety.py` 常量）
- `validate_function_spec(tool_json, recipe_json, *, known_tools, cfg)`（违规 raise `SafetyError`）：
  - name `^[a-zA-Z0-9_\-]{1,64}$`；description 非空。
  - parameters：type=object、properties dict、required⊆properties。
  - impl∈{recipe,script}；script 且 `not allow_script_impl`→拒。
  - permission_category∈白名单。
  - recipe：每 step.tool∈known_tools 且∉`_FORBIDDEN_IN_RECIPE`；步数≤max_recipe_steps。
  - **占位符校验（顺序累积，R1-E1）**：按 step index 递增，已定义符号集 = `parameters.keys()` ∪ **严格在当前 step 之前**的 save_as；当前 step args 里每个 `{x}`/`{x.y}` 的根符号 `x` 必须∈符号集，否则 SafetyError（前向引用 step2.save_as 在 step1 → 报错）。
- **`_FORBIDDEN_IN_RECIPE`（registry 实查，R1-E2）**：实现期 grep registry 实际注册名补全，至少含：`skill_fn__*` 前缀、`skill_invoke`、`tool_search`、`run_shell` + 子代理工具实名（grep `agent`/`spawn_*`，如 `agent_parallel`/`spawn_subagents`/`spawn_team`——以实查为准）。
- known_tools 取 registry 全表名集（复用 `validate_manifest` 取法）。
- 给 `permissions/gate.py::_summarize` 加 `skill_call` 文案分支。

**验收 TG-5**：坏 name/未知工具/递归工具/script 未开 flag/**前向引用 save_as** → 各 SafetyError；合法→通过。

---

### WI-6 — `tool_list_request` 接口（填 G6，recipe 构建器数据源）

**文件**: `main.py`（WS handler）：新增 `tool_list_request` → 返回 `tool_list_response {tools:[{name,toolset,description,parameters}]}`。**过滤** `skill_fn__*`（防自引用）、`dangerous`、env-gated 未满足、`_FORBIDDEN_IN_RECIPE`。
**文件**: 前端 `code-panel/ws.ts` + `SkillCreateForm`（WI-10）：`fetchRegisteredTools()`。

**验收 TG-6**：handler 返回过滤后清单；不含 skill_fn/dangerous；前端 fetch 往返。

---

### WI-7 — 需求1：codifier 扩展（自动产 function call，填 G2）

**文件**: `skill_codifier.py`
- `_CODIFY_PROMPT`：`emit_function_call` 开时额外要求 LLM 输出 `parameters`(JSON Schema) + `recipe`(参数化步骤)。prompt 喂入 `path.steps[].name` + **`path.steps[].args`**（WI-1 捕获，字段 `arguments`）+ `result_digest`。
  - **参数化指令 + few-shot（R1-C3）**：明确启发式「出现在 goal_text 的字面量/明显随任务变化的值 → 参数；固定 URL/路径/常量 → 内联常量」，给 1-2 个 few-shot；**中间降级档**：推断置信度低 → 产**无参 recipe**（args 用录制时的固定值），而非只「合法/纯 Markdown」两档。
- `_generate_candidate`：解析校验扩展字段 → 调 `validate_function_spec`；不过 → 退化纯 Markdown（不阻断原闭环）。
- `render_skill_md`：有 function → frontmatter 加 `has_function:true`（仍 `requires_script:false`，recipe 不走 script）。
- `confirm(accept=True)`：除写 SKILL.md，有 function → `render_tool_json`/`render_recipe_json` 写盘 → `loader.reload()`（→WI-4 注册）。
- **DDL 迁移（R1-MAJOR-1/F8）**：`pending_skill_candidates` 加 `function_json TEXT`。因 `CREATE TABLE IF NOT EXISTS` 不会给老库加列 → `_ensure_table` 内加 `PRAGMA table_info(pending_skill_candidates)` 探测，缺列则 `ALTER TABLE ... ADD COLUMN function_json TEXT`（try/except 容错）。`write_pending`/`fetch_pending` 带上。
- **接线（R1-C3）**：`main.py:_maybe_codify_skill`(~781) 构造 `SkillCodifier` 处透传 `emit_function_call` flag；`skill_candidate_proposed` payload 带 `function`（供前端 WI-8 展示）。

**验收 TG-7**：`emit_function_call=False`→字节同今天；ON+合法 function→confirm 后磁盘 tool.json/recipe.json + reload 后 registry 有 `skill_fn__<slug>`；非法→降级纯 Markdown 不抛；低置信→无参 recipe；pending 往返含 function_json；**老库自动 ALTER 加列**。

---

### WI-8 — 需求1：前端候选卡 + 持久化（填 bug#2 历史坑）

**文件**: `tauri-app/src/` 候选卡(`SkillCandidateCard`) + `code-panel/ws.ts` + `messages.ts` + `sessionsStore.ts` + `types/skillPlatform.ts`
- `skill_candidate_proposed` payload 扩展 `function`（name/parameters/recipe 摘要）；候选卡加「可执行函数」区（函数名+参数+步骤预览）；按钮文案区分「保存为知识技能 / 保存为可执行技能(含函数)」。
- **候选卡 rehydrate（R1-B3，历史踩坑）**：候选卡是内存 ephemeral，reload/切 session 丢失但后端 Future 仍挂起 → 用户点不到 → 超时 reject 技能消失。**修**：session 加载时拉后端 pending（已有 `list_all_pending()`，[skill_codifier.py:259](../../backend/deskpet/skills/skill_codifier.py)）经新 WS `skill_pending_list` 重建卡片；`messages.ts`/`sessionsStore.ts` 承载 awaiting 状态。
- `SkillMeta.to_dict` 的 `has_function` 经 `list_skills` 暴露给 Installed tab（区分图标）。

**验收 TG-8**：vitest 卡片含/不含 function 区；**reload 后候选卡 rehydrate 可点**；`tsc` 0 err。真机 §5 TC-1。

---

### WI-9 — 需求2：用户自创后端（`SkillAuthoringService` + WS）

**文件（新）**: `backend/deskpet/skills/authoring.py`
- `SkillAuthoringService(skill_loader, registry, cfg)`：
  - `draft(spec)->dict`：校验(`validate_function_spec`+frontmatter)+返回渲染预览（不落盘）。
  - `create(spec)->slug`：再校验→slug 解析(碰撞 `-v2`，抽 `skill_codifier._resolve_skill_dir` 为共享 util)→写 SKILL.md(+tool.json+recipe.json/script.py)→`loader.reload()`→返回 slug。
  - `update(slug,spec)`：仅 `user/`（禁 built-in）。
  - `delete(slug)`：仅 `user/`；**顺序 = 删目录 → `loader.reload()`**（reload 内 `unregister_all+register` 幂等重建，被删技能自然消失；**不手动 unregister 单个**，避免 watchdog 竞态把它重注册回来，R1-D3）。

**文件**: `main.py`：WS `skill_create_draft`/`skill_create_confirm`/`skill_function_update`/`skill_function_delete` + 响应；全 gated `functions.enabled`，OFF→返回「未启用」。

**验收 TG-9**：draft 不落盘；create 落盘+reload+注册；update/delete 生效；禁改 built-in；非法拒绝；delete 顺序正确（reload 后技能消失且无残留注册）；flag OFF→short-circuit。`test_main_skill_authoring_wiring.py` WS 往返。

---

### WI-10 — 需求2：用户自创前端（创建 tab + function builder）

**文件**: `SkillStorePanel.tsx`(加第4 tab「创建」) + 新 `SkillCreateForm.tsx` + `ws.ts` + `messages.ts` + `types/skillPlatform.ts`
- 表单：name/description/when_to_use/triggers/body(markdown)、impl(recipe/script)、参数构建器(动态增删 {name,type,required,description,default})、recipe 构建器(从 `fetchRegisteredTools()` 下拉选 tool + 每步 args 模板映射参数/前序 save_as)、script 文本域(仅 `allow_script_impl` 开显示+危险提示)。
- 「预览」→`skill_create_draft`；「创建」→`skill_create_confirm`→成功刷新 Installed。
- 文案沿用**硬编码中文**（项目现状无 i18n，显式声明，R1-B4）。

**验收 TG-10**：vitest 增删参数/step、预览/创建 WS 正确；`tsc` 0 err。真机 §5 TC-2/TC-3。

---

### WI-11 — slash 入口与 function 路径错配决策（R1-A2/B2）

**现状**：新建 user_invocable 技能**自动**进 slash 下拉（`/api/commands/help` 枚举 `list_skills`），但 slash 选中只走 `skill_invoke`（body 注入），**不执行 recipe**——用户期望落空。

**决策（本 plan 采纳）**：slash 下拉对带 function 的技能**视觉区分**（加「⚡函数」标记 + tooltip「此技能含可执行函数，对话中让桌宠调用，或点此仅注入说明」）。**不**改 slash 执行语义（保持 body 注入，BC）；引导用户通过对话触发 function。
**文件**: `SlashDropdown.tsx`(标记) + slash 数据源(main.py:3747 `list_skills` 带 `has_function`)。

**验收 TG-11**：vitest slash 下拉对 skill_fn 显标记；点击仍 body 注入(BC)。

---

### WI-12 — script 模式（opt-in，复用 invoke_script 沙箱）

**文件**: `skill_function_executor.py`(impl=script 分支) + `loader.py::invoke_script`
- ⚠️ R1 实测 invoke_script 当前 args 未真正注入子进程 → 本 WI 实现 **JSON 入参注入**（params→`json.dumps`→子进程 `sys.argv[1]` 或 stdin；保留旧位置参数兼容路径）。
- 严格 gating：`allow_script_impl=False`(默认)→注册时跳过 script 技能的 function（仍可作纯 Markdown 技能）；ON→function `dangerous=True` + 需 `dangerous_tools_allowlist` 放行 + 走 shell 权限门。

**验收 TG-12**：OFF→不注册函数；ON→沙箱执行/超时/错误回收；JSON 入参往返；危险 gating 生效。

---

### WI-13 — 收尾（项目 HARD 纪律）

- **更新 `STATUS/status.md`** §3 技能行（标 ✅ 可执行 function call）+ §4 加里程碑（CLAUDE.md 硬性要求）。
- **更新 `docs/SKILLS.md`**：可执行技能格式、recipe 语法、`{name}` vs `${args[N]}`、安全边界。
- 落盘 `plans/manual-results-2026-06-22-skill-fn/`。

---

## 4. TDD 测试组

| TG | 文件 | 覆盖 |
|---|---|---|
| TG-0 | `test_skills_functions_config.py` | WI-0 + **嵌套段真解析** + BC |
| TG-1 | `test_tool_path_args_capture.py` | WI-1 args/脱敏/flag |
| TG-2 | `test_function_spec.py` | WI-2 往返/容错 |
| TG-3 | `test_skill_function_executor.py` | WI-3 执行/save_as 取值/守门/超时/不吞错 |
| TG-4 | `test_skill_function_registrar.py`+`test_skill_function_visibility.py`+`test_skill_function_e2e_dispatch.py` | WI-4 注册/可见性/全链路/sync 注册不炸 |
| TG-5 | `test_function_safety.py` | WI-5 校验 + 顺序占位符 + 前向引用 |
| TG-6 | `test_tool_list_request.py` | WI-6 |
| TG-7 | `test_skill_codifier_function.py` | WI-7 扩展/降级/**DDL ALTER 迁移** |
| TG-9 | `test_skill_authoring.py`+`test_main_skill_authoring_wiring.py` | WI-9 |
| TG-12 | `test_skill_function_script.py` | WI-12 + 危险 gating |
| TG-BC | `test_skill_functions_byte_baseline.py` | **全 flag OFF → 技能/codify/工具/chat schemas 字节不变** |

前端 vitest：候选卡(含 rehydrate) + 创建表单 + slash 标记；`tsc --noEmit` 0 err。
**铁律**：每 TG 含 flag-OFF BC 断言。

---

## 5. 真机手测（windows-mcp，遵循「真模拟人工」硬约束）

> `LOCAL-DEV-CREDENTIALS.md` 登录走真 LLM。每用例截图→真坐标点击/输入→截图→抓 backend log 判 PASS。落盘 `plans/manual-results-2026-06-22-skill-fn/`。**TC-1/TC-2 在 `expose_mode=all` 下跑**（matched 模式 matcher 短中文召回不稳，会让一票否决 flaky，R1-D1）。

| TC | 场景 | 硬证据 |
|---|---|---|
| **TC-1★(否决)** | 需求1：多步任务("查北京天气并生成 PPT")→agent 完成→候选卡含「可执行函数」区→真点「保存为可执行技能」 | 磁盘出现 tool.json+recipe.json；log `skill_fn_registered name=skill_fn__<slug>`；**下一轮**对话 LLM `tool_call skill_fn__<slug>(city=...)` 成功 |
| **TC-2★(否决)** | 需求2：技能面板「创建」tab→填名/描述/2参/2步 recipe(工具下拉来自 tool_list_request)→预览→创建 | 预览正确；Installed 出现新技能(⚡标记)；磁盘 3 文件；log 注册成功 |
| TC-3 | 调用刚建函数 | `execute_tool skill_fn__<slug>`→权限弹窗(次数可接受)→各 step 执行→结果卡 |
| TC-4 | 安全：recipe 引用未知/递归/前向 save_as | 创建被拒+前端校验错；log SafetyError |
| TC-5 | BC：flag 全 OFF | 技能/slash/codify 行为不变；无 skill_fn 注册；chat schemas 不变 |
| TC-6 | 候选卡 reload 持久化 | 候选卡弹出→前端 reload→卡片 rehydrate 仍可点保存 |
| TC-7 | script gating | OFF→script 技能不暴露函数；ON+allowlist→可执行 |

---

## 6. 分阶段 + flag 矩阵

```
阶段1(核心): WI-0,1,2,3,4,5,6  → 后端可注册+执行+可见性+安全+工具清单(手放 tool.json 即可测)
阶段2(需求1): WI-7,8           → 会话自创产 function call + 候选卡持久化
阶段3(需求2): WI-9,10,11       → UI 创作 + slash 标记
阶段4(危险): WI-12             → script 模式
收尾: WI-13
```

| flag | 默认 | 阶段 |
|---|---|---|
| `[skills.functions].enabled` | False | 1 |
| `[skills.functions].expose_mode` | all | 1 |
| `[skills.functions].max_recipe_steps` | 12 | 1 |
| `[skills.functions].call_timeout_seconds` | 300 | 1 |
| `[skills.codify].emit_function_call` | False | 2 |
| `[skills.functions].allow_script_impl` | False | 4 |

---

## 7. 风险 + BC

| 风险 | 缓解 |
|---|---|
| 任意代码执行 | recipe 不执行代码(白名单工具回放，每步过权限门)；script 三重 gating + builtins 沙箱 |
| 技能函数互调死循环 | `_recursion_guard` 透传 + `_FORBIDDEN_IN_RECIPE`(registry 实查含 skill_fn/skill_invoke/tool_search/agent*) |
| schema 爆炸 | `expose_mode=matched` 可选剔除未命中 skill_fn；`tool_search` 兜底发现 |
| 候选卡 reload 丢失(历史 bug#2) | WI-8 rehydrate（拉 `list_all_pending`） |
| DDL 老库无新列 | WI-7 `PRAGMA`+`ALTER TABLE` 迁移 |
| 跨层契约漂移(史踩 7 次 FP-5) | TG-4 `execute_tool` 全链路集成 + 可见性 BC 专测 + §5 真机 TC-1/2 否决 + config 嵌套段真解析测 |
| receipt 嵌套(skill_fn N 步产 N+1 条 receipt) | **R2 决策**：verify_gate 是**存在性对账**（任一 `tool_name∈tool_hint && ok=True` 即放行，[verify_gate.py:445](../../backend/deskpet/agent/verify_gate.py)）→ 内层 step receipt 只**帮助**对账、外层 skill_fn receipt 无 hint 命中=惰性噪声，**不误判**。唯一副作用：`registry.py:804` `_session_iteration` 每 step +1 → receipt.iteration 监控虚高（cosmetic）。**本 plan 不修**（打 `parent_skill_fn` 标记成本高，defer），仅声明 |
| BC | 全 flag OFF；TG-BC 字节基线；无 tool.json=现状不变；可见性仅增量过滤 skill_fn__* 新前缀 |

---

## 8. 子代理对抗挑战日志

**R1（2 并行只读子代理，2026-06-22）** — 判定 NOT-EXECUTABLE，已全部纳入 v0.2：
- **BLOCKING**：① 可见性 wiring 错位（chat `tools_filter=None` 全暴露，assembler 不是落点）→ §2.6/WI-4 重写为 `agent_loop.run(skill_fn_allowlist=)` 增量过滤；② `tc.args`→`tc.arguments`（全文改）；③ `tool_list_request` 不存在→独立 WI-6；④ reload→注册生命周期/线程安全(watchdog timer 线程、禁 asyncio.run)→WI-4 纯 sync 注册 + async 工厂 handler + 枚举构造点。
- **MAJOR**：DDL ALTER 迁移(WI-7)、config 嵌套段真解析测(WI-0)、async handler 工厂(WI-4)、invoke_script 现状澄清(WI-12/§1.1)、`source` 清场用 `list_tools(source=)`(WI-4)、save_as 语义(§2.3)、codifier 参数化 few-shot+无参降级+main.py:781 接线(WI-7)、候选卡 rehydrate(WI-8)、slash 错配决策(WI-11)、delete 顺序(WI-9)、`_FORBIDDEN` registry 实查(WI-5)、占位符顺序校验(WI-5)、前端 messages.ts/sessionsStore.ts + has_function(WI-8/§2.1)。
- **MINOR**：max_candidates_per_day 现状未强制(§1.1 澄清)、receipt 嵌套对 verify_gate 影响(待 R2 决策)、多 step 权限弹窗 UX(§2.5+TC-3)、`{name}` vs `${args[N]}` 语法(§2.3)、STATUS/docs 收尾(WI-13)、skill_invoke 双注册澄清(§1.1)、i18n 硬编码中文(WI-10)。

**R2（独立只读子代理，2026-06-22）** — 确认 R1 的 4 BLOCKING 已修对；但揪出 v0.2 自引入 1 新 BLOCKING + 5 MINOR，已纳入 v0.3：
- **BLOCKING-R2-1**：`_recursion_guard`/`session_id` 无法经 `execute_tool` 透传给 handler（真实签名 `(name,params,session_id,task_id="")` 返回 **dict**，handler 固定 2 参）→ **改 contextvars**（`_RECURSION_GUARD`/`_SKILL_FN_SESSION`，WI-3/WI-4 已重写）；连带修 WI-3 伪码：execute_tool 第 3 参是 session_id（非 task_id）、返回 dict（去掉 `json.loads`）；handler 必须 async（sync 走 run_in_executor 丢 contextvar）。
- **MINOR**：① WI-0 config 落点更正为 `config.py:1146-1157` 手工 pop+构造（非 main.py:5719）；② matched 模式数据源未坐实 → 阶段1 默认 `all`，matched 实现期 grep 确认或降级 follow-up；③ code/voice venue 已核实 tools_filter=None，skill_fn 全可见，无需单独处理；④ receipt 嵌套对 verify_gate 安全（存在性对账），iteration 虚高为 cosmetic，不修仅声明；⑤ executor session_id 走 contextvar 兜底。
- 其余核实点（可见性 schemas 剔除、DDL ALTER、tc.arguments、list_tools(source=)、_summarize 分支、invoke_script args 闲置）R2 全判可行。

**R3（独立只读子代理终验，2026-06-22）** — 代码级核实 4 点全部成立：
- contextvar 透传：`execute_tool` 对 async handler 走 `await spec.handler(...)`（[registry.py:733](../../backend/deskpet/tools/registry.py)）被 `await asyncio.wait_for(...)`（[registry.py:740](../../backend/deskpet/tools/registry.py)）包裹；executor `set` 在前、`wait_for` 子 task 在后 → 子 task 继承 set 后快照 → **递归守门生效**（前提 handler async，WI-4 已强制）。
- WI-3 伪码签名/返回值（execute_tool 第3参 session_id、返回 dict 不 json.loads）与真实一致（[agent_loop.py:2438](../../backend/agent/agent_loop.py) 同序调用 + [registry.py:875](../../backend/deskpet/tools/registry.py) 返 dict）。
- WI-0 落点 `config.py:1146-1157` 手工 pop+构造属实；`_load_section`（[config.py:484](../../backend/config.py)）按 dataclass fields 自动纳入新字段 → `emit_function_call` 自动覆盖。
- `agent_loop.run` 加 keyword-only `skill_fn_allowlist` 对全部 5 个调用点（main.py:6407 / voice_pipeline:653 / spawn_team:373 / agent_parallel_tool:616 / agent_tool:175）零冲突；chat 入口 set contextvar 与 `_agent.run` 同协程链。

**终判**: ✅ **EXECUTABLE-AS-IS（无遗留 BLOCKING，可按 plan 执行）** — 2026-06-22，经 R1→R2→R3 三轮对抗收敛。

---

## 9. 关键文件索引

| 关注点 | 文件 |
|---|---|
| 技能加载/热重载 | `backend/deskpet/skills/loader.py`(reload~283 / start~251 / watchdog~779 / __init__~226 / to_dict~99) |
| 技能自创 | `backend/deskpet/skills/skill_codifier.py`(DDL~52 / render~146 / confirm~384 / list_all_pending~259) |
| 工具注册表 | `backend/deskpet/tools/registry.py`(register~284 / unregister~405 / schemas~416 / to_openai_schema(names=)~558 / execute_tool~618,731 / list_tools(source=)~1041) |
| 工具路径录制 | `backend/deskpet/agent/tool_path.py`(record_tool~39) · `backend/agent/agent_loop.py`(record~2173 / run~605 / schemas~625 / chat 调用 main.py~6398) |
| 权限 | `backend/deskpet/types/skill_platform.py`(PermissionCategory~21) · `backend/deskpet/permissions/gate.py`(_VALID~63 / _summarize~355) |
| 安全 | `backend/deskpet/skills/marketplace/safety.py` |
| 配置 | `backend/config.py`(SkillsConfig~354-456 / 嵌套解析链 main.py~5719 注释) |
| 装配/WS | `backend/main.py`(lifespan / build_agent / _maybe_codify_skill~760,781 / 候选卡 / slash help~3747 / chat _agent.run~6398) |
| 前端 | `tauri-app/src/components/SkillStorePanel.tsx` · `code-panel/{InputBar,SlashDropdown,ws}.tsx`(候选卡 ws.ts~376) · `messages.ts` · `sessionsStore.ts` · `types/skillPlatform.ts` · `MessageBubble`(~157) |

> ⚠️ main.py 体量大、行号易漂，引用以函数名 grep 为准。
