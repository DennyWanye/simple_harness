# DeskPet PPT 质量优化 plan v2（第 1 轮迭代：可行性 + 完整性挑战）

> 2026-06-20 · 上游 [00-PLAN.md](./00-PLAN.md) · 配套 [STATUS/PPT.md](../../STATUS/PPT.md)
> 本轮目标：对 00-PLAN 的 W1–W9 **逐项核对真实代码 / 挑战可行性 / 补漏洞与降级 / 补遗漏**。
> 约束：**功能一个不少**；本轮只细化不删减。所有点名的函数名/行号均已用 Read/Grep 实测核对（见文末「本轮修正与挑战记录」）。

---

## 0. 全局事实校准（先纠正 00-PLAN 的事实错误，后续 W 项以此为准）

| 00-PLAN 的说法 | 实测结果 | 影响 |
|---|---|---|
| `_render_chart_v2` 在 **:1650** | 实为 **:1651**（`def _render_chart_v2`）；另有一个**死函数** `_render_chart`(:1027) 未挂进 `_RENDERERS`，别误改它 | W6 |
| `_add_icon_badge` 在 **:632** | ✅ 正确(:632)。但「只彩色圆圈无字形」**不准**：它是 OVAL + 居中 `_add_text(label,...)`，**会渲染一个字符/数字**（如 "1"/"A"/"S"/"IMG"），字体写死 `Calibri` | W4 |
| `_render_with_design_pages` 在 **:2938** (STATUS) | 实为 **:2950**；`ppt_create` 在 **:3152**（00-PLAN/STATUS 写 :3216，错） | W1b/W7 接线点 |
| W6「`add_chart` 后未设任何颜色」 | ✅ 属实：:1695-1704 只设了 `has_legend`/`legend.position`，**无 series 上色**、**无关网格线**、**有 legend 无 data_labels** | W6 |
| W7「clone/del element」是全新能力 | **部分已存在**：`_set_text_keep_style`(:2301) 内已用 `copy.deepcopy(p0._p)` 克隆段落 lxml 节点；`_drop_slide_id`(:2936) 已能删整页。**槽位级 shape 的 clone/del 才是新增** | W7 |
| W3「shrink_text 只缩字号」 | ✅ 属实，但还存在**主动按槽高缩字号** `_fit_text_to_shape`(:2506)（WPS 不执行 normAutofit 的兜底），W3 需与它协同别打架 | W3 |

**架构既有事实（写 plan 前必须知道，避免重复造轮子）**：
- `vision_chat`(`ppt_visual_review.py`:74) 已是**通用** `/chat/completions` 原语，接受 OpenAI 风格 `content_parts`（可纯 text，无需图片），**任何失败返回 `""`**，从不抛。W1b 直接复用它即可，**无需新建 relay 接线**。
- 模型解析：`vision_chat` 内 `effective_llm_model_standalone()`，默认 `gpt-5.5`。
- Theme 数据类(:230) 字段确名：`surface_rgb / primary_rgb / secondary_rgb / accent_rgb / highlight_rgb / text_rgb / dark_text_rgb / dark_muted_text_rgb / muted_text_rgb / font_heading / font_body`。**注意是 `highlight_rgb` 不是 `highlight`**（00-PLAN W6 写错成 `theme.highlight`）。三主题 font 全是 `Microsoft YaHei`。
- python-pptx 已实测可用 API（venv `pptx 0.x`）：`series.format.fill.solid()` / `series.format.fill.fore_color.rgb`（`_BaseSeries.format → ChartFormat`，:26）；`series.points[i].format.fill...`（`Point.format → ChartFormat`，point.py:75）；`series.data_labels`（:存在）；`chart.plots`(:144)/`chart.value_axis`(:180)/`chart.category_axis`(:26)；`axis.has_major_gridlines`(setter 存在, axis.py:59)；`chart.has_title`(setter, chart.py:123)。**全部 grounded，W6 可落地。**

---

## 1. 架构定位（同 00-PLAN，修正接线行号）

