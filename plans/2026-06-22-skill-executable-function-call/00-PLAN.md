# 优化 Plan — 技能携带「可执行 Function Call」（会话自创 + 用户自创）

> **状态**: v0.1 DRAFT（待子代理多轮对抗挑战收敛至 EXECUTABLE-AS-IS）
> **作者**: Claude (Lead)
> **日期**: 2026-06-22
> **配套调研档**: [`STATUS/AgentLoop.md`](../../STATUS/AgentLoop.md)（执行引擎源码骨架）· [`STATUS/status.md`](../../STATUS/status.md) §3 技能系统行
> **基线**: 读码核实（master）+ 4 个子代理结构化调研

---

## 0. TL;DR

桌宠现在的「技能(skill)」= 一份 `SKILL.md`：frontmatter + Markdown body，被当作 **prompt 注入**（`skill_invoke` 工具或 `/slash` 命令把 body 作为 user 消息塞进上下文），可选被 `SkillMatcher` 语义自动披露。**它不是 LLM 能直接调用的 function call / 工具**——`requires_script` 字段虽存在，但 codifier 永远硬编码 `false`，且没有任何自动生成 / UI 创作 executable 技能的路径。

本 plan 补上这条断链，落地两个用户需求：

1. **会话自创**：用户在一个 session 里**完成一个任务**后，系统不仅能（现状）把工作流提炼成 Markdown 技能，还能**额外生成一个可被 LLM 直接调用的 function call**（带参数 schema + 可复现的执行体），用户确认后即注册进 `ToolRegistry`，下次 LLM 可直接 `tool_call` 调用。
2. **用户自创**：用户能在桌宠 UI 里**手动创建**自己的技能 + 对应 function call（填名称/描述/参数/执行步骤），保存即生效。

**核心设计**：引入「可执行技能(executable skill)」概念 = `SKILL.md`（不变）+ `tool.json`（function 契约）+ 执行体（`recipe.json` 声明式步骤回放，**默认**；或 `script.py` 沙箱脚本，opt-in）。两个需求共享同一套「契约 + 动态注册 + 安全门 + 执行器」核心，分别在「codifier 自动产出」和「UI 手动产出」两端接入。

**全程 flag 默认 OFF = 字节级 BC**（遵循项目惯例 `auto_disclosure.enabled=False` / `codify.enabled=False` 模式）。

---

## 1. 现状与缺口（读码核实）

### 1.1 技能系统现状（能力清单）

| 组件 | 文件 | 现状 |
|---|---|---|
| 磁盘格式 | `<user_data>/skills/{built-in,user}/<slug>/SKILL.md` | frontmatter(`name`/`description`/`version`/`author`/`when_to_use`/`task_types`/`triggers`/`requires_script`/`user_invocable`/...) + Markdown body |
| 加载/热重载 | `backend/deskpet/skills/loader.py` (`SkillLoader`) | 扫两目录、解析 frontmatter、watchdog 1s debounce 热重载、原子交换 `_skills` dict |
| 调用 | `backend/deskpet/tools/skill_tools.py` (`skill_invoke`) | 单一工具 `skill_invoke(skill_name, arguments)`，把 body 当 **user** 消息注入；`/slash` 走同一路径 |
| 脚本执行 | `loader.py::invoke_script` (574-661) | `requires_script:true` → `sys.executable -I` 子进程 + 白名单 builtins 沙箱 + wall-clock 超时 + stdout 回收。**现状：14 个 builtin 全为 `requires_script:false`，此路径几乎闲置** |
| 自动披露 | `backend/deskpet/skills/skill_matcher.py` + `agent/assembler/components/skill.py` | flag `[skills.auto_disclosure].enabled`(默认 False)；trigger 词法 + BGE-M3 语义混合匹配，强匹配把 body 注入上下文 |
| 自创闭环 | `backend/deskpet/skills/skill_codifier.py` (WI-4.3) | 完成任务→`detect_trigger`→LLM 提候选 `{name,description,trigger_pattern,steps}`→pending 表→前端确认卡→`confirm(accept)` 落 `SKILL.md`。**`render_skill_md` 硬编码 `requires_script:false`，只产 Markdown body** |
| Marketplace | `backend/deskpet/skills/marketplace/{installer,safety}.py` | GitHub URL → clone → `validate_manifest`(name/desc/tools 白名单/permission 白名单) → 落盘 → reload；`skill_install` 权限门 |
| 工具注册表 | `backend/deskpet/tools/registry.py` | `ToolSpec`(frozen) + 运行时 `register`/`unregister`/`replace_allowed`；`execute_tool` v2 全链路（权限 gate + 熔断 + 超时 + receipt） |
| 前端 | `tauri-app/src/components/SkillStorePanel.tsx`（Installed/Marketplace/Add-by-URL 三 tab）+ `code-panel/{InputBar,SlashDropdown}.tsx`（slash 补全）+ 技能候选确认卡 | 无「创建技能」入口 |

