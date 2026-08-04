# DeskPet PPT 质量优化 plan（v1 草案 — 待子代理 3 轮迭代细化）

> 2026-06-20 · 配套文档 [STATUS/PPT.md](../../STATUS/PPT.md)
> 范围决策(已与用户确认):**①大纲修复 = 混合(Skill 重写 + 工具层内容评审闭环)②全覆盖(大纲 + 6短板 + stale 修复 + 打包说明)**。
> 迭代约束:**不可少做功能;每轮逐步细化到具体代码实现**。

---

## 0. 回答用户三问

### Q1：生成的 PPT 质量如何？(现状客观评估)
基于两次真机产物 + 代码审计:
- **渲染/版式**:中上。模板设计页填充专业;AI 整页配图电影感强;视觉评审闭环能自动修溢出/重叠;chart 原生可编辑。
- **内容/大纲**:**差**(用户痛点)。bullet 空泛像目录词(深海 deck「黑暗中的生物发光/极端环境的生存智慧」零信息密度)、无数据支撑、无叙事逻辑、副标题辞藻空。
- **6 短板**(2026-06 评估主动 defer):信息密度失控 / 图标简陋(只彩色圆圈) / 图片裁切生硬 / **图表配色不随主题**(实锤:`_render_chart_v2` 用 pptx 默认色) / 内容数≠模板槽数 / 细节装饰基础。
- **总评 ≈ 3.3/5**:有审美意识但内容空、细节糙。

### Q2：怎么优化？
见 §2 工作项 W1–W9，每项细到函数。两条主线:**内容深度(大纲)** + **视觉精细度(6短板)**。

### Q3：大纲质量归属（已探讨结论）
**大纲 = skill/LLM 层职责**(工具层只渲染不改内容)。但采**混合方案**:Skill 层重写指导(治本+立即见效) + 工具层加「大纲内容评审闭环」(类比视觉评审,跨入口兜底,质量稳)。详 W1。

---

## 1. 架构定位（改哪、不改哪）

| 层 | 文件 | 本 plan 是否动 |
|---|---|---|
| Skill(写什么) | `ppt-generate/SKILL.md` | ✅ W1/W2 重写 |
| 工具-编排 | `ppt_tools.py::ppt_create` | ✅ W1 加内容评审 hook |
| 工具-内容评审(新) | `ppt_outline_review.py`(新) | ✅ W1 新增 |
| 工具-渲染 | `ppt_tools.py::_render_*` | ✅ W3-W8 |
| 工具-图表 | `_render_chart_v2` | ✅ W6 |
| 工具-图标 | `_add_icon_badge` | ✅ W4 |
| 工具-配图 | `_place_cover`/image | ✅ W5 |
| 模板选择 | `ppt_template_picker.py` | ✅ W7 部分 |
| 视觉评审 | `ppt_visual_review.py` | 复用 vision_chat,W6 配色不依赖它 |

---

## 2. 工作项（每项：现状→问题→改法(函数级)→验收）

### W1 — 大纲内容质量（混合：Skill 重写 + 工具层内容评审闭环）★最高优先

**W1a — 重写 `ppt-generate/SKILL.md`(skill 层治本)**
- 现状:质量要求全是格式约束(页数/条数/字数),无内容深度;「bullet 是提示词不是讲稿」反推浅。
- 改法:
  1. 加「内容深度三要素」:每条 bullet 必须含 **具体事实/数据/机制**,禁纯概念词(反例「极端环境的生存智慧」→正例「热泉口 2℃→400℃ 温差,管虫靠化能合成菌共生」)。
  2. 加 **好/坏大纲 few-shot 各 1 份**(同主题对比)。
  3. 加 **分场景结构模板**(汇报/教学/产品发布/方案各一套页序)。
  4. 加「数据型必有 chart 页」「每页一个核心论点(one-message-per-slide)」。
  5. 修 stale(见 W2)。
- 验收:同主题(深海)新旧大纲对比,bullet 信息密度肉眼显著提升。

**W1b — 新增工具层「大纲内容评审闭环」`ppt_outline_review.py`**
- 设计(类比 `ppt_visual_review.py`):
  - `review_outline(slides, topic) -> list[issue]`:把 outline(纯文本,无需渲染)发 `vision_chat`/文本 LLM,按规则查:空泛 bullet / 缺数据 / 结构断裂 / 标题党 / 单页信息过载。返回 `[{page, issue, action: deepen|split|merge|rewrite, suggestion}]`。从不抛,失败返 []。
  - `apply_outline_fixes(slides, issues) -> slides`:对 deepen/rewrite 调 LLM 重写该页内容(带 topic 上下文)。
- 接线 `ppt_create`(:3216 前,parse_outline 后、渲染前):新增 `_outline_review_loop(slides, topic)`,≤2 轮,`[ppt].outline_review` 开关(默认 on),pytest 跳过。
- 复用 `ppt_visual_review.vision_chat`(纯文本调用,传 text-only content)。
- 验收:喂一份故意空泛的 outline,闭环后 bullet 被深化;单测 mock vision 验证降级不抛。

### W2 — 修复 stale SKILL.md 模板名（我上次改动引入）
- 现状:SKILL.md:27 决策表硬编码旧 3 模板名(商务深蓝-水墨/高级感-蓝/简约高级-灰,已删),与新大类机制冲突 → LLM 按表传旧名 → 解析失败回落 from-scratch。
- 改法:决策表「模板填充」行改为「按 **大类名**(高级色/高级简约/通用商务)选,具体由桌宠看预览图挑」;删旧模板名;示例 outline 的 template 注释更新。
- 验收:用户说「商务/专业」→ LLM 传大类名 → design-pages 命中(非 from-scratch)。