| 层 | 文件 / 函数 | 本 plan 动作 | 真实位置 |
|---|---|---|---|
| Skill | `ppt-generate/SKILL.md` | W1a/W2 重写 | :27（stale 决策表）/ :65-71（质量要求） |
| 工具-编排 | `ppt_tools.py::ppt_create` | W1b 加 outline 评审 hook | **:3152**（parse 在 :3191，渲染分派在 :3249/3280） |
| 工具-内容评审(新) | `ppt_outline_review.py` | W1b 新建 | — |
| 工具-图表 | `_render_chart_v2` | W6 | **:1651** |
| 工具-图标 | `_add_icon_badge` | W4 | :632 |
| 工具-配图 | `_place_cover` / `_render_image_*` | W5 | `_place_cover`:1340 |
| 工具-密度 | `_render_bullet_v2` / outline 评审 | W3 | :1193 |
| 工具-设计页槽位 | `_fill_design_bullets` / `_best_design_content_page` / `_select_design_page` | W7 | :2644 / :2397 / :2454 |
| 工具-装饰 | `_add_footer` / `_add_corner_motif` | W8 | :1777 / :616 |
| 视觉评审 | `ppt_visual_review.vision_chat` | W1b 复用，W6 不依赖 | :74 |

---

## 2. 工作项（每项：现状[真实函数:行号] → 问题 → 改法[API/签名/结构] → 风险与降级 → 验收）

---

### W1 — 大纲内容质量（混合：Skill 重写 + 工具层内容评审闭环）★最高优先

#### W1a — 重写 `ppt-generate/SKILL.md`（skill 层治本）

- **现状**：质量要求(:65-71) 全是格式约束（页数 6-14、bullet ≤5 条、≤15 汉字、节奏建议）；唯一内容指引是「bullet 是提示词不是讲稿」(:71)——这句**反而把内容推浅**（鼓励写关键词而非有信息量的句子）。
- **问题**：无「信息密度 / 数据支撑 / 叙事逻辑」要求 → LLM 产「黑暗中的生物发光」式空泛词。
- **改法**：
  1. 把 :71 那句改写为**「bullet 要短，但每条必须承载一个具体事实/数据/机制，而非概念标签」**，并给反例→正例对照（深海：「极端环境的生存智慧」→「热泉口 2℃↔400℃ 温差带，管虫靠化能合成菌共生固碳」）。
  2. 加「内容深度三要素」清单：①具体（数字/专名/机制）②单页一论点（one-message-per-slide）③数据型必上 `chart` 页。
  3. 加 **好/坏大纲 few-shot 各 1 份**（同主题对照，放在 schema 块后）。
  4. 加 **4 套分场景页序模板**（汇报 / 教学 / 产品发布 / 方案）。
- **风险与降级**：
  - ⚠ **gpt-5.5 输出窗口约束**：STATUS §9 提到「窗口仅 8000」。few-shot + 4 模板会显著拉长 SKILL.md（当前 121 行），**挤占大纲生成 token**。→ few-shot **各压到 ≤6 行**；4 套页序写成**单行紧凑表**而非展开示例；总增量控制在 ≤40 行。
  - SKILL.md 是给 LLM 读的纯文本，无可执行验收 → 真测靠 W1b + 真机对比。
- **验收**：同主题（深海）跑新旧 SKILL.md 各产一份 outline，**逐条 bullet 比对信息密度**（数字/专名出现率↑）；真机 windows-mcp 截图存证。

#### W1b — 新增工具层「大纲内容评审闭环」`ppt_outline_review.py`