### 1.2 关键缺口（本 plan 要填）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | **技能无法成为 LLM 直接可调用的 function call** —— 唯一桥 `skill_invoke` 只做 body 注入，不暴露每个技能为独立 function schema | `skill_tools.py` 只注册 1 个 `skill_invoke`；`loader.py` 不向 registry 注册 per-skill 工具 |
| G2 | **codifier 只产 Markdown，不产 function call** | `skill_codifier.py:146-183` `render_skill_md` 硬编码 `requires_script:false`，`_generate_candidate` 不产 parameters/recipe |
| G3 | **ToolPath 不记录 args** —— 无法把工作流参数化成可复现的 function | `tool_path.py` `ToolStep` 只有 `name/ok/corrected/recovered`，`agent_loop.py:2165-2177` record_tool 不传 args |
| G4 | **没有 UI 创作技能/function** | `SkillStorePanel.tsx` 仅安装类 tab，无创建表单 |
| G5 | **没有「可执行技能」契约与动态注册管线** | 全仓无 `tool.json`/`recipe.json` 概念；`script.py` 路径无生成器、无校验、无 UI |

---

## 2. 设计总览

```
                 ┌──────────────────────────────────────────────┐
                 │            共享核心（本 plan 主体）            │
                 │                                              │
  需求1 会话自创 ─┤  ① 可执行技能契约 (tool.json + recipe.json)   │
  (codifier 扩展)│  ② SkillFunctionExecutor (recipe 回放执行器)  ├─→ ToolRegistry
                 │  ③ 动态注册 (loader.reload → register ToolSpec)│   (LLM 可直调)
  需求2 用户自创 ─┤  ④ 安全校验 (validate_function_spec + 递归守门) │
  (UI 创作)      │  ⑤ 可见性 wiring (assembler enabled_toolsets) │
                 └──────────────────────────────────────────────┘
```

### 2.1 「可执行技能」磁盘格式（扩展，非破坏）

现有技能目录**不变**；可执行技能在原目录**新增**文件：

```
<user_data>/skills/user/<slug>/
  SKILL.md          # 不变：frontmatter + body（知识/prompt）
  tool.json         # 【新】function 契约（OpenAI function schema + 执行体引用 + 权限）
  recipe.json       # 【新·recipe 模式】参数化的工具调用序列
  script.py         # 【新·script 模式·opt-in】沙箱可执行脚本（复用 invoke_script）
```

- 一个技能**有没有** `tool.json` 决定它是否成为 function call。**无 `tool.json` = 现状纯 Markdown 技能，行为字节不变。**
- `SKILL.md` frontmatter 新增**可选**字段 `has_function: true`（仅作快速标记，真相以 `tool.json` 存在为准），缺省/false 时旧逻辑不变。

### 2.2 `tool.json` 契约（v1）

```jsonc
{
  "schema_version": 1,
  "name": "weather-ppt-report",          // 与目录 slug 对齐；注册名为 skill_fn__weather-ppt-report
  "description": "查询城市天气并生成 PPT 报告",
  "parameters": {                         // 标准 JSON Schema（OpenAI function parameters）
    "type": "object",
    "properties": {
      "city": {"type": "string", "description": "城市名"},
      "days": {"type": "integer", "description": "预报天数", "default": 3}
    },
    "required": ["city"]
  },
  "impl": "recipe",                       // "recipe"(默认) | "script"
  "permission_category": "skill_call",    // 见 §2.5；script 模式强制升级到 "shell"
  "recipe_ref": "recipe.json",            // impl=recipe 时
  "script_ref": null,                     // impl=script 时填 "script.py"
  "source": "codified" | "user",          // 产出来源
  "version": "1.0.0"
}
```

### 2.3 `recipe.json` 契约（v1，recipe 模式）

```jsonc
{
  "schema_version": 1,
  "steps": [
    {
      "tool": "web_search",                       // 必须 ∈ 已注册工具白名单
      "args": {"query": "{city} 天气 未来{days}天"}, // {param} 占位符 → 从 function call 入参替换
      "save_as": "weather"                          // 可选：把本步结果存入变量上下文
    },
    {
      "tool": "ppt_create",
      "args": {"topic": "{city}天气报告", "context": "{weather}"} // 可引用前序 save_as
    }
  ],
  "return": "last"                               // "last"(返回末步结果) | "all"(汇总各步)
}
```

