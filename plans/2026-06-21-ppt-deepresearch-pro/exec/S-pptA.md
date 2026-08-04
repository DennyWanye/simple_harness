在 DeskPet 项目实现 PPT Pro 计划的 ppt_tools「内容半」（WI-0 + WI-1 + WI-2）。只改 `G:\projects\deskpet\backend\deskpet\tools\ppt_tools.py` 一个文件（可新增 import）。**不要动 image_tools/main/config 等其它文件。**

先读权威计划 `G:\projects\deskpet\plans\2026-06-21-ppt-deepresearch-pro\00-PLAN.md` 的 WI-0、WI-1、WI-2 段（含 §1.1/§1.3 接口、§2.5 编排骨架里这几个函数怎么被调）。只读参考：`backend/config.py` 的 `standalone_config_section`、`backend/deskpet/tools/research_tools.py` 的 `deepresearch`(@~1341)/`_DEPTH_PRESETS`(@~2110)/`_resolve_default_llm_call`(@~2222)/`ResearchReport`/`_save_report`/`_update_deepresearch_index`/`paths.deepresearch_dir`、本文件已有的 `SlideOutline`/`parse_outline`/`normalize`/`_assign_image_layouts`。

实现以下新增（都加在 ppt_tools.py 合适位置，中文注释）：

**WI-0 配置读取（修已知坏读法）**
- 新增 `_ppt_pro_cfg()`：返回一个轻量对象/dict，字段含 `enabled`(默认True)/`default_depth`("deep")/`max_revisions`(2)/`research_timeout_s`(360.0)/`confirm_timeout_s`(1800.0)/`image_probe_timeout_s`(8.0)/`render_timeout_s`(0.0,0=动态 max(600,pages*120))/`save_research`(True)/`outline_history`(True)。**经 `config.standalone_config_section("ppt")` 读 toml**，缺失用默认。
- 顺手把本文件现有用 `_cfg.config.raw`（或类似拿不到 config 单例）的坏读法（如 `_ppt_async_enabled`/`_ppt_preview_render_enabled` 等，grep `config.raw`/`config.config`）改成走 `standalone_config_section("ppt")`，行为不变只是读法健壮（STATUS 多次记录该 bug）。

**WI-1 调研封装**
- `async def _research_topic_for_ppt(topic, *, depth, timeout_s)`：`from deskpet.tools.research_tools import deepresearch, _resolve_default_llm_call, _DEPTH_PRESETS`；取档位参数；`await asyncio.wait_for(deepresearch(topic, llm_call=await _resolve_default_llm_call(), max_sub_questions/max_urls_per_query/max_total_passages/max_rounds=按档位, mode=depth, user_request=f"为制作PPT调研：{topic}"), timeout=timeout_s)`。
  - `except asyncio.TimeoutError`: log warning, 返回 None。
  - `except Exception as e`: 若 `_is_config_or_auth_error(e)` → `raise`（上抛让用户知情，不静默编）；否则 log warning 返回 None（降级）。
- `_is_config_or_auth_error(e) -> bool`：识别 401/403/key 缺失/provider 未配置等硬错误（看 e 的 message/类型，参考 image_tools 401 处理思路）。
- `_save_and_index_research(topic, report)`：调 research_tools 的 `_save_report` + `_update_deepresearch_index`（best-effort，失败只 log）。供编排在 `save_research=True` 时落盘用。

**WI-2 大纲拟制**
- `async def _draft_outline_from_research(topic, report, *, pages, theme, image_mode, llm_call, feedback="", prev_slides=None) -> list[SlideOutline]`：
  - `research_md = (report.report_md if report else "")[:_PPT_PRO_RESEARCH_CTX_CHARS]`（常量=6000）。
  - `prompt = _build_outline_prompt(topic, research_md, pages=pages, image_mode=image_mode, feedback=feedback, prev_md=(_outline_to_markdown(prev_slides) if prev_slides else ""))`。
  - `raw = await llm_call(prompt)`; `slides = parse_outline(raw)`; 空则 `slides = _fallback_minimal_outline(topic, pages, image_mode)`; 返回。
- `_build_outline_prompt(...)`：产出 SlideOutline 兼容 JSON 数组的提示词。**双模式**：每页同时含 `title`+`bullets`(3-5 条充实要点)+`image_prompt`(电影感/负空间/禁字)；image_mode 时 layout 多用 `image_full`。强约束「内容必须基于调研报告/引用其数据/不得编造」并附 research_md。`prev_md` 非空 → 指令改「这是当前大纲+用户要改X，只改相关页保留其余」（增量）。`feedback` 注入。
- `_outline_to_markdown(slides) -> str`：页码+标题+bullets 缩进的可读大纲（给确认卡 content_md / prev_md）。
- `_fallback_minimal_outline(topic, pages, image_mode) -> list[SlideOutline]`：纯本地兜底（封面+pages-1 内容页占位）。
- 常量 `_PPT_PRO_RESEARCH_CTX_CHARS = 6000`。

约束/验收：
- 这趟**只加函数，不改 ppt_create/不加 handler/不注册工具**（那些在下一趟）。`_render_pro`/`_handle_ppt_pro` 等不在本趟。
- 不破坏现有任何函数；`-k ppt` 现有测试保持绿。
- 自己补单测 `tests/test_ppt_pro_content.py`：mock llm_call + mock deepresearch——(a) 有report拟纲含image_prompt与bullets；(b) report=None仍出纲；(c) feedback+prev_slides 触发增量(校验prompt含feedback/prev_md)；(d) llm返回垃圾→fallback；(e) _outline_to_markdown 多页成文；(f) _research_topic_for_ppt 超时→None、config错→raise（mock）。
- 跑 `cd /g/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/ -k "ppt and content" -q` 与 `-k ppt -q` 到绿。

完成后简述新增函数清单 + 单测覆盖 + 你修了哪些 config.raw 坏读法。