- **现状**：无任何内容层兜底；大纲质量 100% 押在 LLM 单次输出。
- **改法（类比 `ppt_visual_review.py` 的结构，复用 `vision_chat`）**：
  - 新文件 `backend/deskpet/tools/ppt_outline_review.py`：
    ```python
    def review_outline(slides: list[SlideOutline], topic: str,
                       *, timeout: float = 60.0) -> list[dict]:
        # 把 outline 序列化成纯文本（页号|layout|title|bullets），
        # content_parts = [{"type":"text","text": <规则+大纲>}]
        # text = vision_chat(content_parts, system=_OUTLINE_SYSTEM, max_tokens=1200)
        # 解析 JSON 数组；失败/空 → 返回 []
        # 每条: {"page":int,"ok":bool,"issues":[str],
        #        "action":"ok|deepen|split|merge|rewrite",
        #        "suggestion": <action 所需的新内容草案>}

    def apply_outline_fixes(slides, issues, topic) -> list[SlideOutline]:
        # deepen/rewrite: 用 issue.suggestion 覆写该页 bullets/subtitle
        #   （suggestion 已由 review 调用一次性产出，避免每页再起一次 LLM 调用）
        # split: 把一页拆两页（在 slides 列表 insert）
        # merge: 相邻两页合并
        # 从不抛；任何异常 → 返回原 slides
    ```
  - **关键设计决策（纠 00-PLAN 的可行性漏洞）**：00-PLAN 说「apply 对 deepen/rewrite **调 LLM 重写**」——这会**每个 issue 一次 LLM 调用**，N 页 deck 触发 N 次串行调用，**时延爆炸**（每次 ~数秒~分钟，relay 还可能被 Clash 掐空闲连接，见全局踩坑）。**改为**：`review_outline` 在**同一次** vision_chat 调用里就让模型把 `suggestion`（深化后的新 bullets）一并产出，`apply` 只做**纯本地数据结构操作**（不再调 LLM）。`split`/`merge` 也是纯本地。→ **整闭环 = 每轮 1 次 LLM 调用**，与视觉评审同量级。
  - 接线 `ppt_create`：在 **:3191 `parse_outline` 之后、:3232 `wants_fullbleed` 判定之前**插入 `slides = _outline_review_loop(slides, topic)`。`topic = title or slides[0].title`。
  - `_outline_review_loop`：≤2 轮；开关 `[ppt].outline_review`（默认 on）；**pytest 环境跳过**（与视觉闭环同机制——查 `PYTEST_CURRENT_TEST` env 或现有的跳过判定，复用之）。
- **风险与降级**：
  - ⚠ **split/merge 改变页数**：会让后续「内容数↔模板槽数」（W7）、视觉评审页号、`_is_conclusion_slide(index,total)`(:1769) 的 total 全部偏移。→ outline 评审**必须在 W7 选页、视觉评审、conclusion 判定之前**完成（接线点已选在最前，✅）。**split 上限**：单轮最多拆 2 页、总页数硬上限 18（与 SKILL.md 一致），防失控膨胀。
  - ⚠ **deepen 可能把 bullet 写超长** → 撞 W3 信息密度。→ `apply` 后对每条 bullet 截断/警告（≤30 汉字），把「深化」引导成「换更实的内容」而非「加长」。
  - vision_chat 失败返回 `""` → `review_outline` 返回 `[]` → loop 原样透传 slides，**零影响**（已验证 vision_chat 从不抛）。
  - ⚠ **dry_run 路径**(:3204)：dry_run 在 parse 之后立即 return markdown，**不经渲染**。决策：outline 评审应在 dry_run **之前**跑（让用户预览的是已深化的大纲），即接线点放在 :3191 之后、:3204 dry_run 判定之前。
- **验收**：
  - 单测：mock `vision_chat` 返回固定 JSON → 验 `apply_outline_fixes` 对 deepen/split/merge/空返回的行为；mock 返回 `""` → 验 loop 不抛、原样返回（降级）。
  - 单测：`split` 后 `total` 正确、conclusion 判定不错位。
  - 真机：喂一份故意空泛 outline → 闭环后 bullet 明显变实（截图 + backend log `outline_review done pages=.. issues=..`）。

---

### W2 — 修复 stale SKILL.md 模板名

- **现状（实测）**：`SKILL.md:27` 决策表「模板填充」行硬编码 **`教育/文化/政务→商务深蓝-水墨;科技/商业/产品→高级感-蓝;设计/品牌/高端→简约高级-灰`** 并写「只能从 schema 里列出的精选模板…别自己编模板名」。这 3 个名字**已删**（STATUS §9.2 实锤），与新「大类名（高级色/高级简约/通用商务）+ 预览图视觉选」机制冲突。:81 还写「bundled 模板名或 .pptx 绝对路径」。
- **问题**：LLM 按表传旧名 → `_resolve_template_for_render` 解析失败 → `ppt_create` 回落 from-scratch（:3275 `template not found` 日志），模板模式名存实亡。
- **改法**：
  1. 改 :27 决策表「模板填充」行为：**`传 template=<大类名>，三选一：高级色（科技/商业/产品/高端）/ 高级简约（设计/品牌/极简）/ 通用商务（汇报/教育/政务/通用）。具体设计页由桌宠看预览图自动挑，别编模板名、别传具体文件名。`**
  2. 同步改 :81 参数说明：`template: 仅模板模式传，值为大类名（高级色/高级简约/通用商务）`。
  3. 核对 schema 示例（:39-58）里若有 template 字段注释一并更新（实测 schema 块内无 template 字段，仅 :30/:81 提到，确认改这两处即可）。