**执行语义**（`SkillFunctionExecutor`）：
- 按序执行每个 step，**每步都走 `registry.execute_tool`**（→ 自动复用权限 gate / 熔断 / 超时 / receipt），不绕过任何安全层。
- `{param}` / `{save_as 变量}` 用浅层字符串模板替换（仅 string/number 值，禁止任意表达式求值）。
- 任一步 `ok=False` → 整个 function 返回 `{"ok": false, "error": "step N (<tool>) failed: ...", "result": null}`，**不吞错**（遵循 `feedback` 无静默失败）。
- **递归守门**：recipe 的 `tool` 字段**禁止**是任何 `skill_fn__*`（防技能函数互调死循环）+ 禁止 `agent_parallel`/`spawn_subagents`（对齐 deepresearch fanout 的 `_FORBIDDEN_IN_KIND` 守门思路）。

### 2.4 `ToolStep` 扩展（填 G3，为 recipe 生成提供 args）

`backend/deskpet/agent/tool_path.py`：

```python
@dataclass
class ToolStep:
    name: str
    ok: bool = True
    corrected: bool = False
    recovered: bool = False
    args: dict[str, Any] = field(default_factory=dict)     # 【新】脱敏后的入参快照
    result_digest: str = ""                                # 【新】结果摘要(<=200字, 仅供 LLM 推断参数)
```

- 仅在 `codify.enabled` 或 `functions.enabled` 任一开启时填充 args（否则保持空 dict，零开销 + BC）。
- **脱敏**：args 写入前过滤掉疑似敏感键（正则 `key|token|secret|password|api[_-]?key`），值长 > 500 截断。复用思路同 `permissions/gate.py` 的敏感识别。

### 2.5 权限分类

新增 `PermissionCategory` 取值 `"skill_call"`（`backend/deskpet/types/skill_platform.py` 的 `Literal` + 前端 `tauri-app/src/types/skillPlatform.ts` 镜像）：
- **recipe 模式** function → `permission_category="skill_call"`；首次调用弹一次窗（按 params 形状哈希缓存，同形状免重复），**但其内部每个 step 仍各自走自己工具的权限门**（双层）。
- **script 模式** function → 强制 `permission_category="shell"` + `dangerous=True`（与 `run_shell` 同级），且受 `[skills.functions].allow_script_impl`（默认 False）+ `dangerous_tools_allowlist` 双重 gating。

### 2.6 LLM 可见性（enabled_toolsets wiring）

- 所有可执行技能函数注册到**新 toolset** `"skill_function"`。
- `registry.schemas(enabled_toolsets=...)` 只在 `"skill_function"` ∈ 本轮 `enabled_toolsets` 时把它们暴露给 LLM。
- **暴露策略** `[skills.functions].expose_mode`：
  - `"matched"`（**默认**）：仅当 `SkillMatcher` 判定某技能与本轮 query 相关时，把该技能的函数所属 toolset 纳入 `enabled_toolsets`（避免技能多时 schema 爆炸）。另外 `tool_search` 元工具始终能搜到它们（registry 全表可搜）。
  - `"all"`：所有 skill_function 始终可见（技能少时方便）。
- wiring 点：`ContextAssembler` 决定 `enabled_toolsets` 的策略层（`backend/deskpet/agent/assembler/` 的 policy + `components/skill.py`，与 WI-5「SkillComponent fanout」同一处）。**实现时 grep `enabled_toolsets` 赋值点确认精确文件/行**。

---

## 3. 工作项（WI）分解

> 依赖序：WI-0 → WI-1 → (WI-2,3,4,5 共享核心) → WI-6/7(需求1) ‖ WI-8/9(需求2) → WI-10(script 模式)。
> 每个 WI 标注：改哪些文件、具体改动、验收点。**不可少做任一 WI。**

### WI-0 — 配置开关（基础，default OFF = BC）

**文件**: `backend/config.py`（`SkillsConfig` 区，约 354-456）

新增 dataclass + 段：
```python
@dataclass
class SkillsFunctionsConfig:
    """``[skills.functions]`` — 可执行技能 function call（本 plan）。OFF=字节 BC。"""
    enabled: bool = False               # 主开关：注册 + 分发 skill_function
    allow_script_impl: bool = False     # 是否允许 impl=script（危险）
    expose_mode: str = "matched"        # "matched" | "all"
    max_recipe_steps: int = 12          # recipe 步数上限（防滥用）
    call_timeout_seconds: float = 300.0 # 单次 function 调用墙钟上限

# 挂到 SkillsConfig:
@dataclass
class SkillsConfig:
    ...
    functions: SkillsFunctionsConfig = field(default_factory=SkillsFunctionsConfig)

@dataclass
class SkillsCodifyConfig:
    enabled: bool = False
    max_candidates_per_day: int = 3
    emit_function_call: bool = False    # 【新】codifier 是否额外产 tool.json/recipe.json
```