### W3 — 信息密度失控
- 现状:`_visual_review_loop` 的 shrink_text 只缩字号治溢出,不治「一页塞太多」。
- 改法:
  1. W1 outline 评审加 `split`(一页拆两页)/`merge` 动作根治源头。
  2. 渲染层 `_render_bullet_v2` 加每页 bullet 数硬上限(>6 自动分页或降级提示)。
  3. `_add_title_block`/正文区留白比例参数化,提高呼吸感。
- 验收:塞 8 条 bullet 的页 → 自动拆/降密度。

### W4 — 图标简陋
- 现状:`_add_icon_badge`(:632)只 `MSO_SHAPE.OVAL` 彩色圆圈,无字形。
- 改法:
  1. 内置小型 **图标集**(Unicode 几何/箭头/对勾 或 内置 SVG 路径集),按 bullet 关键词→图标映射(`_icon_for_keyword`)。
  2. `_add_icon_badge` 改为圆底 + 居中图标字形(用 Segoe UI Symbol / 思源图标字体)。
  3. 评估嵌入图标字体到 pptx 避免缺字(或用 freepik 风格 SVG 转 emf)。
- 验收:bullet 列表前出现与语义相关的图标,非清一色圆点。

### W5 — 图片裁切生硬
- 现状:`_place_cover` 已 object-fit cover 比例裁切;但 split/card 等版式裁切焦点固定中心。
- 改法:
  1. 裁切焦点参数化(默认中心,封面可偏上 1/3 留标题区)。
  2. 图片加微渐变蒙版边缘过渡(`_set_fill_alpha` 已有,扩展到边缘羽化)。
  3. split 版式图文交界加分隔装饰。
- 验收:封面/split 图不再硬切人脸/主体;边缘自然。

### W6 — 图表配色不随主题 + 去 chart junk ★实锤易改
- 现状:`_render_chart_v2`(:1650)`add_chart` 后**未设任何颜色**,用 pptx 默认蓝橙灰。
- 改法(`_render_chart_v2` 内 `add_chart` 后):
  1. 调色板 = `[theme.secondary, theme.accent, theme.highlight, theme.primary, ...]`;遍历 `chart.plots[0].series` 设 `series.format.fill.solid()+fore_color.rgb`;pie 遍历 `points` 逐点上色。
  2. 去 chart junk:关 `chart.value_axis.has_major_gridlines=False`、轴线/字号随主题、`has_title=False`(标题已在页头)。
  3. 数据标签 `plot.has_data_labels=True` + 字号/颜色随主题。
- 验收:bar/line/pie 三类图配色 = 当前主题色,无默认蓝橙;无多余网格线。

### W7 — 内容数≠模板槽数
- 现状:`_select_design_page`/`_best_design_content_page`/`_clear_design_body_slots` 已部分缓解(贪心选页 + 清空余槽)。
- 改法:
  1. PPTAgent 式 **clone/del element**:槽不够→clone 一个 body 槽样式补;槽过多→del 余槽(非只清空文字,连形状删)。
  2. `_fill_design_page` 填充前按内容条数选「槽数最接近」的设计页(`_best_design_content_page` 评分加权槽数匹配度)。
- 验收:5 条内容进 4 槽页 → 不丢内容(扩槽或换页);2 条进 6 槽页 → 无空槽残留。

### W8 — 细节装饰基础
- 现状:from-scratch 装饰靠 `_add_corner_motif`/`_add_callout_card` 等基础形状。
- 改法:
  1. 页脚/页码/进度条精细化(`_add_footer` 加细线进度指示)。
  2. 标题区加主题色装饰线/几何点阵(参数化强度,避免喧宾夺主)。
  3. section 过渡页加大号章节序号水印。
- 验收:逐页观感更「成品感」,非裸版式。

### W9 — 打包/兜底说明（文档+校验）
- 现状:外部大库 gitignored 不进包;bundled 3 套兜底。
- 改法:不改打包(2.8GB 不可进包);在 SKILL.md/STATUS 注明分发行为;加启动期日志:库根解析结果(外部/兜底/无)。
- 验收:无外部库时日志明确提示走兜底。

---

## 3. 优先级 / 顺序

P0(质量天花板):W1(大纲) > W6(图表配色,易改高回报) > W2(stale 修复)
P1(精细度):W3(信息密度) > W7(槽位匹配) > W5(图片裁切)
P2(锦上添花):W4(图标) > W8(装饰) > W9(文档)

---

## 4. 统一验收

1. 单测:每 W 项新增/改测试,`-k ppt` 全绿。
2. 冒烟:`ppt_create` 三路径(模板/AI图/from-scratch)产物渲染对比改前后。
3. 真机 windows-mcp:同主题(深海/新能源)各出一份,肉眼 + backend log 验大纲深度↑、图表配色随主题、图标语义化。
4. 遵守项目铁律:真机 E2E + 截图存证,新文件即 commit。

---

## 5. 待迭代细化的空白（留给子代理 3 轮）
- W1b 内容评审的 prompt/JSON schema 具体设计 + 与现有 vision_chat 的 text-only 调用细节。
- W4 图标集的具体落地方式(字体 vs SVG vs emf)+ 关键词映射表。
- W6 python-pptx 设 series/point 颜色的精确 API 调用(版本兼容)。
- W7 clone/del element 的 lxml 具体操作。
- 各 W 项的精确 diff / 函数签名变更 / 测试用例清单。