- **风险与降级**：大类名要与 `ppt_template_picker.py` / `_resolve_template_for_render` 实际接受的值**逐字一致**（含「通用商务」是否真支持）。→ 改前 Grep picker 里的合法大类常量，对齐拼写。
- **验收**：真机说「做个商务/专业 PPT」→ backend log 出现 `_render_with_design_pages` 成功（**非** `template not found` 回落）→ 截图确认是设计页填充而非 from-scratch。

---

### W6 — 图表配色随主题 + 去 chart junk ★实锤、API 已 grounded、最高性价比

- **现状（实测 :1651-1707）**：`add_chart`(:1696) 后仅设 `has_legend=True`/`legend.position=BOTTOM`/`include_in_layout=False`。**无 series 上色**（默认蓝橙灰）、**无关网格线**、**无 data_labels**、**chart 自带 title 区**（与页头 `_add_title_block` 重复）。
- **改法（在 :1701 `chart = gframe.chart` 之后、`except` 之前，全部包在 try 内，每步独立 try 容错）**：
  1. **调色板**（注意字段名修正）：
     ```python
     palette = [theme.secondary_rgb, theme.accent_rgb,
                theme.highlight_rgb, theme.primary_rgb]   # 不是 theme.highlight
     plot = chart.plots[0]
     if ctype == "pie":
         pts = plot.series[0].points        # 单 series，逐点上色
         for i, pt in enumerate(pts):
             pt.format.fill.solid()
             pt.format.fill.fore_color.rgb = RGBColor(*palette[i % len(palette)])
     else:
         for i, s in enumerate(plot.series): # bar/line：逐 series 上色
             s.format.fill.solid()
             s.format.fill.fore_color.rgb = RGBColor(*palette[i % len(palette)])
     ```
     - ⚠ **line 图特例**：`series.format.fill` 给的是**线条/标记填充**；折线的「线色」严格应走 `series.format.line.color.rgb`（`ChartFormat.line` 存在, chtfmt.py:34）。→ line 类型同时设 `s.format.line.color.rgb`。
  2. **去 junk**（每步 try 包裹，pie 无 value_axis 要跳过）：
     ```python
     chart.has_title = False                 # 页头已有标题
     if ctype != "pie":
         chart.value_axis.has_major_gridlines = False
         chart.value_axis.has_minor_gridlines = False
     ```
  3. **data_labels**（pie 尤其需要占比标签）：
     ```python
     plot.has_data_labels = True
     dl = plot.data_labels
     dl.font.size = Pt(10); dl.font.color.rgb = RGBColor(*theme.text_rgb)
     if ctype == "pie":
         dl.number_format = "0%"; dl.number_format_is_linked = False
         dl.show_percentage = True
     ```
- **风险与降级**：
  - ⚠ **WPS vs PowerPoint 渲染差异**：本项目用 WPS COM 渲 PNG（视觉闭环/预览）。WPS 对 `c:dPt`（逐点上色）、`number_format`、`dLbls` 的支持与 PowerPoint 有差。→ **必须真机 WPS 截图验证三类图都真上色**，不能只信单测 XML。
  - ⚠ pie 用 `chart.value_axis` 会抛（无值轴）→ 已用 `if ctype != "pie"` 规避；仍整体 try 兜底。
  - `RGBColor` 已在 :133 import，`Pt` 在 :132，无需新增 import。
  - 失败降级：上色/junk 步骤任何异常**不影响**已渲染的图（chart 已 add 成功），只是退回默认外观；最外层 `except`(:1705) 仍保留「降级为 cards」。