**config.toml** 文档示例段（注释说明，默认全 OFF）。

**验收**: `pytest` 解析 config 不报错；未设段时 `cfg.skills.functions.enabled is False`；字节级 BC 测试（不开 flag 时所有现有行为不变）。

---

### WI-1 — `ToolStep` 扩展 + recorder 捕获 args（填 G3）

**文件**:
- `backend/deskpet/agent/tool_path.py`：`ToolStep` 加 `args`/`result_digest`（见 §2.4），`ToolPathRecorder.record_tool(...)` 增可选 `args`/`result_digest` 形参（默认 None → 不填，BC）。
- `backend/agent/agent_loop.py`（record_tool 调用点 ~2165-2177）：当 `self.tool_path_recorder` 非空**且** codify/functions flag 开（通过构造期传入的布尔标志判断），把 `tc.args`（脱敏后）与结果摘要传入。脱敏 helper 放 `tool_path.py` 模块级 `_sanitize_args(args) -> dict`。

**验收**: 新增 `test_tool_path_args_capture.py`：
- record_tool 不传 args → `step.args == {}`（BC）。
- 传敏感键 → 被过滤；长值 → 截断。
- flag OFF 时 agent_loop 不传 args（mock recorder 断言 args 为空）。

---

### WI-2 — 可执行技能契约 + parser

**文件（新）**: `backend/deskpet/skills/function_spec.py`
- `@dataclass(frozen=True) FunctionSpec`：映射 `tool.json`（name/description/parameters/impl/permission_category/recipe/script_ref/source/version）。
- `@dataclass RecipeStep` / `Recipe`：映射 `recipe.json`。
- `load_function_spec(skill_dir: Path) -> FunctionSpec | None`：目录无 `tool.json` 返回 None（→ 纯 Markdown 技能）；有则解析 + 校验（调 WI-5 的 `validate_function_spec`），失败 raise `FunctionSpecError`。
- `render_tool_json(...)` / `render_recipe_json(...)`：把候选 dict 渲染成磁盘 JSON（codifier / UI 共用）。

**文件**: `backend/deskpet/skills/loader.py`
- `SkillMeta` 加字段 `function_spec: FunctionSpec | None = None`、`has_function: bool`。
- 扫描每个 skill 目录时调 `load_function_spec`，把结果挂到 `SkillMeta`；解析失败 → warn + skip function（但**不影响** Markdown 技能加载，soft-fail，对齐现有 reload 容错）。

**验收**: `test_function_spec.py`：往返渲染/解析、缺 tool.json → None、坏 JSON → 抛错且不污染其余技能。

---

### WI-3 — `SkillFunctionExecutor`（recipe 回放执行器）

**文件（新）**: `backend/deskpet/skills/skill_function_executor.py`

```python
class SkillFunctionExecutor:
    def __init__(self, *, registry, skill_loader, cfg, recursion_guard: set[str] | None = None): ...

    async def execute(self, slug: str, params: dict, task_id: str) -> str:
        """返回 JSON 字符串 envelope {"ok":bool,"result":str|None,"error":str|None}。"""
        # 1. 取 SkillMeta.function_spec；缺/无 tool.json → ok=False
        # 2. flag 检查：cfg.skills.functions.enabled? impl=script 还要 allow_script_impl
        # 3. 参数校验：必填项齐全；按 parameters schema 浅校验类型
        # 4. impl=recipe:
        #    - 防递归：slug ∈ recursion_guard → ok=False("skill function recursion blocked")
        #    - 逐 step: 模板替换 args（{param} + {save_as}）→ 校验 tool ∉ 禁用集 → registry.execute_tool(tool, args, task_id)
        #    - step.ok=False → 立即返回 error（不吞）
        #    - save_as → 存进局部 vars
        #    - 步数 > cfg.max_recipe_steps → 截断报错
        #    - 整体超 call_timeout_seconds → asyncio.wait_for 取消并报错
        #    - 按 recipe.return 组织返回
        # 5. impl=script: 委托 skill_loader.invoke_script(slug, args)（复用现有沙箱）
```

- 递归守门：execute 入口把 `slug` 加入传给内层 execute_tool 的 recursion_guard（通过 task context 或 executor 实例传递），内层若再触发 skill_fn 则拒绝。**禁用工具集** `_FORBIDDEN_IN_RECIPE = {agent_parallel, spawn_subagents} ∪ {所有 skill_fn__* 前缀}`。

