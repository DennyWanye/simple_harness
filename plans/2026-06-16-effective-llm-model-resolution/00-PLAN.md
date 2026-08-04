# 有效出站 LLM 模型解析统一 — 实施 PLAN（根治 P-B）

> **状态**: 📋 规划中
> **目标**: 让所有"需要知道当前出站 LLM 模型"的代码读到**运行时有效模型**（onboarding 写的
> `llm_runtime.json` 覆盖后的 gpt-5.5），而不是 `config.raw["llm"]["model"]` 的**旧种子值**
> （gemma4:e4b）。根治压缩窗口按错模型算（P-B，见 2026-06-16-context-compaction-optim §3.5）。
> **最后更新**: 2026-06-16

## 1. 背景与根因（读真实代码得出，非凭空）

DeskPet 的 LLM 模型有两层：
- **种子值**：`config.toml [llm] model`（出厂默认 `gemma4:e4b`，见 `config.py:88`）。
- **运行时覆盖**：`backend/userdata/llm_runtime.json`（onboarding 登录写：`chinzy.com 中转站 gpt-5.5`）。

加载流程（`main.py`）：
```
148  LLM_RUNTIME_PATH = user_data_dir/llm_runtime.json
183  _llm_overrides = _load_llm_runtime_overrides()      # {base_url, model, temperature}
188  config.llm.local.model = _llm_overrides["model"]    # ★ 覆盖只写进 dataclass 字段
```

**根因**：覆盖在 `main.py:188` 只写进了 **`config.llm.local.model`（dataclass）= gpt-5.5**，
**没同步回 `config.raw["llm"]["model"]`（原始 toml dict）→ 它还是 `gemma4:e4b`**。
于是"读 dataclass 的代码"对，"读 raw dict 的代码"错。

**读对的（用 `config.llm.local.model`，正确）**：`main.py:242`(主 LLM 构造)、`266`(cloud,但 cloud_llm 已恒 None)、`2684/2688`。
> ✅ **R1 核准主出站源**：`main.py:239-245` 的 `local_llm = OpenAICompatibleProvider(model=config.llm.local.model,...)`
> 是唯一真出站 provider；`cloud_llm` 在 `main.py:254` 恒 `None`（统一 `[llm]` schema 下不再构造 cloud）。
> 压缩器 `main.py:1376` `_compactor_llm = local_llm or cloud_llm` = `local_llm`。
> **所以「有效出站模型 = `config.llm.local.model`」假设成立**（`LLMRoutingConfig.strategy="cloud_first"`
> 是历史 HybridRouter 字段，当前部署 cloud 缺席 → 不影响有效模型，访问器不必读 cloud）。

**读错的（用 `config.raw["llm"]["model"]` 或不可达单例，拿到旧 gemma / hardcode 回落）**：
| 位置 | 实际读法 | 用途 | 后果 |
|---|---|---|---|
| `main.py:1384` | `config.raw["llm"]["model"]` | **压缩窗口/阈值解析**（ContextCompressor 初始化） | 按 gemma 32K 算阈值 → gpt-5.5 1M 时过早压缩浪费窗口（P-B 本体） |
| `main.py:4268` | `config.raw["llm"]["model"]` | `_stub_model`（context_usage ring 的占位 model 名，**发给前端 UI**） | 前端模型环显示旧 gemma 名 + 错窗口 |
| `main.py:2944` → `persona.py:91-93` | `_resolve_persona(config.raw)` 读 `raw["llm"]["model"]` | **`_breakdown` 调试探针**（context breakdown UI 面板的 persona 预览片段，**非系统提示**） | ⚠️ **R2 证伪 R1**：真 persona system prompt 走 assemble(:5304) 传的 `local_llm.model`（已对），**这处只是调试面板预览显示 stale 名（cosmetic）**。WI-2 同步 raw 顺带修，非主修点 |
| `deskpet/tools/ppt_visual_review.py:155` | `_cfg.config.raw...`（**`_cfg.config` 属性不存在**） | PPT 视觉复审取模型名 | ★ **R1 证伪**：`import config as _cfg; _cfg.config` 抛 AttributeError → 被 except 吞 → 回落 hardcode `"gpt-5.5"`。**当前因 hardcode 恰好对而没坏**，但换模型时它读不到有效值 |

