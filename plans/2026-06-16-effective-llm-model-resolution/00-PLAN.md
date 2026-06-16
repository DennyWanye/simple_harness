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

**读对的（用 `config.llm.local.model`，正确）**：`main.py:242`(主 LLM 构造)、`266`(cloud)、`2684/2688`。
**读错的（用 `config.raw["llm"]["model"]`，拿到旧 gemma）**：
| 位置 | 用途 | 后果 |
|---|---|---|
| `main.py:1384` | **压缩窗口/阈值解析**（ContextCompressor 初始化） | 按 gemma 32K 算阈值 → gpt-5.5 1M 时过早压缩浪费窗口（P-B 本体） |
| `main.py:4268` | `_stub_model`（某 stub 路径取模型名） | 拿到旧 gemma 模型名 |
| `deskpet/tools/ppt_visual_review.py:155` | PPT 视觉复审取模型名 | 拿到旧 gemma 模型名 |

> 注：本会话已临时把 `userdata/config.toml [llm] model` 手改成 `gpt-5.5` 堵上（让 raw 也对）。
> 但那是**用户本地 config 的临时对齐**，新用户/换模型仍会种子 gemma → raw stale。**根治要在代码层**。

## 2. 决策

- **单一真源**：以 `config.llm.local.model`（运行时覆盖已应用）为"有效出站模型"的唯一来源。
- **加一个显式访问器** `effective_llm_model(config) -> str`，集中处理：优先 `config.llm.local.model`，
  空则回落 `config.raw["llm"]["model"]`，再空回落 `config.py` 的 BUILTIN 默认。所有读模型名的点走它，
  杜绝再各读各的。
- **同时把 raw 同步对齐**（belt-and-suspenders）：`main.py:188` 应用覆盖时，**也写
  `config.raw["llm"]["model"] = model`**，使任何残留的 raw 读取（含第三方/未来代码）也拿到有效值。
  双保险：访问器 + raw 同步。

## 3. 范围与改法（WI）

### WI-1 加 `effective_llm_model` 访问器
- 在 `config.py` 加 `def effective_llm_model(cfg: AppConfig) -> str`:
  优先 `cfg.llm.local.model` → `cfg.raw.get("llm",{}).get("model")` → BUILTIN 默认 `"gemma4:e4b"`（保持兜底）。
  纯函数,空安全。
- 单测: dataclass 有值时返 dataclass;dataclass 空 raw 有值时返 raw;都空返默认。

### WI-2 main.py:188 应用覆盖时同步 raw
- 在 `config.llm.local.model = _llm_overrides["model"]` 之后,加
  `config.raw.setdefault("llm", {})["model"] = _llm_overrides["model"]`（同步 raw,使 1384/4268 即便
  不改也读对——但仍按 WI-3 改读访问器,双保险）。base_url 同理可同步(可选,本期可只同步 model)。

### WI-3 三处 stale 读改走访问器
- `main.py:1384`（压缩窗口）: `(config.raw.get("llm") or {}).get("model")` → `effective_llm_model(config)`。
- `main.py:4268`（_stub_model）: 同上。
- `deskpet/tools/ppt_visual_review.py:155`: 改读有效模型。注意它在 tool 里用 `_cfg.config`——若
  `config` 模块单例不可靠(见 commit 2b7baa1 的 `_cfg.config` 坑),需用 `load_config(resolve_config_path())`
  或注入,WI-0 先验证该处能否拿到 dataclass;拿不到则退而用"raw 已被 WI-2 同步"的值。

### WI-4 压缩窗口解析正确性（P-B 验收点）
- `main.py:1376-1402` 压缩窗口解析改用 `effective_llm_model(config)` 喂 `model_info.resolve`。
- 验收: 设 `llm_runtime.json model=gpt-5.5` + 用户档 1M override → 启动日志
  `wi4_0_compaction_enabled context_window=1000000`（而非 32000）。

## 4. 测试
- 单测: `effective_llm_model` 三分支;压缩窗口解析在"config 种子 gemma + runtime 覆盖 gpt-5.5(1M)"下
  解析出 1M 而非 32K（mock config + llm_runtime）。
- 集成: 模拟 main 启动序(应用 override 后) → `config.raw["llm"]["model"]` 与 `config.llm.local.model`
  一致 + 压缩器 context_window 为有效模型窗口。
- 回归: `pytest tests/ -k "config or llm or model or compact or compress" -v` 全绿。
- 真机: 开 `compaction_enabled` 重启 → 日志确认压缩窗口=gpt-5.5 的窗口;不再"过早压缩"。

## 5. 降级/边界
- 无 `llm_runtime.json`（纯本地 ollama 模式）→ 访问器回落 config 种子,行为不变（BC）。
- `llm_runtime.json` 有 model 但无对应 model_info BUILTIN → `model_info.resolve` 已有 `_default` 兜底（32K）。
- 不改 onboarding 写 `llm_runtime.json` 的逻辑（只读侧统一）。

## 6. 文件清单
- 改: `backend/config.py`(+effective_llm_model) · `backend/main.py`(:188 同步 raw、:1384/:4268 改读访问器、
  压缩窗口解析) · `backend/deskpet/tools/ppt_visual_review.py:155`
- 测: `backend/tests/test_effective_llm_model.py`(新) + 压缩窗口解析集成测试
- 关联: 根治 `plans/2026-06-16-context-compaction-optim/00-PLAN.md` §3.5 P-B