**验收**: `test_skill_function_executor.py`（全 mock registry）：
- 单步 recipe 成功 → ok=True，结果透传。
- 多步 + save_as 变量引用 → 替换正确。
- 中间步 fail → 立即返回 error、后续步不执行。
- 缺必填参数 → ok=False。
- 递归（recipe 调自身 skill_fn）→ 被守门拦。
- 超 max_recipe_steps / 超时 → 报错。
- flag OFF → ok=False("skill_functions disabled")。

---

### WI-4 — 动态注册 + 可见性 wiring（填 G1）

**文件（新）**: `backend/deskpet/skills/skill_function_registrar.py`
- `register_skill_functions(registry, skill_loader, executor, cfg) -> list[str]`：遍历 `skill_loader` 所有带 `function_spec` 的技能，为每个构造 `ToolSpec`：
  - `name = f"skill_fn__{slug}"`（命名空间隔离，避免撞内置工具）
  - `toolset = "skill_function"`
  - `schema = {"type":"function","function":{"name":name,"description":...,"parameters":spec.parameters}}`
  - `handler = lambda args, task_id: asyncio.run/await executor.execute(slug, args, task_id)`（async handler，registry 支持）
  - `permission_category = spec.permission_category`（script→shell）、`dangerous = (impl==script)`、`replace_allowed=True`、`timeout_seconds = cfg.functions.call_timeout_seconds`、`concurrency_safe=False`（recipe 内含写操作可能）、`source="skill_function"`
  - 注册前先 `registry.unregister(name)`（幂等，支持 reload 后重注册）
- `unregister_all_skill_functions(registry)`：reload 前清场（按 `skill_fn__` 前缀枚举 unregister）。

**文件**: `backend/deskpet/skills/loader.py`
- `reload()` 末尾（原子交换 `_skills` 之后）：若 `cfg.skills.functions.enabled` → 调 `unregister_all_skill_functions` + `register_skill_functions`。需要 loader 持有 registry/executor/cfg 引用（构造期注入，可选，None 时跳过 = BC）。

**文件**: `backend/main.py`（lifespan + build_agent）
- lifespan 构造 `SkillFunctionExecutor` 并注入 `SkillLoader`（仅 `functions.enabled` 时）。
- 启动 `skill_loader.start()` 后做一次初始注册。

**文件**: `backend/deskpet/agent/assembler/`（`components/skill.py` + policy）
- 把 `"skill_function"` 纳入 `enabled_toolsets`：
  - `expose_mode="all"` → 直接把 `"skill_function"` 加入各 policy 的 enabled toolset。
  - `expose_mode="matched"` → 当 `SkillMatcher` 命中带 function 的技能时，把 `"skill_function"`（或更细：仅该工具名，需 registry.schemas 支持按 name 过滤——若不支持则退化为按 toolset）纳入。**实现时确认 `schemas()` 是否支持 per-name 过滤；不支持则本 WI 增加该能力**（registry.py `schemas()` 加可选 `enabled_tool_names` 参数）。

**验收**:
- `test_skill_function_registrar.py`：注册产生正确 ToolSpec；reload 幂等（不抛 conflict）；unregister 清场干净；flag OFF → 不注册。
- `test_skill_function_visibility.py`：`expose_mode=all` 时 `schemas()` 含 `skill_fn__*`；`matched` 时仅命中后含。
- 集成：`test_skill_function_e2e_dispatch.py`：注册一个 recipe 技能 → `execute_tool("skill_fn__x", {...})` 走通权限/熔断/receipt 全链路。

---

### WI-5 — 安全校验 + 递归守门（填安全面）

**文件**: `backend/deskpet/skills/marketplace/safety.py`（或新 `skills/safety_function.py` 复用其常量）
- `validate_function_spec(tool_json: dict, recipe_json: dict | None, *, known_tools: set[str], cfg) -> None`（违规 raise `SafetyError`）：
  - `name` 符合 `^[a-zA-Z0-9_\-]{1,64}$`；`description` 非空。
  - `parameters` 是合法 JSON Schema 子集（type=object、properties 是 dict、required ⊆ properties）。
  - `impl ∈ {recipe, script}`；`impl=script` 时若 `not cfg.functions.allow_script_impl` → 拒绝。
  - `permission_category ∈ PermissionCategory` 白名单。
  - recipe 模式：每个 step.tool ∈ `known_tools` 且 ∉ `_FORBIDDEN_IN_RECIPE`；步数 ≤ `cfg.max_recipe_steps`；args 模板里 `{...}` 占位符必须能在 parameters 或前序 save_as 里解析到（静态检查未定义变量）。
- 复用现有 `validate_manifest` 的 known_tools 白名单获取方式（registry 全表名集）。