> ⚠️ **R1 关键机制澄清（决定修法）**：AppConfig 单例是 **`main.py:93` 的 `config` 全局**，
> **`config` 模块本身没有 `config` 属性**（见 `research_tools.py:126-131` 记录的真机 bug TC-P2-05：
> 各工具 `import config as _cfg; _cfg.config.raw` 全部 AttributeError 被吞、配置恒取默认）。
> 因此 **out-of-main 工具（ppt_visual_review / ppt_tools / image_tools …）拿不到 main 那个被覆盖过的
> dataclass**。它们的可靠运行时源是 **`<user_data>/llm_runtime.json`**（onboarding 写的 base_url+model，
> 见 `image_tools._resolve_endpoint` 直读该文件）——`llm_runtime.json` 里就有 `"model":"gpt-5.5"`。

**不在本次范围（R1 核准）**：`llm/registry.py:132` `main_model`（`[[llm.providers]]` 多 provider namespace）
是 **settings 面板的 provider CRUD/probe 子系统**（`main.py:296` `LLMProviderRegistry`，只服务
`settings_providers_*` ws handler），**不在 local_llm 真出站链路**，与 P-B 压缩/persona/stub 无关 → 不改。

> 注：本会话已临时把 `userdata/config.toml [llm] model` 手改成 `gpt-5.5` 堵上（让 raw 也对）。
> 但那是**用户本地 config 的临时对齐**，新用户/换模型仍会种子 gemma → raw stale。**根治要在代码层**。

## 2. 决策

> **R1 修订：区分两类读取点，因为它们能不能拿到 main 的 dataclass 不同 —— 一刀切访问器会假修。**

- **真源**：以 `config.llm.local.model`（main.py:188 运行时覆盖已应用）为"有效出站模型"的真源。
- **In-process 读取点**（`main.py:1384` 压缩窗口、`4268` stub、`2944` breakdown 调试探针）读 `config.raw`
  → **走访问器 `effective_llm_model(config)`** 或靠 WI-2 同步 raw：优先 `cfg.llm.local.model`，空回落
  `cfg.raw["llm"]["model"]`，再空回落 BUILTIN 默认。
  > **R2 修订**：真 persona system prompt 注入（`main.py:5304 assemble(config={"llm":{"model":local_llm.model}})`）
  > **本就读对 gpt-5.5,不是 stale 读取点**（第1轮误把 :2944 breakdown 探针当 persona 主注入,R2 已证伪,从本类删除 persona）。
- **Out-of-main-单例-作用域 工具**（`ppt_visual_review.py`,与 main 同进程但 `config.config` 属性不存在）**拿不到 main 的 dataclass**
  （`_cfg.config` 不存在，R1 已证伪）→ **不能传 `effective_llm_model(_cfg.config)`**，否则照样 AttributeError。
  改为读 **`llm_runtime.json` 的 `model`**（onboarding 写的有效值），回落 `load_config(resolve_config_path()).raw["llm"]["model"]`，
  再回落原 hardcode。**建议把这段做成 `config.py` 的独立 helper `effective_llm_model_standalone()`**（不依赖 main 单例，
  自己读 llm_runtime.json + 磁盘 config），供所有工具复用，根治 `research_tools.py:126` 记录的"工具读不到配置"通病。
- **raw 同步（belt-and-suspenders）**：`main.py:188` 应用覆盖时**也写 `config.raw["llm"]["model"] = model`**。
  > R1 判定：raw 同步与 in-process 访问器**部分冗余但不矛盾**——同步后 1384/4268/breakdown探针 即便忘了改也读对，
  > 是低成本保险，保留。但它**只覆盖 in-process raw 读取**，对 out-of-main 工具无效（那些读的是磁盘 config / llm_runtime.json，
  > 不是 main 的内存 raw）→ 所以 ppt 路径**必须**走 llm_runtime.json 直读，不能只靠 WI-2。
  > **R2 修订**：WI-2 救的 in-process raw 读取点 = `:1384` 压缩窗口 + `:4268` stub + `:2944` breakdown 调试探针，
  > **不含 persona**（真 persona 不读 raw,见上）。R2 复核执行顺序确认 `config.raw` 全程同一对象、无 deepcopy → 同步可见。
  > 若同步 base_url（可选）能顺带让 breakdown 探针的 base_url 预览也对（真 persona 的 base_url 同样走 local_llm,本就对）。

## 3. 范围与改法（WI）

### WI-1 加访问器（两个，分别给 in-process / standalone）
- 在 `config.py` 加 `def effective_llm_model(cfg: AppConfig) -> str`（in-process 用）:
  优先 `cfg.llm.local.model` → `cfg.raw.get("llm",{}).get("model")` → BUILTIN 默认 `"gemma4:e4b"`（保持兜底）。
  纯函数,空安全。