- **验收**：
  - 单测：构造 bar/line/pie outline → 渲染后解析 .pptx XML，断言 series/point 出现 `<a:srgbClr>` 且值=主题色、`value_axis` 无 `majorGridlines`、`has_title` 关。
  - 真机：minimal/dark/playful 三主题各出一张图，WPS 截图确认配色随主题、无默认蓝橙、无多余网格线。

---

### W3 — 信息密度失控

- **现状**：`_render_bullet_v2`(:1193) 已有**隐性上限**——`items[:6]`(:1224) 和 `items[:4]`(:1208) 截断，但**多出的 bullet 被静默丢弃**（不是分页，是丢内容）。视觉闭环 `shrink_text` 只缩字号；`_fit_text_to_shape`(:2506) 按槽高主动缩。
- **问题**：根因在**源头一页塞太多**；渲染层「丢弃溢出 bullet」是隐性数据丢失。
- **改法**：
  1. **根治在 W1b**：outline 评审 `split` 动作把过载页拆页（已设计）。
  2. 渲染层兜底：`_render_bullet_v2` 当 `len(items) > 6` 时，不再静默 `[:6]` 丢弃，而是 **log.warning + 把超出项并进最后一张卡片**（参照 `_fill_design_bullets`:2662 已有的「末槽吃剩余」做法），保证不丢内容。
  3. 留白参数化：`_add_title_block` / 正文起始 top 抽成常量，便于调呼吸感（**低优先，避免动太多坐标引发回归**）。
- **风险与降级**：改坐标/留白易引发既有版式回归（视觉闭环是按当前坐标调过的）。→ W3.3 留白仅作可选项，**默认不改坐标**，优先靠 W1b split。
- **验收**：塞 8 条 bullet 的页 → W1b 拆成两页 **或** 渲染层末卡承载全部 8 条（无丢失），backend log 有 warning。

---

### W4 — 图标简陋

- **现状（实测 :632）**：`_add_icon_badge` = OVAL + 居中 `_add_text(label)`，字体写死 `Calibri`。调用处传的 label 是 **"1"/"2"/"A"/"B"/"S"/"IMG"** 等占位字符（:736/1164/1253/1303/1567），**确实没有语义图标**（00-PLAN「只彩色圆圈」夸张了——有字符，但是无意义编号/占位词）。
- **改法**：
  1. 新增 `_icon_for_keyword(text: str) -> str`：bullet 关键词 → **Unicode 符号/几何字形**映射表（如 增长→「↑」、目标→「◎」、时间→「⏱」、对比→「⇄」、风险→「⚠」、对勾→「✓」、想法→「💡」…），无命中 → 回退当前编号字符。
  2. `_add_icon_badge` 字体从写死 `Calibri` 改为 **优先 `Segoe UI Symbol`**（Win 自带，覆盖几何/箭头/对勾），回退 `Calibri`；font 设为参数，调用方可传。
  3. 在 `_add_item_card`(:718) / `_render_bullet_v2`(:1209) 调用处，把 `badge=str(idx+1)` 改为 `badge=_icon_for_keyword(item)`（保留编号作回退）。
- **风险与降级（这是 00-PLAN 列为「3 个待定方案」之一，本轮**定方案**）**：
  - ❌ **否决「嵌入图标字体到 pptx」**：python-pptx **不支持字体嵌入**；手搓 lxml 注入 embedded font 复杂且 WPS/PowerPoint 行为不一，**性价比极差**。
  - ❌ **否决「SVG→EMF」**：需外部转换器（Inkscape/cairosvg+第三方），引入重依赖与跨平台脆弱性，违背「单机桌宠少依赖」。
  - ✅ **选「系统符号字体 Unicode 字形」**：零依赖、零嵌入。代价是**字形依赖目标机字体**——Win 上 `Segoe UI Symbol` / emoji 可渲；**WPS 渲 PNG 时部分 emoji 可能显示成方框**。→ 映射表**优先用几何/数学符号**（↑◎⇄⚠✓ 等覆盖率高），**少用彩色 emoji**；任何字形缺失只是退化为普通字符，**不破版**。
  - ⚠ 中文字体：badge 内是符号不是中文，不受 YaHei 影响。