**验收**: `test_function_safety.py`：坏 name/未知工具/递归工具/未定义占位符/script 未开 flag → 各自 SafetyError；合法 → 通过。

---

### WI-6 — 需求1：codifier 扩展（自动产 function call，填 G2）

**文件**: `backend/deskpet/skills/skill_codifier.py`
- `_CODIFY_PROMPT` 扩展：当 `emit_function_call` 开，要求 LLM 额外输出：
  - `parameters`：从工作流里**抽象出的可变输入**（哪些 step args 是参数，哪些是常量）的 JSON Schema。
  - `recipe`：参数化后的步骤序列（`[{tool, args(含{param}), save_as?}]`）。
  - prompt 里喂入 `path.steps[].name` + **`path.steps[].args`**（WI-1 已捕获）+ `result_digest`，让 LLM 能正确推断参数与模板。
- `_generate_candidate`：解析并校验扩展字段；调 `validate_function_spec`（WI-5）；任一不过 → 退化为「仅 Markdown 候选」（不阻断原有闭环）。
- `render_skill_md`：当有 function → frontmatter 加 `has_function: true`（仍保留 `requires_script:false`，因为 recipe 模式不走 script）。
- `confirm(accept=True)`：除写 `SKILL.md` 外，若候选含 function → 调 `render_tool_json`/`render_recipe_json` 写 `tool.json` + `recipe.json`，再 `loader.reload()`（reload 内 WI-4 会注册函数）。
- pending 表 DDL 扩展：`pending_skill_candidates` 加 `function_json TEXT`（存候选 function dict），`SkillCandidateStore.write_pending/fetch_pending` 带上。

**验收**: `test_skill_codifier_function.py`：
- `emit_function_call=False` → 行为字节同今天（只产 Markdown，无 tool.json）。
- ON + LLM 返回合法 function → confirm 后磁盘出现 tool.json/recipe.json 且 reload 后 registry 有 `skill_fn__<slug>`。
- LLM 返回非法 function → 降级为纯 Markdown，不抛、不阻断。
- pending 往返含 function_json。

---

### WI-7 — 需求1：前端候选卡展示 function 签名

**文件**: `tauri-app/src/` 候选确认卡组件（`SkillStorePanel.tsx` 内或独立 `SkillCandidateCard`）+ `code-panel/ws.ts` + 类型 `types/skillPlatform.ts`
- `skill_candidate_proposed` WS payload 扩展 `function`（name/parameters/recipe 摘要）。
- 候选卡新增「可执行函数」区：展示函数名 + 参数列表 + 步骤预览；保存按钮文案区分「保存为知识技能」vs「保存为可执行技能(含函数)」。
- 确认/拒绝沿用现有 `skill_candidate_confirm` 通道，无需新 WS 类型。

**验收**: `vitest` 渲染测试（卡片含 function 区 / 无 function 时退化）；`tsc --noEmit` 0 err。真机见 §5 TC-1。

---

### WI-8 — 需求2：用户自创后端（`SkillAuthoringService` + WS）

**文件（新）**: `backend/deskpet/skills/authoring.py`
- `SkillAuthoringService(skill_loader, registry, cfg)`：
  - `draft(spec_dict) -> dict`：校验（`validate_function_spec` + frontmatter 必填）+ 返回预览（渲染好的 SKILL.md/tool.json/recipe.json 文本，不落盘）。
  - `create(spec_dict) -> str`：再校验 → 解析 slug（碰撞 `-v2`，复用 `skill_codifier._resolve_skill_dir` 逻辑，抽到共享 util）→ 写 `SKILL.md`(+`tool.json`+`recipe.json`/`script.py`) → `loader.reload()` → 返回 slug。
  - `update(slug, spec_dict)` / `delete(slug)`：仅限 `user/` 目录（禁改 built-in）；delete 同时 `unregister` 其函数。

**文件**: `backend/main.py`（WS handler）
- 新增消息类型：`skill_create_draft`（→ draft 预览）、`skill_create_confirm`（→ create）、`skill_function_update`、`skill_function_delete`。回响应消息 `skill_create_result` 等。沿用现有 `service_context` 取 service 的模式。
- 全部 gated by `cfg.skills.functions.enabled`；OFF 时 handler 返回「功能未启用」。

**验收**: `test_skill_authoring.py`：draft 不落盘、create 落盘 + reload + 注册、update/delete 生效、禁改 built-in、非法 spec 拒绝、flag OFF 时 handler short-circuit。`test_main_skill_authoring_wiring.py`：WS 往返。

---

### WI-9 — 需求2：用户自创前端（创建 tab + function builder）