- **R1 新增** 在 `config.py` 加 `def effective_llm_model_standalone() -> str`（工具用,**不依赖 main 单例**）:
  优先读 `<user_data>/llm_runtime.json` 的 `model` → 回落 `load_config(resolve_config_path()).raw["llm"]["model"]`
  → 回落 BUILTIN 默认。**全程不碰 `config.config`**（那属性不存在）。失败静默返默认。
  > **R2 实现提示**：抄 `image_tools._resolve_endpoint`(:94-134) 的 IO 模式——`from paths import user_data_dir;
  > rt = user_data_dir()/"llm_runtime.json"; data = json.loads(rt.read_text(encoding="utf-8"))` + 宽 except。
  > 但**注意 image_tools 只从该文件取 base_url/api_key,不取 model**(它 model 走 `[image]` 段);standalone 是
  > **新读该文件的 `model` 字段**(onboarding 经 `_save_llm_runtime_overrides` 写入,字段确实存在)。
  > `user_data_dir()` 用 `DESKPET_USER_DATA_DIR` env → 与 main 同进程同目录,R2 已核实一致。
- 单测: ① `effective_llm_model`: dataclass 有值时返 dataclass;dataclass 空 raw 有值时返 raw;都空返默认。
  ② `effective_llm_model_standalone`: llm_runtime.json 有 model 返它;只有 base_url 没 model→回落磁盘 config;
  两者都缺→BUILTIN。(用 tmp_path + monkeypatch `paths.user_data_dir`)

### WI-2 main.py:188 应用覆盖时同步 raw
- 在 `config.llm.local.model = _llm_overrides["model"]` 之后,加
  `config.raw.setdefault("llm", {})["model"] = _llm_overrides["model"]`（同步 raw,使 1384/4268 即便
  不改也读对——但仍按 WI-3 改读访问器,双保险）。base_url 同理可同步(可选,本期可只同步 model)。

### WI-3 stale 读改走访问器（R2 修订：3 处真 in-process raw 读 + 1 处 standalone）
**In-process（读 `config.raw`，走 `effective_llm_model(config)` 或靠 WI-2 同步）：**
- `main.py:1384`（压缩窗口）: `(config.raw.get("llm") or {}).get("model")` → `effective_llm_model(config)`。
- `main.py:4268`（_stub_model，发给前端 model 名）: 同上。
- `main.py:2944`（`_breakdown` 调试探针，**非 system prompt**）: `_resolve_persona(config.raw)` 读 stale raw,
  但**只影响 context breakdown UI 面板的 persona 预览片段显示的模型名**。靠 WI-2 同步 raw 即自动修对(它读 `config.raw`)。
  **不必改 persona.py**。
  > ⚠️ **R2 证伪 R1**：这**不是**真 persona/system prompt 注入。真注入走 `main.py:5304 assemble(config={"llm":
  > {"model":_persona_model}})`,`_persona_model=getattr(local_llm,"model")=config.llm.local.model`(覆盖后 gpt-5.5)
  > **本就读对**。第1轮把此 breakdown 探针误当 persona 主注入,R2 已纠正。无 persona bug 可修。

**Out-of-singleton 工具（走 `effective_llm_model_standalone()`）：**
- `deskpet/tools/ppt_visual_review.py:155`: 现写法 `_cfg.config.raw...` **恒 AttributeError→回落 hardcode**
  （R1 已证伪，当前靠 hardcode "gpt-5.5" 侥幸不坏）。改为 `effective_llm_model_standalone()`，
  这样换模型时也读对。**不要**改成 `effective_llm_model(_cfg.config)`（会继续踩 `_cfg.config` 不存在的坑）。

### WI-4 压缩窗口解析正确性（P-B 验收点）
- `main.py:1376-1402` 压缩窗口解析改用 `effective_llm_model(config)` 喂 `model_info.resolve`。
- 验收: 设 `llm_runtime.json model=gpt-5.5` + 用户档 1M override → 启动日志
  `wi4_0_compaction_enabled context_window=1000000`（而非 32000）。

## 4. 测试
- 单测: `effective_llm_model` 三分支 + **R1 `effective_llm_model_standalone` 三分支**（llm_runtime.json 优先 /
  只 base_url 无 model 回落磁盘 config / 全缺 BUILTIN）;压缩窗口解析在"config 种子 gemma + runtime 覆盖
  gpt-5.5(1M)"下解析出 1M 而非 32K（mock config + llm_runtime）。
