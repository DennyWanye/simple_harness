# PPT 精美主线 — PLAN（2026-06-09）

> 承接 `plans/HANDOFF-2026-06-09.md` §4。用户已拍板「A+B 都做」。
> Lead = Claude（规划/审 diff/真机验收）；Expert = codex `gpt-5.5` 子代理（实现）。

---

## 0. 现状勘探结论（已读源码，修正交接文档的过时描述）

| 交接文档说 | 实际源码 | 结论 |
|---|---|---|
| 「图片工具没返回可用本地路径」 | `image_tools.py` **sync 路径**(line 283-299) 已返回 `path`+`artifacts[]`；`_generate_png`+`_save_image` 是干净同步原语 | 落盘+返回路径**已实现**。真问题 = **async 模式(默认)只回 `status=generating` 无 path**，PPT 拼图拿不到路径 |
| 图像生成不可靠(SSL UNEXPECTED_EOF) | `_generate_png` 已有 retry(2 次)+b64/url 双路 | retry 仅 1 次；**裸 `ssl.SSLError` 落到通用 `except`(line 231) 立即返回不重试** = 健壮性缺口 |
| 整页生图需要新引擎 | `ppt_tools.py` `_render_image_v2`(line 1188-1194) **已支持** `image_path` → `add_picture` 真嵌图 | B-2 渲染侧**已就绪**，缺的只是「同步批量生图拿路径填 outline」 |

**关键架构洞察**：PPT 引擎渲染侧 + 图像生成同步原语**都已存在**，主线工作量集中在「把两者用同步批量路径接起来」+「relay 健壮性加固」+「模板模式」。

---

## 1. 工作分解 + 执行顺序（依赖序）

### B-1 图像生成可靠性加固 + 同步批量原语 ★先做（基石）
**文件**：`backend/deskpet/tools/image_tools.py`（单文件，不破现有接口）
**改动**：
1. `_generate_png` 健壮性：
   - 把 `ssl.SSLError`、`httpx.ProtocolError`、`httpx.ReadTimeout` 等显式归类为**瞬时可重试**（当前裸 `ssl.SSLError` 会落到 line 231 通用 except 立即返回 → 漏接 `UNEXPECTED_EOF_WHILE_READING`）。
   - `_MAX_ATTEMPTS` 2→**4**，退避改指数 `(3, 8, 20)`；`_TOOL_TIMEOUT_S` 同步上调覆盖最坏预算。
   - 错误信息保留「最后一次瞬时原因」便于诊断。
2. 新增**同步批量原语** `generate_images(prompts: list[str], size=...) -> list[dict]`：
   - 复用 `_generate_png`+`_save_image`，逐 prompt 同步生成、落盘、返回 `[{"prompt","path"|None,"error"|None}]`。
   - 不走 async worker（PPT 拼图需要确定性同步路径）。
   - 给 B-2 用；不注册成 LLM 工具（内部 helper）。
**验收**：
- `pytest backend/tests/test_generate_image_tool.py` 全绿（不破现有）。
- 新增 `test_generate_png_retries_on_ssl_error`（mock httpx 抛 `ssl.SSLError`→断言重试到上限）。
- 新增 `test_generate_images_batch_returns_paths`（mock `_generate_png` → 断言落盘+路径列表）。

### B-2 整页生图 PPT 模式（依赖 B-1）
**文件**：`backend/deskpet/tools/ppt_tools.py`
**改动**：`SlideOutline` 支持 `image_prompt` 字段；`ppt_create` 渲染前若某页有 `image_prompt` 且无 `image_path` → 调 B-1 `generate_images` 批量生成 → 回填 `image_path` → 既有 `_render_image_v2` 真嵌图。
**约束**：不破 `ppt_create` 现有签名/返回/`markdown_fallback`；`image_prompt` 为可选新增；无 endpoint/生图失败时优雅降级到占位（现有 placeholder 逻辑）。
**验收**：pytest `-k ppt` 全绿 + 新增「带 image_prompt 的 outline → mock generate_images → 断言 add_picture 被调」。

### A-1 模板资产
**产物**：`backend/deskpet/tools/ppt_templates/*.pptx`（2-3 套 slide master+layouts+theme），来源参考 `.tmp/ai-education-deck.pptx` 设计铁律。

### A-2 模板填充模式（依赖 A-1）
**文件**：`ppt_tools.py`：`ppt_create` 加 `template` 参数 → 载模板 → 用其 layout 加页填充 → 格式继承 → 可编辑专业版。
**约束**：default 仍走现有 from-scratch 引擎（BC）；`template` 显式指定才走模板模式。

---

## 2. 派活纪律
- 每个 WI 用 `codex exec -m gpt-5.5`，prompt 自包含（文件绝对路径 + 现有约定 + 验收点 + 不破接口约束）。
- Lead 审 diff + 跑 pytest + （主线末）真机生成 PPT 肉眼验收。
- 不加新依赖；python-pptx 既有。

## 3. 验收标杆
`.tmp/ai-education-deck.pptx`（pptxgenjs 好看版）；设计铁律见 `anthropic-skills:pptx` 技能。