**文件**: `tauri-app/src/components/SkillStorePanel.tsx`（加第 4 tab「创建/Create」）+ 新 `SkillCreateForm.tsx` + `code-panel/ws.ts` + `types/skillPlatform.ts`
- 表单字段：name、description、when_to_use/triggers、body(markdown 文本域)、impl 选择(recipe/script)、参数构建器（动态增删 {name,type,required,description,default} 行）、recipe 构建器（从已注册工具下拉选 tool + 为每步映射 args 模板，引用参数/前序 save_as）、script 文本域（仅 `allow_script_impl` 开时显示，带醒目危险提示）。
- 「预览」按钮 → `skill_create_draft` → 展示渲染结果；「创建」→ `skill_create_confirm` → 成功后刷新 Installed tab。
- 工具下拉数据：复用现有「已注册工具列表」WS 查询（若无则新增 `tool_list_request`）。

**验收**: `vitest` 表单交互（增删参数行、recipe step、预览/创建调用正确 WS）；`tsc` 0 err。真机见 §5 TC-2/TC-3。

---

### WI-10 — script 模式（opt-in，复用 invoke_script 沙箱）

**文件**: `backend/deskpet/skills/skill_function_executor.py`（impl=script 分支）+ `loader.py::invoke_script`（确认入参/出参约定：JSON params → 脚本，stdout JSON ← 脚本）
- script 模式：`tool.json.impl="script"` + `script.py` 存在；执行委托 `invoke_script`，但**入参从 `${args[N]}` 位置参数升级为 JSON**（约定脚本读 `sys.argv[1]` = JSON 字符串 / 或 stdin）。需小改 invoke_script 支持 JSON 入参（向后兼容旧位置参数）。
- 严格 gating：`allow_script_impl=False`（默认）→ 注册时跳过 script 技能的函数（仍可作纯 Markdown 技能）；ON 时函数 `dangerous=True` + 需 `dangerous_tools_allowlist` 放行 + 调用走 `shell` 权限门。

**验收**: `test_skill_function_script.py`：allow_script_impl OFF → 不注册函数；ON → 沙箱执行、超时、错误回收；JSON 入参往返；危险 gating 生效。

---

## 4. TDD 测试组（汇总）

| TG | 文件 | 覆盖 WI |
|---|---|---|
| TG-0 | `test_skills_functions_config.py` | WI-0 config 解析 + BC |
| TG-1 | `test_tool_path_args_capture.py` | WI-1 args 捕获 + 脱敏 + flag gating |
| TG-2 | `test_function_spec.py` | WI-2 契约往返/解析容错 |
| TG-3 | `test_skill_function_executor.py` | WI-3 recipe 执行/守门/超时/不吞错 |
| TG-4 | `test_skill_function_registrar.py` + `test_skill_function_visibility.py` + `test_skill_function_e2e_dispatch.py` | WI-4 注册/可见性/全链路分发 |
| TG-5 | `test_function_safety.py` | WI-5 安全校验 |
| TG-6 | `test_skill_codifier_function.py` | WI-6 codifier 扩展 + 降级 |
| TG-7 | `test_skill_authoring.py` + `test_main_skill_authoring_wiring.py` | WI-8 用户自创后端 |
| TG-8 | `test_skill_function_script.py` | WI-10 script 模式 + 危险 gating |
| TG-BC | `test_skill_functions_byte_baseline.py` | **全 flag OFF → 现有技能/codify/工具行为字节级不变** |

前端：`vitest` 候选卡 + 创建表单；`tsc --noEmit` 0 err。

**铁律**：每个 TG 必含 flag-OFF BC 断言（项目宪法：默认 OFF 字节级 BC）。

---

## 5. 真机手测用例（windows-mcp，遵循项目「真模拟人工」硬约束）

> 凭 `LOCAL-DEV-CREDENTIALS.md` 登录走真 LLM。每用例：截图→真坐标点击/输入→截图→抓 backend log 判 PASS。落盘 `plans/manual-results-2026-06-22-skill-fn/`。

| TC | 场景 | 期望硬证据 |
|---|---|---|
| **TC-1★（一票否决）** | 需求1：真发多步任务（如"查北京天气并生成 PPT"）→ agent 完成→候选卡含「可执行函数」区→真点「保存为可执行技能」 | 磁盘出现 `<user>/skills/user/<slug>/tool.json`+`recipe.json`；backend log `skill_fn_registered name=skill_fn__<slug>`；**下一轮**对话直接出现 LLM `tool_call skill_fn__<slug>(city=...)` 并成功 |
| **TC-2★（一票否决）** | 需求2：打开技能面板「创建」tab→填名/描述/2 参数/2 步 recipe→预览→创建 | 预览正确渲染；创建后 Installed tab 出现新技能；磁盘 3 文件；log 注册成功 |
| TC-3 | 需求2：调用刚创建的函数 | 真对话触发→`execute_tool skill_fn__<slug>`→权限弹窗一次→执行 recipe 各步→返回结果卡 |
| TC-4 | 安全：recipe 引用未知工具/递归 skill_fn | 创建被拒（前端报校验错）；log `SafetyError` |
| TC-5 | BC：flag 全 OFF | 现有技能/slash/codify 行为不变；无 `skill_fn__*` 注册；`tool_search` 搜不到 |
| TC-6 | script 模式 gating | `allow_script_impl=False` 时 script 技能不暴露函数；ON + allowlist 后可执行 |