- **R2 改写 persona 测试**（R1 的「persona 单测」基于误诊,删原意；改为**回归守护**）:
  断言真 persona 注入读 `local_llm.model` 而非 `config.raw`——构造 `config.raw["llm"]["model"]="gemma4:e4b"`
  但 `local_llm.model="gpt-5.5"`,走 `assemble(config={"llm":{"model":local_llm.model}})` 路径 → persona 文本含
  gpt-5.5。**可证伪「未来有人误把 persona 改成读 raw」**。（注：breakdown 探针 :2944 读 raw 由 WI-2 同步覆盖,
  非系统提示,不单独测。）
- 集成: 模拟 main 启动序(应用 override 后) → `config.raw["llm"]["model"]` 与 `config.llm.local.model`
  一致 + 压缩器 context_window 为有效模型窗口 + stub model 名 = gpt-5.5。
- **R1 边界可证伪用例**:
  - 纯本地 ollama 无 `llm_runtime.json` → in-process 访问器=config 种子;standalone 回落磁盘 config;行为不变（BC）。
  - `llm_runtime.json` 只有 `base_url` 没 `model` → standalone **不能**误用空串,要回落磁盘 config 的 model。
  - `llm_runtime.json` 损坏/非法 JSON → standalone 静默回落,不抛。
  - **R2 新增** standalone 用 `DESKPET_USER_DATA_DIR` monkeypatch 指向 tmp_path → 断言读到的是
    **与 main 同一目录**的 llm_runtime.json（可证伪"工具与 main user_data_dir 漂移"假设）。
- 回归: `pytest tests/ -k "config or llm or model or compact or compress or persona" -v` 全绿。
- 真机: 开 `compaction_enabled` 重启 → 日志确认 `wi4_0_compaction_enabled context_window=1000000`（非 32000）;
  不再"过早压缩";前端 context_usage ring 显示 gpt-5.5 名。

## 5. 降级/边界
- 无 `llm_runtime.json`（纯本地 ollama 模式）→ 访问器回落 config 种子,行为不变（BC）。
- `llm_runtime.json` 有 model 但无对应 model_info BUILTIN → `model_info.resolve` 已有 `_default` 兜底（32K）。
- `llm_runtime.json` 只有 base_url 无 model（半配置态）→ standalone 不取空,回落磁盘 config model。
- 不改 onboarding 写 `llm_runtime.json` 的逻辑（只读侧统一）。
- **不碰** `[[llm.providers]] main_model`（settings 面板子系统,与 P-B 无关,R1 已核准排除）。

## 6. 文件清单
- 改: `backend/config.py`(+`effective_llm_model` +`effective_llm_model_standalone`) ·
  `backend/main.py`(:188 同步 raw → 顺带修 :2944 breakdown 探针、:1384 压缩窗口、:4268 stub 改读访问器；
  **真 persona 注入 :5304 本就读对,不改**) ·
  `backend/deskpet/tools/ppt_visual_review.py:155`(改 `effective_llm_model_standalone()`)
- 测: `backend/tests/test_effective_llm_model.py`(新,含 standalone 三分支 + persona + 边界) + 压缩窗口解析集成测试
- 关联: 根治 `plans/2026-06-16-context-compaction-optim/00-PLAN.md` §3.5 P-B

---

## 评审迭代记录

### 第 1 轮（2026-06-16，资深架构评审子代理，读真实代码对抗式核验）

**核准的假设（证实）**
- ✅ 「有效出站模型 = `config.llm.local.model`」**成立**。真出站 provider 是 `main.py:239-245` 的 `local_llm`
  （`model=config.llm.local.model`）;`cloud_llm` 在 `main.py:254` 恒 `None`;压缩器 `main.py:1376`
  `local_llm or cloud_llm` = `local_llm`。`strategy="cloud_first"` 是历史字段,当前不构造 cloud,不影响。
  访问器**无需读 cloud.model**。
- ✅ 根因链正确:`config.py:872-873` `raw_llm.pop("local"/"cloud")` 后,`raw["llm"]` 留下 flat 的种子 model;
  `main.py:188` 只覆盖 dataclass,`config.raw = dict(raw)`(line 1003)浅拷贝→`raw["llm"]["model"]` 永远 stale。