- **验收**：bullet 列表前出现**与语义相关**的符号（增长页→↑、风险页→⚠），非清一色「1/2/3」；WPS 截图确认符号真渲染（非方框）；缺字时回退编号不破版。

---

### W5 — 图片裁切生硬

- **现状（实测 :1340）**：`_place_cover` 做了 object-fit cover 等比裁切（焦点居中）；`_render_image_v2`(:1280) 用 `add_picture(width=...)` **只锁宽不锁高**——会按原图比例放（可能溢出框），且无裁切焦点控制。
- **改法**：
  1. `_place_cover` 加 `focus: str = "center"` 参数（`center`/`top`/`upper-third`），封面用 `upper-third` 给标题区让位（裁切时上移取景框）。
  2. 边缘羽化：现有 `_set_fill_alpha` 是给**形状**做半透明，**不能直接羽化图片边缘**（图片无 alpha 蒙版能力）→ **改法降级**：在图片上叠一个**与背景同色、从透明到实色的渐变矩形**做软过渡（python-pptx 渐变填充 via lxml `a:gradFill`），而非真羽化。
  3. split 版式图文交界加 1 个细色条分隔形状。
- **风险与降级**：
  - ⚠ 00-PLAN「微渐变蒙版边缘过渡」用 `_set_fill_alpha`**不可行**（那是形状透明度，非图片边缘 alpha）→ 已改为「叠渐变矩形」方案。
  - 渐变填充需手搓 `a:gradFill` lxml（python-pptx 高层 API 对渐变支持弱）→ 控制复杂度，**仅做单边线性渐变**，失败静默跳过。
  - 焦点上移可能把主体裁出框 → 仅对封面默认、内容图保持 center。
- **验收**：封面图主体不被硬切、标题区不压主体；split 交界有过渡；WPS 截图对比改前后。

---

### W7 — 内容数 ≠ 模板槽数（clone/del element）

- **现状（实测）**：`_fill_design_bullets`(:2644)：槽 ≥ 内容 → 多余槽清空文字（**形状仍在**，:2657）；槽 < 内容 → 末槽塞剩余全部（:2662，多条挤一个框）。`_best_design_content_page`(:2397) 选页已按 `n_body` 与 wanted 的差排序。**无 shape 级 clone/del**——只清文字不删形状。
- **改法**：
  1. **选页优先**（成本最低、最稳）：`_select_design_page`(:2454) bullet 分支已用 `_best_design_content_page(wanted)`；强化其评分让「槽数恰好≥内容且最接近」更优先（当前 key 已含 `n_body<wanted` 惩罚 + `abs` 差，**基本够用**，微调权重即可）。
  2. **槽过多 → del 余槽形状**（不止清文字）：`_clear_design_body_slots` 当前只 `_set_text_keep_style(shape, [])`。新增「删形状」路径——对多余 body 形状 `shape._element.getparent().remove(shape._element)`（lxml，已在 :494/:2334/:2341 用过同款 remove）。**默认仍清空文字**（删形状可能破坏设计页背景构图），del 仅在「空槽明显残留底色块」时启用，加开关谨慎。
  3. **槽不足 → clone 槽**：复用 :2346 `copy.deepcopy(p0._p)` 同款思路，但 clone 整个 body **shape** 的 `<p:sp>` 元素并下移定位。⚠ **高风险**（见下），列为 W7 **可选增强**，非必须。
- **风险与降级**：
  - ⚠ **clone shape 定位**：克隆出的形状会与原形状**完全重叠**，需手算 offset 下移；模板设计页布局千差万别，通用 offset 必翻车。→ **clone 列为最低优先**；主路径靠「选对槽数的页」(W7.1) + 「末槽吃剩余」(既有) + 「del 余槽」(W7.2)。
  - ⚠ del 形状不可逆且可能删掉设计页构图元素 → 仅删**纯文字 body 槽**（`info["bodies"]` 内的），绝不碰 `decor`/背景图。
  - python-pptx 对 clone 出的 shape 的 id/rId 唯一性无保证 → 若做 clone，需 `_drop`/重分配 spid（复杂）。**本轮建议只做 W7.1+W7.2，W7.3 clone 标注「需第 2/3 轮再评估」**（功能不删，降级实现）。