---

## 6. 分阶段落地 + flag 矩阵

```
阶段1（共享核心）: WI-0,1,2,3,4,5  → 后端可注册+执行可执行技能（无产出端，靠手放 tool.json 测）
阶段2（需求1）   : WI-6,7          → 会话自创产 function call
阶段3（需求2）   : WI-8,9          → UI 手动创作
阶段4（危险增强） : WI-10           → script 模式
```

| flag | 默认 | 阶段 | 作用 |
|---|---|---|---|
| `[skills.functions].enabled` | False | 1 | 主开关：注册 + 分发 |
| `[skills.functions].expose_mode` | matched | 1 | LLM 可见策略 |
| `[skills.functions].max_recipe_steps` | 12 | 1 | recipe 上限 |
| `[skills.functions].call_timeout_seconds` | 300 | 1 | 调用墙钟 |
| `[skills.codify].emit_function_call` | False | 2 | codifier 产函数 |
| `[skills.functions].allow_script_impl` | False | 4 | 放行 script 模式 |

---

## 7. 风险 + BC 保证

| 风险 | 缓解 |
|---|---|
| **任意代码执行** | recipe 模式不执行代码（仅按白名单工具回放，每步过权限门）；script 模式三重 gating（flag+allowlist+shell 权限门）+ 复用现有 builtins 沙箱 |
| **技能函数互调死循环** | `_FORBIDDEN_IN_RECIPE` 禁 skill_fn__* + recursion_guard；对齐 deepresearch fanout 守门 |
| **schema 爆炸**（技能多→LLM 工具列表过长） | `expose_mode=matched` 默认仅披露相关技能函数；其余靠 `tool_search` 按需找 |
| **codifier 产出低质函数** | 用户确认门（沿用 WI-4.3）+ 校验失败降级为纯 Markdown |
| **跨层契约漂移**（单测绿但生产死，项目历史踩过 7 次） | TG-4 含 `execute_tool` 全链路集成测 + §5 真机 TC-1/TC-2 一票否决 + `enabled_toolsets` wiring 专测（对标 FP-5 缺口 5a-5i 教训） |
| **BC 破坏** | 所有 flag 默认 OFF；TG-BC 字节基线；`tool.json` 不存在 = 现状技能不变 |

---

## 8. 子代理对抗挑战日志

> 本 plan 须经子代理多轮只读对抗挑战（验证每处 file:line 引用真实存在、每个接线点可落地、无遗漏功能），逐轮纳入修复，收敛至 EXECUTABLE-AS-IS 方可执行。

- R1: _待填_
- R2: _待填_
- ...
- 终判: _待填_

---

## 9. 关键文件索引（实现期 grep 锚点）

| 关注点 | 文件 |
|---|---|
| 技能加载/热重载 | `backend/deskpet/skills/loader.py` |
| 技能自创 | `backend/deskpet/skills/skill_codifier.py` |
| 工具注册表 | `backend/deskpet/tools/registry.py`（`register`/`unregister`/`schemas`/`execute_tool`） |
| 工具路径录制 | `backend/deskpet/agent/tool_path.py` · `backend/agent/agent_loop.py`(record_tool ~2165) |
| 权限分类 | `backend/deskpet/types/skill_platform.py` · `backend/deskpet/permissions/gate.py` |
| 安全校验 | `backend/deskpet/skills/marketplace/safety.py` |
| 自动披露/可见性 | `backend/deskpet/skills/skill_matcher.py` · `backend/deskpet/agent/assembler/components/skill.py` |
| 配置 | `backend/config.py`（`SkillsConfig` ~354-456） |
| 装配/WS | `backend/main.py`（lifespan / build_agent / `_maybe_codify_skill` ~731-841 / WS handlers） |
| 前端技能 UI | `tauri-app/src/components/SkillStorePanel.tsx` · `code-panel/{InputBar,SlashDropdown,ws}.tsx` · `types/skillPlatform.ts` |

> ⚠️ main.py 体量大、行号易漂，引用处以函数名 grep 为准。