**关键发现 / 改了什么（3 个核心）**
1. **漏掉两个 stale 读取点**（已补进 §1 表 + WI-3）:
   - `main.py:2944 → persona.py:93`:**persona/system prompt** 读 `config.raw["llm"]["model"]`,
     把旧 gemma 模型名注入系统提示（用户可见 + 影响模型自我认知）。最简修法靠 WI-2 同步 raw 即自动修对。
   - `main.py:4268 _stub_model`:不仅是"取个名",它把 model 名 + 错窗口**发给前端 context_usage ring**。
2. **WI-3 对 ppt_visual_review 的修法被证伪并重写**:`ppt_visual_review.py:155` 的 `import config as _cfg;
   _cfg.config.raw` **恒抛 AttributeError**——`config` 模块根本没有 `config` 属性（AppConfig 单例是
   `main.py:93` 的 main 全局,`research_tools.py:126-131` 已记录这是真机 bug TC-P2-05）。当前靠 except 回落
   hardcode `"gpt-5.5"` 侥幸不坏。**不能**改成 `effective_llm_model(_cfg.config)`（继续踩坑）。新增
   `effective_llm_model_standalone()`:直读 `llm_runtime.json` 的 model（onboarding 写的有效值,已确认含
   `"model":"gpt-5.5"`）→ 回落磁盘 config → BUILTIN。
3. **WI-2 / WI-3 冗余关系判定 + 排除项**:raw 同步（WI-2）与 in-process 访问器**部分冗余、不矛盾**,
   保留作低成本保险;但 WI-2 **只救 in-process raw 读取**,救不到 out-of-main 工具（它们读磁盘/llm_runtime.json,
   不是 main 内存 raw）→ ppt 路径**必须**走 standalone 直读。另核准 `[[llm.providers]] main_model`
   （`llm/registry.py:132`）是 settings 面板 provider 管理子系统,**不在真出站链路,排除出本期范围**。

### 第 2 轮（2026-06-16，资深架构评审子代理，读真实代码对抗式核验落地坑）

> 结论先行:**第1轮最关键的「persona 漏网」结论被证伪**——真 persona 注入本就读对了模型,
> 第1轮把一个调试探针误当主注入路径。其余结论(真源/standalone/main_model 排除/执行顺序)经复核**成立**。

**🔴 证伪（第1轮误诊,必须改方案）**

1. **persona/system prompt 根本没读 stale raw —— 第1轮「persona 漏网」是误诊,删除该 WI**。
   真 persona 注入路径是 `PersonaComponent.emit()` → `_resolve_persona(ctx.config)`,而 `ctx.config`
   由 `main.py:5304 _assembler.assemble(config=...)` 传入,实参是**临时新构造的 dict**:
   ```python
   config={"llm": {"model": _persona_model, "base_url": _persona_base}, ...}   # main.py:5312-5316
   ```
   `_persona_model = getattr(local_llm, "model", "unknown")`（`main.py:5242`)= `config.llm.local.model`
   （:188 覆盖后 = gpt-5.5）**→ 真 persona system prompt 早已注入正确的 gpt-5.5,不是 bug**。
   注释 `main.py:5238-5241` 明确写了这个设计("a gpt-5.5-bound session truthfully reads its prompt")。
   - 第1轮指的 `main.py:2944 _resolve_persona(config.raw)` **是 `_breakdown` 调试探针**
     （context breakdown UI 面板的 persona 预览片段,见 :2938-2952 注释),**不是**发给 LLM 的 system prompt。
     它确实读 stale `config.raw`,但只影响**调试面板显示的模型名预览**,不影响真出站 prompt。
   - **影响修订**:① 删 WI-3 的「persona」条目(WI-2 同步 raw "自动修 persona" 的论证作废——真 persona 不读 raw);
     ② 删第1轮新增的「persona 单测」(无 bug 可证伪);③ §1 表的 persona 行从「★ 漏网/系统提示注入旧 gemma」
     降级为「**调试面板 breakdown 预览**显示 stale 名(cosmetic),非系统提示」。
   - WI-2 同步 raw **仍保留**:它能顺带修好这个 breakdown 探针 + :1384 + :4268(都读 `config.raw`),
     但定位从「persona 主修」降为「修 in-process raw 读取(含 breakdown 探针/压缩窗口/stub)的统一保险」。

**🟢 核准(复核成立)**