- **验收**：5 条内容进 4 槽页 → 选页阶段优先换 ≥5 槽页（不丢内容）；2 条进 6 槽页 → 余 4 槽无残留底色（del 或清空，截图无空块）。

---

### W8 — 细节装饰基础

- **现状**：`_add_footer`(:1777) 注释「无装饰条的小页码」；`_add_corner_motif`(:616) 圆角矩形+小圆点。装饰偏基础。
- **改法**：
  1. `_add_footer` 加**细线进度指示**（底部 `n/total` 宽度比例的细矩形），颜色 `theme.accent_rgb`。
  2. 标题区加主题色装饰线/几何点阵（参数化强度，默认弱）。
  3. section 过渡页加大号章节序号水印（半透明大数字）。
- **风险与降级**：装饰过度→喧宾夺主 + 撞文字（视觉闭环已为治重叠而生）。→ 强度参数默认保守；所有新装饰**置于文字之下的 z 序**或留足边距；逐项可关。
- **验收**：逐页页脚有进度条、section 有序号水印；视觉闭环不报新的 occlusion；WPS 截图观感「成品感」↑。

---

### W9 — 打包 / 兜底说明（文档 + 启动日志）

- **现状**：外部大库 gitignored（2.8GB 不进包）；`ppt_templates/通用商务/` 3 套 bundled 兜底（git 跟踪）；`template_library_root()` 外部优先回退 bundled。
- **改法**：
  1. **不改打包**（2.8GB 不可进包，结论正确）。
  2. STATUS/PPT.md §4 + SKILL.md 注明分发行为（外部库不进安装包、新机器走 3 套 bundled）。
  3. **加启动期 / 首次用 log**：`template_library_root()` 解析时 log 出「源=外部大库(<path>) / 兜底bundled / 无」。实测需 Grep 该函数当前是否已有日志，避免重复。
- **风险与降级**：纯文档 + 日志，无功能风险。
- **验收**：临时改 env `DESKPET_PPT_TEMPLATE_ROOT` 指向空目录 → backend log 明确「走 bundled 兜底」；文档与行为一致。

---

## 3. 优先级 / 顺序（微调 00-PLAN）

- **P0（质量天花板 + 高性价比）**：**W6（图表配色，API 已 grounded、改动局部、回报高）** ≈ W2（stale 修复，10 分钟） > W1a（SKILL 重写） > W1b（内容评审闭环）
- **P1（精细度）**：W3（密度，靠 W1b split） > W7.1+W7.2（选页 + del 余槽） > W5（图片）
- **P2（锦上添花）**：W4（图标 Unicode 字形） > W8（装饰） > W9（文档）
- **挑战说明**：00-PLAN 把 W1 排第一，但 **W6/W2 是「确定能做、改动小、立刻可见」**，建议先落 W6/W2 拿到快速反馈，再啃 W1b（涉及新文件 + LLM 闭环，迭代成本高）。

---

## 4. 统一验收（强化 00-PLAN）

1. **单测**：每 W 新增/改测试，`cd backend && python -m pytest -k ppt -v` 全绿；W6 断言 XML 出现主题色 srgbClr；W1b mock vision_chat 验降级不抛。
2. **冒烟**：`ppt_create` 三路径（设计页 / AI 图 / from-scratch）各出一份，**WPS 渲 PNG 对比改前后**（不能只看 XML——WPS 渲染差异是本项目实锤坑）。
3. **真机 windows-mcp**（铁律，不可用脚本/import/WS 替代）：同主题（深海 / 新能源）各出一份，截图存 `plans/manual-results-<date>/screenshots/`，肉眼 + backend log 验：①大纲信息密度↑ ②图表配色随主题、无网格 junk ③图标语义化、WPS 真渲染非方框。
4. **铁律**：真机 E2E + 截图存证；新文件即 `git add` + commit（防沙箱回滚）；改 backend 代码后必跑 pytest；任务完成同步更新 STATUS/PPT.md §3/§4。

---

## 5. 留给第 2/3 轮的空白

- W1b 的 outline 评审 prompt 全文 + JSON schema 字段冻结 + split/merge 的页号重排细节。
- W4 关键词→Unicode 符号映射表全表 + WPS 渲染缺字白名单（实测哪些符号 WPS 能渲）。
- W5 `a:gradFill` lxml 手搓的精确 XML 片段。
- W7.3 clone shape 的定位算法（offset 计算 / spid 重分配）—— 第 2 轮决定做不做。
- 各 W 项精确 diff / 函数签名变更 / pytest 用例清单。

---

## 本轮修正与挑战记录

### A. 纠正的事实错误（对 00-PLAN / STATUS）
1. `_render_chart_v2` 行号 **1650 → 1651**；并发现一个**死函数** `_render_chart`(:1027) 未挂 `_RENDERERS`，提醒别误改。
2. `ppt_create` 接线行号 **3216 → 3152**（parse 在 :3191）。
3. `_render_with_design_pages` **2938 → 2950**。
4. W4「图标只彩色圆圈无字形」**不准**：`_add_icon_badge` 实为 OVAL + 居中文字（渲染 "1/A/S/IMG" 等占位字符），问题是**字符无语义**而非「无字形」。
5. W6 字段名错误：`theme.highlight` → 实为 **`theme.highlight_rgb`**（Theme :238）。
6. W7「clone/del 是全新能力」**部分不准**：`copy.deepcopy(p0._p)` 段落克隆(:2346)、`getparent().remove()`(:2334/2341/494)、`_drop_slide_id`(:2936) 整页删 **均已存在**；真正新增的只是 **body shape 级**的 clone/del。
7. W3「shrink_text 只缩字号」**漏了** `_fit_text_to_shape`(:2506) 这个按槽高主动缩字号的既有兜底，新改法需与它协同。
8. 发现 `_render_bullet_v2` 对 >6 条 bullet 是**静默丢弃**（隐性数据丢失），00-PLAN 未点出。

### B. 否决 / 替换的不可行方案
1. **W1b apply「每个 issue 调一次 LLM 重写」→ 否决**（N 页 = N 次串行 LLM 调用，时延爆炸 + Clash 掐连接风险）。**替换为**：review 一次性产 `suggestion`，apply 纯本地操作，整闭环每轮仅 1 次 LLM 调用。
2. **W4「嵌入图标字体到 pptx」→ 否决**（python-pptx 不支持字体嵌入）。
3. **W4「SVG→EMF」→ 否决**（需重外部依赖、跨平台脆弱）。**替换为**：系统符号字体（Segoe UI Symbol）Unicode 几何字形，零依赖、缺字优雅回退。
4. **W5「`_set_fill_alpha` 做图片边缘羽化」→ 否决**（那是形状透明度，无法羽化图片边缘）。**替换为**：叠加单边 `a:gradFill` 渐变矩形软过渡。
5. **W7「clone body shape」→ 降级为可选/缓办**（clone 后重叠定位在任意模板上必翻车 + spid 唯一性问题）；主路径改为「选对槽数页 + del 余槽 + 既有末槽吃剩余」。

### C. 补的遗漏（00-PLAN 未列）
1. W1b 与 dry_run 路径(:3204) 的先后顺序（评审须在 dry_run 之前，让预览即深化后大纲）。
2. W1b split/merge 改页数 → 必须早于 W7 选页 / 视觉评审 / `_is_conclusion_slide` total 判定（已锁接线点）；并加 split 上限防膨胀。
3. W6 pie 图无 `value_axis` 的特判（直接设会抛）+ line 图线色应走 `format.line` 而非 `format.fill`。
4. W6/W4 **WPS vs PowerPoint 渲染差异**作为头号验收风险（逐点上色 / number_format / 符号字形在 WPS 可能退化）——必须 WPS 真截图，不能只信单测 XML。
5. W3 渲染层「>6 bullet 静默丢弃」的数据丢失兜底。
6. W1b/视觉闭环统一的 **pytest 跳过** + `[ppt].outline_review` 开关 + vision_chat 失败降级链路。
7. SKILL.md token 预算约束（gpt-5.5 窗口）：few-shot/页序模板需压行，防挤占大纲生成 token。