2. **standalone helper 读 llm_runtime.json 方案可行,但 plan 的实现引用要纠偏**。
   `image_tools._resolve_endpoint`(:94-134)直读 `user_data_dir()/llm_runtime.json` 的正确模式确认:
   `from paths import user_data_dir; rt = user_data_dir()/"llm_runtime.json"; data = json.loads(rt.read_text(...))`
   + 宽 `except` 容错。standalone 应**抄这个 IO 模式**。
   - ⚠️ **但 plan §3.1/§1 注释「抄 image_tools 读 model」措辞不准**:image_tools 从 llm_runtime.json
     **只取 `base_url`/`api_key`,不取 `model`**(它的 model 走 `_image_model()` 读 `[image]` 段,不是 `[llm]`)。
     所以 standalone 是**新读 llm_runtime.json 的 `model` 字段**,llm_runtime.json 里确有 `"model"`
     (onboarding 写,见 `main.py:185-188` 应用的就是 `_llm_overrides["model"]`,`_save_llm_runtime_overrides`
     :3249 写的 `overrides_to_save` 含 model)→ 字段存在,可读。
   - **user_data_dir 一致性核实通过**:`paths.user_data_dir()`(:123-138)优先 `DESKPET_USER_DATA_DIR` env;
     工具与 main **同进程同 env** → 同一目录。(注:工具不是"独立进程",是 backend 内 import 的;`config.config`
     AttributeError 是"模块无该属性",非"跨进程拿不到单例"——**§1/§2/§3 的「out-of-process」措辞应改「out-of-main-单例-作用域」**,
     避免误导后续实现者去想进程间通信。)

3. **执行顺序核实通过**:`config` 是 main.py 顶部 import 的全局单例;`main.py:183-198`(应用 override
   + 拟加 WI-2 同步 raw)在模块顶层、`local_llm`(:239)/压缩器(:1376/1384)/breakdown(:2944)/stub(:4268)
   /assemble(:5304)**全部之后才执行的代码之前**。且全程 `config.raw` **无重新赋值/deepcopy**
   (grep `config.raw =`/`deepcopy` 在 main.py 无命中)→ WI-2 写入对所有 in-process raw 读取点可见。

4. **gemma 回落安全(复核)**:`gemma4:e4b` **不在 BUILTIN 表**(表里 gpt-5.5/deepseek-v4-pro/claude-*
   /gpt-5-pro/gemini-2.5-pro/_default),`model_info.resolve` 对它走 `_default`(32K,见 :232-236)。
   故访问器回落 `"gemma4:e4b"` 字面值 vs 回落空串:`resolve` 都得 32K,**仅 `.model` 名字段不同**(影响日志/
   stub 发前端的 UI 显示名)。**轻微 cosmetic 权衡**:回落 gemma 字面会让 stub UI 显示 "gemma4:e4b";
   若想更干净可回落空串让上层显"未知"。非阻塞,可保留现回落。

5. **再 grep 漏网读取点 —— 无新增真 bug**。全后端读 `raw["llm"]["model"]` 仅 4 处:`main.py:1384`(压缩,WI-3 已含)、
   `main.py:4268`(stub,WI-3 已含)、`main.py:2944`(breakdown 探针,WI-2 顺带修)、`ppt_visual_review.py:155`
   (WI-3 standalone 已含)。其余把 model 发前端/落库的点(`:2810`/`:2838` 读 `_picked.model`=provider 覆盖后值、
   `:3043`/`:3235`/`:3265` 回显当前请求 model、`:5314` persona 读 `local_llm.model`)**都读对**,非 stale。
   ⚠️ **新发现同类坑(非本期 bug,登记)**:`image_tools.py:122` 的 `_cfg.config.raw.get("llm")...base_url`
   与 ppt 同病(`_cfg.config` AttributeError 被吞),但它前面已先读 llm_runtime.json 的 base_url(:107-113)
   兜底 → 实际不坏;不在 P-B 范围,**spawn_task 另跟踪**清理。

6. **测试可证伪性补强**:
   - ✅ 已可证伪:standalone 三分支(plan 已有)、压缩窗口 1M(plan 已有)、执行顺序集成(plan §4 已有)。
   - ❌ 删:第1轮「persona 单测」(persona 无 bug,无可证伪点);若仍想守护可改为
     **「persona 注入读 `local_llm.model` 而非 `config.raw`」的回归断言**(传 raw=gemma 但 local_llm.model=gpt-5.5
     → persona 文本含 gpt-5.5),证伪"未来有人误把 persona 改成读 raw"。
   - ➕ 补:standalone 的 `user_data_dir` 用 `DESKPET_USER_DATA_DIR` monkeypatch,断言读到的是
     **main 同一目录**的 llm_runtime.json(可证伪"工具与 main 目录漂移"假设)。
