# DeskPet PPT 质量优化 plan v3（第 2 轮迭代：实现就绪 implementation-ready）

> 2026-06-20 · 上游 [01-iteration1.md](./01-iteration1.md) · [00-PLAN.md](./00-PLAN.md) · [STATUS/PPT.md](../../STATUS/PPT.md)
> 本轮目标：把第 1 轮已 grounded 的「方向」落成**可直接写代码的 diff / 函数签名 / 数据结构 / prompt 全文 / 配置键 / WPS 验收点**。
> 约束：**W1–W9 一个功能不删**。所有行号锚点已用 Read 在本轮重新核对（见文末「本轮深化记录 · 行号复核」）。

---

## 0. 本轮新增的「全局事实复核」（在第 1 轮基础上，本轮二次核对）

| 项 | 实测结论（本轮 Read 确认） | 用途 |
|---|---|---|
| import 现状(:132-138) | `Inches/Pt/Emu`、`RGBColor`(:133)、`MSO_SHAPE/PP_PLACEHOLDER`、`PP_ALIGN/MSO_ANCHOR/MSO_AUTO_SIZE`、`MSO_FILL_TYPE`、`XL_CHART_TYPE/XL_LEGEND_POSITION` 已全在顶部 `try` import 块。**W6 需新增 `XL_LABEL_POSITION`（data_labels 位置）** 到 :138 那行。`qn` 是局部 import(:488/:2693)。 |
| `_in_pytest()`(:61-64) | 判 `PYTEST_CURRENT_TEST` env；视觉闭环 `_visual_review_loop` 第一行就 `if _in_pytest(): return`。**W1b 闭环必须复用同一函数**（测试可 `monkeypatch.setattr(ppt_tools, "_in_pytest", lambda: False)` 放行，见 test_ppt_visual_review.py:91）。 |
| `_ppt_visual_review_enabled()`(:3358-3365) | 真实写法：`bool((_cfg.config.raw.get("ppt") or {}).get("visual_review", True))`，异常默认 True。**W1b 的 `_ppt_outline_review_enabled()` 照抄此写法**，键名 `outline_review`。 |
| 大类名常量(picker `CATEGORY_STYLE_HINTS`:41-44) | 合法三类逐字为 **`高级色` / `高级简约` / `通用商务`**（`is_category()` 用 `_normalize_category` 去编号后匹配目录）。W2 文案必须用这三个词。 |
| `template_library_root()`(picker:88-91) | = `_external_root() or _bundled_fallback_root()`，**当前无任何「源=外部/兜底」日志**。W9 需在此（或调用点 `_resolve_template_for_render`）补一条 info 日志。 |
| `SlideOutline`(:161-221) | dataclass，含 `font_scale`(:181) 已被视觉闭环复用；`normalize()`(:193) 会 strip + 限制 layout。W1b apply 产出的新页须经 `SlideOutline(...).normalize()` 才进列表。 |
| vision_chat 签名(:74-81) | `vision_chat(content_parts, *, system="", timeout=120.0, max_tokens=1500, temperature=0.0) -> str`。失败返 `""` 从不抛。W1b 直接调。 |
| `_parse_review_json`(:142-165) | 用 `re.search(r"\[.*\]", text, re.DOTALL)` 抠数组 + 逐字段强转。W1b 的 `_parse_outline_review_json` 照此结构造（字段不同）。 |
| `_fill_design_bullets`(:2644-2663) | 槽≥内容：逐槽填+余槽清空(:2656)；槽<内容：末槽 `_set_text_keep_style(bodies[-1], clean[len-1:])` 吃剩余(:2662)。**W3「不丢内容」其实模板路径已天然成立**；真正会丢的是 from-scratch 的 `_render_bullet_v2`(:1224 `items[:6]`)。 |

---

## W1a — 重写 `ppt-generate/SKILL.md`（内容深度治本）★最高优先

### 精确改动（按 SKILL.md 真实行号）

**改动点 1：替换 :71 那句「bullet 是提示词不是讲稿」**（这句把内容推浅）。

近似 diff（删 :71 整行，替换为「内容深度三要素」块，紧跟在 :70 节奏建议后）：

```diff
- - **不要**把整段长文本塞进 bullet——bullet 是提示词，不是讲稿。详细内容用 `notes` 字段写进备注页（演讲者可见）。
+ **内容深度三要素（每页都要过这三关，否则重写）**：
+ 1. **具体 > 概念**：每条 bullet 必须承载一个【数字 / 专有名词 / 机制 / 因果】，
+    禁止只写概念标签。详细解释放 `notes`（备注页），bullet 只留最实的那一句。
+ 2. **一页一论点（one-message-per-slide）**：每页围绕一个可被记住的核心结论，
+    bullet 是支撑它的证据，不是并列的目录词。
+ 3. **数据上图**：出现对比 / 趋势 / 占比的数字，用 `chart` 页而非堆进 bullet。
```

**改动点 2：在 schema 块（:59 ```后）与「内容质量要求」之间，加好/坏大纲 few-shot**（≤6 行，压行防挤占 token，见风险）。实际文案：

```markdown
**好 / 坏 bullet 对照（同主题「深海生态」）**：
- ❌ 空泛：「极端环境的生存智慧」「黑暗中的生命奇迹」「丰富的生物多样性」
- ✅ 具体：「热泉口 2℃↔400℃ 温差带，管虫靠化能合成菌共生固碳」
- ✅ 具体：「6000m 深海压强 600 个大气压，狮子鱼靠 TMAO 稳定蛋白质」
- ✅ 具体：「鮟鱇雌雄体型差 60 倍，雄性寄生融合供精」
判据：删掉主题名后这句还有信息 = 合格；只剩形容词 = 空泛，重写。
```

**改动点 3：把 :69-70 的节奏建议升级为「4 套分场景页序模板」（单行紧凑表，省 token）**：

```markdown
**分场景页序模板（按用途选一套，再按内容增减）**：
| 场景 | 页序骨架 |
|---|---|
| 汇报/总结 | title → toc → 现状(bullet) → 数据(chart) → 问题(bullet) → 对策(two_column) → section总结 → 结论 |
| 教学/科普 | title → 为什么重要(bullet) → 概念(bullet) → 机制(image/chart) → 案例(bullet) → 小结 |
| 产品发布 | title(image_full) → 痛点(bullet) → 方案(section) → 功能(two_column) → 数据(chart) → 行动号召(conclusion) |
| 方案/提案 | title → 背景(bullet) → 目标(bullet) → 路径(two_column) → 里程碑(chart) → 风险(bullet) → 总结 |
```

### 配置 / 向后兼容
- 纯文本文件，无 config。**总增量目标 ≤40 行**（few-shot 6 行 + 三要素 7 行 + 模板表 7 行 + 删 1 行），守住 gpt-5.5 8000 窗口预算（STATUS §9.1）。

### WPS 验收点
- 无（SKILL.md 不渲染）。靠 W1b + 真机对比验。

### 验收
- 同主题（深海）跑新旧 SKILL.md 各产 outline，逐条 bullet 数「含数字/专名」的占比应显著↑；windows-mcp 真机截图存证。

---

## W2 — 修复 stale SKILL.md 模板名（10 分钟，必先做）

### 精确改动（SKILL.md 真实行号）

**:27 决策表「模板填充」单元格**，整段替换为：

```diff
- 传 `template=<精选模板名>` —— **只能**从 schema 里列出的精选模板按主题选最贴的(教育/文化/政务→商务深蓝-水墨;科技/商业/产品→高级感-蓝;设计/品牌/高端→简约高级-灰),**别自己编模板名**。页面**不要**写 image_prompt。
+ 传 `template=<大类名>`，三选一：**高级色**(科技/商业/产品/营销/通用职场)、**高级简约**(设计/品牌/方案/学术/高端)、**通用商务**(无外部库时的兜底)。具体设计页由桌宠看预览图自动挑——**只传大类名这三个词之一，别编模板名、别传文件名**。页面**不要**写 image_prompt。
```

**:81 参数说明**：

```diff
- - `template`: 仅「模板填充」模式传(bundled 模板名或 .pptx 绝对路径)
+ - `template`: 仅「模板填充」模式传，值为大类名三选一：`高级色` / `高级简约` / `通用商务`（也接受 .pptx 绝对路径，但常规场景用大类名）
```

### 配置 / 向后兼容
- `_resolve_template_for_render`(:1979) 仍接受路径/stem（`is_category` 不命中走 `_resolve_template_path`），**旧的「直接传 .pptx 路径」行为不破**。

### WPS 验收点
- 无渲染差异项。

### 验收
- 真机说「做个商务/专业 PPT」→ backend log 出 `pick_template: vision chose id=...` + `_render_with_design_pages` 成功（**不是** `template not found - falling back`）。

---

## W6 — 图表配色随主题 + 去 chart junk ★实锤、API 已 grounded、最高性价比

### 精确改动

**新增 import**（:138 那行尾部追加）：

```diff
- from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
+ from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
```

**`_render_chart_v2`（:1695-1707）**：在 `chart.legend.include_in_layout = False`(:1704) 之后、`except`(:1705) 之前，插入下列三块（全在已有的 `try` 内，每块各自再套 try 防一处失败拖垮全部）。完整可落地片段：

```python
        chart = gframe.chart
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False

        # ── W6: 配色随主题 + 去 chart junk ──────────────────────────
        palette = [
            theme.secondary_rgb, theme.accent_rgb,
            theme.highlight_rgb, theme.primary_rgb,
        ]  # 注意字段名: highlight_rgb 不是 highlight
        plot = chart.plots[0]
        # (a) series / point 上色
        try:
            if ctype == "pie":
                for i, pt in enumerate(plot.series[0].points):
                    pt.format.fill.solid()
                    pt.format.fill.fore_color.rgb = RGBColor(*palette[i % len(palette)])
            else:
                for i, s in enumerate(plot.series):
                    col = RGBColor(*palette[i % len(palette)])
                    s.format.fill.solid()
                    s.format.fill.fore_color.rgb = col
                    if ctype == "line":            # 折线的「线色」走 line 而非 fill
                        try:
                            s.format.line.color.rgb = col
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("chart palette failed: %s", exc)
        # (b) 去 junk: 关图自带标题(页头已有) + 关网格线(pie 无值轴)
        try:
            chart.has_title = False
            if ctype != "pie":
                chart.value_axis.has_major_gridlines = False
                chart.value_axis.has_minor_gridlines = False
        except Exception as exc:  # noqa: BLE001
            log.debug("chart junk removal failed: %s", exc)
        # (c) data_labels: pie 显百分比, bar/line 显数值
        try:
            plot.has_data_labels = True
            dl = plot.data_labels
            dl.font.size = Pt(10)
            dl.font.color.rgb = RGBColor(*theme.text_rgb)
            if ctype == "pie":
                dl.number_format = "0%"
                dl.number_format_is_linked = False
                dl.show_percentage = True
                dl.position = XL_LABEL_POSITION.OUTSIDE_END
        except Exception as exc:  # noqa: BLE001
            log.debug("chart data labels failed: %s", exc)
        # ── /W6 ───────────────────────────────────────────────────
    except Exception as exc:  # noqa: BLE001  (既有最外层兜底, 不动)
        log.warning("chart render failed, degrading to cards: %s", exc)
        _render_bullet_v2(slide, outline, theme)
```

### 数据结构 / 常量
- **调色板取色顺序（冻结）**：`[secondary_rgb, accent_rgb, highlight_rgb, primary_rgb]`。理由：secondary 是主视觉强调色（teal/indigo/teal），accent 次强调（amber/cyan/red），highlight 第三，primary 是深底色放最后（避免一上来就大面积深色块）。`% len` 循环兜底超 4 系列。

### 配置 / 向后兼容
- 无新 config。`RGBColor`/`Pt` 已在 :132-133；仅 `XL_LABEL_POSITION` 为新 import。所有新代码在原 try 内，任何失败只退回默认外观，**不影响 chart 已 add 成功**，最外层降级 cards 逻辑不动。

### WPS 验收点（必须真截图，不能只信单测 XML）
1. **bar 图**：3 系列 → 三根柱分别为 secondary/accent/highlight 色，**无默认蓝橙灰**；柱顶有数值标签；纵向无横网格线。
2. **line 图**：线色随主题（不是默认蓝），标记点同色；无网格线。
3. **pie 图**：各扇区不同主题色；每扇区显 `xx%` 百分比标签（WPS 对 `dPt` 逐点上色 + `show_percentage` 支持度是头号风险点，必须肉眼确认，不行就记为 WPS 退化项）。

### 验收
- 单测：构造 bar/line/pie outline，渲染后用 lxml 解析 .pptx，断言 series/point 出现 `<a:srgbClr val=...>` 且值=主题 hex；`c:valAx` 下无 `c:majorGridlines`；`c:chart` 下 `c:autoTitleDeleted val="1"`。
- 真机：minimal/dark/playful 三主题各出一张图，WPS 截图。

---

## W1b — 新增 `ppt_outline_review.py`（大纲内容评审闭环）★

### 模块完整设计

**文件**：`backend/deskpet/tools/ppt_outline_review.py`

#### 函数签名

```python
def review_outline(
    slides: list["SlideOutline"], topic: str,
    *, timeout: float = 90.0,
) -> list[dict]:
    """把 outline 序列化成纯文本 → 一次 vision_chat 文本调用 → 解析 JSON。
    返回 issue 列表(见 schema)。失败/空 → []。从不抛异常。"""

def apply_outline_fixes(
    slides: list["SlideOutline"], issues: list[dict], topic: str,
    *, max_total_pages: int = 18, max_chars_per_bullet: int = 30,
) -> list["SlideOutline"]:
    """对 deepen/rewrite/split/merge **纯本地**应用 issue.suggestion(无 LLM 调用)。
    任何异常 → 返回原 slides。"""

def _parse_outline_review_json(text: str) -> list[dict]:
    """照 ppt_visual_review._parse_review_json 结构, 字段换成 outline 评审字段。"""

def _serialize_outline(slides, topic: str) -> str:
    """页号|layout|title|bullets/subtitle 多行纯文本(给 LLM 看)。"""
```

#### 内容评审 JSON schema（issue 结构，冻结）

```jsonc
[
  {
    "page": 2,                         // 1-based 页号; 0/越界 → apply 忽略
    "ok": false,
    "action": "deepen",                // ok | deepen | rewrite | split | merge
    "issues": ["bullet 全是概念标签，无数据支撑"],   // ≤3 条, 仅记录用
    "suggestion": {                    // action 所需的新内容, review 一次性产出
      "title": "深海高压生存机制",       // 可选, 缺省保留原
      "subtitle": "",                  // 可选
      "bullets": [                     // deepen/rewrite/split 用
        "6000m 深海 600 大气压，狮子鱼靠 TMAO 稳定蛋白",
        "管虫无消化道，化能合成菌共生固碳"
      ],
      "split_into": [                  // 仅 action=split: 拆成 2 页的 bullets
        {"title": "高压适应", "bullets": ["..."]},
        {"title": "化能生态", "bullets": ["..."]}
      ],
      "merge_with": 3                  // 仅 action=merge: 与第 N 页(1-based)合并
    }
  }
]
```

#### apply 的纯本地操作语义（治第 1 轮指出的「每 issue 一次 LLM 调用」时延爆炸）

- `deepen` / `rewrite`：用 `suggestion.bullets` 覆写该页 `bullets`；`title`/`subtitle` 给了就覆写。**每条 bullet 截断到 `max_chars_per_bullet`(30 汉字)** 防深化变加长（撞 W3）。
- `split`：用 `suggestion.split_into` 在原页位置 `insert` 拆成 2 页（原页替换为第 1 项，第 2 项 insert 到 +1）；**单轮全 deck 累计 split 上限 = 2 页**，且 `len(slides) > max_total_pages` 时拒绝再 split。
- `merge`：把 `page` 页 bullets 并进 `merge_with` 页，删除 `page` 页（只允许相邻，非相邻忽略）。
- 所有产出页经 `SlideOutline(**fields).normalize()` 入列；**任何单条 issue 异常 → skip 该条，不影响其余**。
- 处理顺序：先 deepen/rewrite（不改页数），再 merge（减页），最后 split（增页）——避免页号在处理中错位。**split/merge 阶段以「快照页号」一次性算好，逐条按倒序 page 应用**，防 insert/del 后索引漂移。

#### system prompt 全文草案（`_OUTLINE_SYSTEM`）

```text
你是资深 PPT 内容编辑。下面给你一份 PPT 大纲(纯文本, 每页含 页号/版式/标题/要点)。
请逐页审查【内容质量】(不管视觉排版), 只挑出真正有问题的页, 合格页不要输出。

判定一页「有问题」的标准(命中任一):
1. 空泛: bullet 是概念标签/形容词堆砌, 删掉主题名后几乎无信息(如「极端环境的生存智慧」)。
2. 缺数据: 该页本应有数字/专名/机制却只有泛泛而谈。
3. 信息过载: 一页要点 > 5 条, 或多个不相关论点挤在一页。
4. 标题党: 标题与要点不符, 或要点之间无逻辑。
5. 该拆该并: 一页讲了两件事(拆), 或两页内容单薄重复(并)。

对每个有问题的页, 给出修复动作(只能选一):
- "deepen": 内容方向对但太空 → 在 suggestion.bullets 给出【更具体】的新要点(带数字/专名/机制),
  每条 ≤30 汉字, 数量 ≤5。不要加长, 要换成更实的内容。
- "rewrite": 标题/要点跑题或逻辑乱 → suggestion 给整页新 title + bullets。
- "split": 一页两主题 → suggestion.split_into 给两页(各 title+bullets)。
- "merge": 与相邻页内容单薄重复 → suggestion.merge_with 给要合并到的页号。

输出严格 JSON 数组, 不要任何其它文字。合格页不出现在数组里。示例:
[{"page":3,"ok":false,"action":"deepen","issues":["全是概念标签"],
  "suggestion":{"bullets":["6000m 深海 600 大气压，狮子鱼靠 TMAO 稳定蛋白","管虫化能合成菌共生固碳"]}}]
```

#### 一次批量调用（治时延爆炸）

`review_outline` 把**整份 outline** 序列化进**一个** `content_parts=[{"type":"text","text": <_OUTLINE_SYSTEM 之外的大纲正文>}]`，调 `vision_chat(content, system=_OUTLINE_SYSTEM, max_tokens=1500, timeout=90)` **一次**。`apply_outline_fixes` 全本地。→ **整闭环每轮 = 1 次 LLM 调用**（与视觉评审同量级）。

#### 降级 / 异常 / pytest 跳过

```python
def _ppt_outline_review_enabled() -> bool:
    """config [ppt].outline_review (默认 True)。照 _ppt_visual_review_enabled 写法。"""
    try:
        import config as _cfg  # type: ignore[import-not-found]
        return bool((_cfg.config.raw.get("ppt") or {}).get("outline_review", True))
    except Exception:  # noqa: BLE001
        return True
```

- `vision_chat` 返 `""` → `_parse_outline_review_json` 返 `[]` → loop 原样透传 slides。
- 闭环函数（放 ppt_tools.py，复用 `_in_pytest`）：

```python
def _outline_review_loop(slides, topic, *, max_rounds=2):
    try:
        if _in_pytest() or not _ppt_outline_review_enabled():
            return slides
        from . import ppt_outline_review as _orv
        for _ in range(max_rounds):
            issues = _orv.review_outline(slides, topic)
            bad = [i for i in issues if not i.get("ok", True)]
            log.info("outline_review done pages=%d issues=%d", len(slides), len(bad))
            if not bad:
                break
            slides = _orv.apply_outline_fixes(slides, bad, topic)
        return slides
    except Exception as exc:  # noqa: BLE001
        log.warning("outline_review loop failed: %s", str(exc)[:200])
        return slides
```

#### 接线 ppt_create（真实行号）

在 `slides = parse_outline(outline)`(:3191) 与 `if not slides:`(:3192) 校验**之后**、`if dry_run:`(:3204) **之前**插入：

```python
    slides = parse_outline(outline)
    if not slides:
        return { ... }   # 不动
    # ── W1b: 大纲内容评审闭环(在 dry_run/渲染之前, 让预览即深化后大纲) ──
    topic = title or (slides[0].title if slides else "")
    slides = _outline_review_loop(slides, topic)
    # ── /W1b ──
    if dry_run:
        ...
```

理由：放在 dry_run 之前，用户预览的就是已深化大纲；放在 `wants_fullbleed`/conclusion/W7 选页**全部之前**，避免 split/merge 改页数后页号错位（`_is_conclusion_slide(index,total)`:1769 的 total 用最终页数）。

### 风险与降级
- split 改页数 → 已锁接线点在最前 + 单轮 ≤2 页 + 总 ≤18 页硬上限。
- deepen 变加长 → apply 截断每条 ≤30 汉字。
- vision 不可用 → 全链路返回原 slides，零影响（已验证 vision_chat 从不抛）。

### WPS 验收点
- 无渲染差异项（纯数据层）。验证看 backend log + 最终 .pptx 内容。

### 验收（pytest，照 test_ppt_visual_review.py 风格）
- `monkeypatch` `ppt_outline_review.vision_chat` 返回固定 JSON → 验 `apply_outline_fixes` 对 deepen/split/merge 的页数与内容。
- mock 返回 `""` → 验 loop 不抛、原样返回。
- 构造 split issue → 验 `len(slides)` +1、各页 bullets 正确、不超 18 上限。
- `monkeypatch _in_pytest=lambda:False` + config off → 验 loop 不调 LLM。
- 真机：喂故意空泛 outline → 闭环后 bullet 变实（截图 + log `outline_review done pages=.. issues=..`）。

---

## W3 — 信息密度失控（根治靠 W1b split + 渲染层不丢内容）

### 精确改动

**`_render_bullet_v2`（:1224）**：把静默丢弃改成「末卡吃剩余 + warning」。近似 diff：

```diff
     rows = 2 if len(items) <= 4 else 3
     card_w = Inches(4.16)
     card_h = Inches(1.14 if rows == 3 else 1.48)
     start_top = Inches(1.46)
-    for idx, item in enumerate(items[:6]):
+    if len(items) > 6:
+        log.warning("bullet page overflow: %d items > 6 slots, merging tail", len(items))
+        items = items[:5] + ["；".join(items[5:])]   # 末卡承载剩余, 不丢内容
+    for idx, item in enumerate(items[:6]):
         row, col = divmod(idx, 2)
         _add_item_card(...)
```

- 同理 callout 分支 :1208 `items[:4]`：保守起见**本轮只改非 callout 主分支**（callout 分支已有 `rest` 兜底，改动收益小、回归风险大）。

### 配置 / 向后兼容
- 无 config。仅行为从「丢」改「并」+ warning。模板路径 `_fill_design_bullets`(:2662) 本就不丢内容（末槽吃剩余），无需改。

### WPS 验收点
- 塞 8 条 bullet 的 from-scratch 页：WPS 截图确认末卡含合并文本、无 bullet 消失。

### 验收
- 单测：8 条 bullet 渲染后，解析 .pptx 文本框文字，断言 8 条内容字符全部出现（无丢失）；log 有 warning。
- W1b split 优先把过载页拆开（联动验收）。

---

## W4 — 图标语义化（系统符号字体 Unicode 字形，零依赖）

### 精确改动

**新增映射表 + 函数**（放 `_add_icon_badge`(:632) 之前）：

```python
# W4: bullet 关键词 → 几何/数学符号(优先 Win 自带 Segoe UI Symbol 可渲, 少用彩色 emoji)
_ICON_KEYWORD_MAP: list[tuple[tuple[str, ...], str]] = [
    (("增长", "提升", "上升", "增加", "提高", "growth", "increase", "up"), "↑"),
    (("下降", "降低", "减少", "缩减", "下滑", "decline", "down"), "↓"),
    (("目标", "方向", "聚焦", "定位", "goal", "target", "focus"), "◎"),
    (("时间", "周期", "阶段", "时长", "效率", "time", "speed"), "⏱"),
    (("对比", "比较", "对照", "权衡", "vs", "compare"), "⇄"),
    (("风险", "警告", "注意", "隐患", "挑战", "risk", "warning"), "⚠"),
    (("完成", "达成", "成功", "通过", "优势", "done", "success", "advantage"), "✓"),
    (("想法", "创新", "灵感", "策略", "方案", "idea", "innovation"), "✦"),
    (("数据", "指标", "统计", "占比", "metric", "data", "stat"), "▦"),
    (("用户", "客户", "团队", "人群", "user", "team", "people"), "◍"),
    (("钱", "成本", "收入", "营收", "预算", "cost", "revenue", "money"), "¤"),
    (("流程", "步骤", "环节", "链路", "process", "step", "flow"), "→"),
    (("全球", "市场", "范围", "网络", "global", "market", "network"), "✺"),
    (("安全", "保护", "稳定", "可靠", "security", "stable"), "⛉"),
    (("增", "升", "扩", "强"), "↑"),  # 单字兜底, 放末尾
]

def _icon_for_keyword(text: str, fallback: str = "") -> str:
    """bullet 文本 → 语义符号; 无命中返回 fallback(通常是编号字符)。从不抛。"""
    try:
        t = str(text or "")
        for keywords, glyph in _ICON_KEYWORD_MAP:
            if any(k in t for k in keywords):
                return glyph
        return fallback
    except Exception:  # noqa: BLE001
        return fallback
```

**`_add_icon_badge`(:654)**：字体改可参数化 + 默认优先 Segoe UI Symbol：

```diff
 def _add_icon_badge(
     slide, label, *, left, top, size, theme,
     fill=None, text_color=None,
+    font_name: str = "Segoe UI Symbol",   # 覆盖箭头/几何/对勾; 缺字回退普通字符不破版
 ):
     _add_shape(slide, MSO_SHAPE.OVAL, left=left, top=top, width=size, height=size,
                fill=fill or theme.secondary_rgb)
     return _add_text(
         slide, label, ...,
-        font_name="Calibri",
+        font_name=font_name,
         align="center", anchor="middle", margin=0.0,
     )
```

**调用点改 badge 取符号（保留编号作 fallback）**：
- `_render_bullet_v2`:1213 `badge=str(idx + 1)` → `badge=_icon_for_keyword(item, str(idx + 1))`
- `_render_bullet_v2`:1231 同上（用 `item`）
- `_add_item_card`(:736) 内部不动（它收 badge 参数），由调用方决定；toc(:1647) 保持编号（目录用数字更合理，**不改**）。

### 数据结构 / 常量（已冻结于上表）
- 选符号原则：全部用 **几何/数学/箭头符号**（↑↓◎⏱⇄⚠✓✦▦◍¤→✺⛉），WPS 渲 PNG 覆盖率高；**不用彩色 emoji**（WPS 易渲成方框）。

### 配置 / 向后兼容
- 无 config。`font_name` 默认参数，旧调用（如 two_column 传 "A"/"B"）不传该参数仍走 Segoe UI Symbol——"A"/"B" 在该字体也正常渲，无破坏。**保守起见 two_column 的 A/B 标识保留字母不走映射**（语义就是 A 方案 B 方案）。

### WPS 验收点（必须真截图）
- 增长页 bullet 前出现 `↑`、风险页出现 `⚠`、对比页出现 `⇄`——**WPS 截图确认是符号不是方框/豆腐块**；缺字时回退编号不破版（删 Segoe UI Symbol 模拟缺字测一次）。

### 验收
- 单测：`_icon_for_keyword("营收增长 30%")=="↑"`、`_icon_for_keyword("市场风险")=="⚠"`、`_icon_for_keyword("普通文本", "3")=="3"`。
- 真机：WPS 截图符号真渲染。

---

## W5 — 图片裁切焦点 + 软过渡

### 精确改动

**`_place_cover`(:1340)** 加 `focus` 参数控制裁切取景：

```diff
-def _place_cover(slide, img, *, left, top, width, height) -> bool:
+def _place_cover(slide, img, *, left, top, width, height, focus: str = "center") -> bool:
```

在裁上下分支(:1366-1369) 用 focus 调整：

```python
            elif img_ar < slot_ar:
                frac = (1.0 - img_ar / slot_ar) / 2.0
                if focus == "top":
                    pic.crop_top = 0.0; pic.crop_bottom = frac * 2.0
                elif focus == "upper-third":
                    pic.crop_top = frac * 0.5; pic.crop_bottom = frac * 1.5
                else:
                    pic.crop_top = frac; pic.crop_bottom = frac
```

- 封面变体调用处（`_fullbleed_picture`/cover 版式）传 `focus="upper-third"` 给标题区让位；内容图保持 `center`（默认，零回归）。

**软过渡（降级方案，第 1 轮已否决羽化）**：split 版式图文交界叠一个**与面板同色的窄渐变矩形**。本轮**降级为「叠纯色半透明窄条」**（`_set_fill_alpha` 已支持形状半透明），不手搓 `a:gradFill`——理由：lxml 渐变在 WPS 渲染不稳定，性价比低，留第 3 轮再评估真渐变。窄条用现有 `_add_shape` + `_set_fill_alpha`。

### 配置 / 向后兼容
- `focus` 默认 `center`，所有现有调用（`_fullbleed_picture`/`_side_picture`）不传则行为完全不变。

### WPS 验收点（必须真截图）
- 封面人像图：主体（脸）不被裁出框、底部三分之一留给标题；改前后 WPS 截图对比。
- split 交界有可见过渡条（非硬切边）。

### 验收
- 单测：`_place_cover(..., focus="top")` 后 `pic.crop_top==0`；`focus="center"` 行为与原一致。
- 真机：封面截图主体完整。

---

## W7 — 内容数 ≠ 模板槽数（选页优先 + del 余槽；clone 缓办）

### 精确改动

**W7.1 选页评分微调** — `_best_design_content_page`(:2403) 的 key 已含 `n_body<wanted` 惩罚 + `abs` 差，**本轮判定「基本够用，不改」**（避免动排序引发回归）。仅在验收发现选页明显不对时再调。

**W7.2 槽过多 → del 余槽形状**（不止清文字）。`_clear_design_body_slots`(:2390) 加可选 `delete=False` 参数：

```diff
-def _clear_design_body_slots(info, *, keep=()):
+def _clear_design_body_slots(info, *, keep=(), delete: bool = False):
     keep_ids = {id(shape) for shape in keep if shape is not None}
     for shape in info.get("bodies", []):
         if id(shape) not in keep_ids:
-            _set_text_keep_style(shape, [])
+            if delete:
+                try:
+                    shape._element.getparent().remove(shape._element)  # 同 :2334/2341 用法
+                except Exception:  # noqa: BLE001
+                    _set_text_keep_style(shape, [])
+            else:
+                _set_text_keep_style(shape, [])
```

- **默认 `delete=False`（清空文字，零回归）**。仅当确认「空槽残留明显底色块」时由调用方传 `delete=True`。本轮**接线保守：先不在主路径开启 delete**，只把能力做进去 + 单测覆盖，留真机观察后再决定是否默认开。

**W7.3 clone 槽** — 第 1 轮已降级。**本轮维持缓办**（clone 后重叠定位在任意模板必翻车 + spid 唯一性）。主路径靠 W7.1 选对页 + `_fill_design_bullets` 末槽吃剩余(:2662，已不丢内容)。

### 配置 / 向后兼容
- `delete` 默认 False，旧行为不破。`getparent().remove()` 仅作用于 `info["bodies"]`（纯文字槽），绝不碰 decor/背景。

### WPS 验收点（真截图）
- 5 条内容进 4 槽页：截图确认 5 条内容全在（末槽合并，无丢失）。
- 2 条进 6 槽页（若启 delete）：余 4 槽无残留空底色块。

### 验收
- 单测：`_clear_design_body_slots(info, delete=True)` 后 `info["bodies"]` 对应 shape `_element` 已从 parent 移除（mock 一个含 bodies 的 info）。

---

## W8 — 细节装饰

### 精确改动

**`_add_footer`(:1777)** 加细线进度条。在页码 `_add_text` 后追加：

```python
    # W8: 底部细线进度指示(当前页占比宽度), 弱强度, 不撞文字
    try:
        frac = max(0.04, min(1.0, page_number / max(total, 1)))
        _add_shape(
            slide, MSO_SHAPE.RECTANGLE,
            left=Inches(0.0), top=_SLIDE_HEIGHT - Inches(0.04),
            width=int(_SLIDE_WIDTH * frac), height=Inches(0.04),
            fill=theme.accent_rgb, line=None,
        )
    except Exception:  # noqa: BLE001
        pass
```

**section 过渡页大号序号水印** — `_render_conclusion_v2` / section 渲染加半透明大数字（z 序在文字下，用 `_set_fill_alpha` 弱化）。本轮**列为可选**，强度参数默认弱；标题区装饰线同理可选。

### 配置 / 向后兼容
- 无 config。进度条置于页面最底部 0.04in 高，**不与正文区重叠**（正文从 1.4in 起）。`_add_footer` 已被所有非 title 页调用(:3326)，自动全覆盖。

### WPS 验收点（真截图）
- 逐页底部有 accent 色进度条且宽度随页号递增；视觉闭环不报新的 occlusion。

### 验收
- 单测：渲染后断言每页底部存在一个 `top≈slide_height-0.04in` 的矩形 shape。
- 真机：WPS 截图观感。

---

## W9 — 打包 / 兜底说明（文档 + 启动日志）

### 精确改动

**`template_library_root()`(picker:88-91)** 加源日志（首次解析时）：

```diff
 def template_library_root() -> Optional[Path]:
-    return _external_root() or _bundled_fallback_root()
+    ext = _external_root()
+    if ext is not None:
+        log.info("ppt template lib: external=%s", ext)
+        return ext
+    bundled = _bundled_fallback_root()
+    if bundled is not None:
+        log.info("ppt template lib: bundled fallback=%s (external lib missing)", bundled)
+        return bundled
+    log.warning("ppt template lib: none (external + bundled both missing)")
+    return None
```

- ⚠ 该函数被 `list_categories`/`category_pptx` 高频调用 → 日志会刷屏。**改为 `log.info` 但加一次性缓存**（模块级 `_logged_root_once = False`）或降为 `log.debug`。本轮决策：**用模块级布尔只 log 一次**，避免刷屏。

**文档**：STATUS/PPT.md §4 + SKILL.md 已基本说明分发行为，本轮补一句「打包 app/新机器仅 3 套 bundled 兜底」即可。

### 配置 / 向后兼容
- 纯日志 + 文档，无功能改动。

### WPS 验收点
- 无。

### 验收
- 临时设 `DESKPET_PPT_TEMPLATE_ROOT` 指向空目录 → backend log 出 `bundled fallback=...`；指向真库 → `external=...`。

---

## 优先级 / 落地顺序（承第 1 轮，按实现就绪度重排）

- **P0**：W2（10min stale 修复）→ W6（chart 配色，diff 已就绪）→ W1a（SKILL 重写）→ W1b（新模块）
- **P1**：W3（密度兜底）→ W4（图标符号）→ W7.2（del 余槽，能力先做不默认开）
- **P2**：W5（图片 focus）→ W8（装饰）→ W9（日志+文档）

---

## 统一验收（承第 1 轮，本轮补 pytest 用例清单）

新增/改动测试文件：
- `tests/test_ppt_chart_theme_w6.py`：bar/line/pie XML 断言主题色/无网格/无标题。
- `tests/test_ppt_outline_review_w1b.py`：mock vision_chat 验 deepen/split/merge/降级（照 test_ppt_visual_review.py 的 monkeypatch 风格）。
- `tests/test_ppt_bullet_overflow_w3.py`：8 条 bullet 不丢内容 + warning。
- `tests/test_ppt_icon_keyword_w4.py`：映射表断言。
- `tests/test_ppt_design_del_slots_w7.py`：del 余槽形状移除。
- 全量：`cd backend && python -m pytest -k ppt -v` 全绿。

真机（铁律，windows-mcp，不可脚本/import/WS 替代）：同主题（深海/新能源）各出模板版 + AI 图版，截图存 `plans/manual-results-<date>/screenshots/`，肉眼 + log 验①大纲密度↑ ②chart 配色随主题无 junk ③图标符号真渲染 ④进度条/封面焦点。

---

## 本轮深化记录

### A. 把「方向」落成「具体代码」的清单
1. **W6** → 完整可落地代码块（含 import 增量、调色板顺序冻结、pie/line 特判、每步独立 try、data_labels OUTSIDE_END）。
2. **W1b** → 整模块设计：4 函数签名 + issue JSON schema 冻结 + `_OUTLINE_SYSTEM` prompt 全文 + 一次批量调用 + apply 纯本地语义（deepen/rewrite/split/merge 处理顺序与倒序应用防漂移）+ `_ppt_outline_review_enabled`（照真实写法）+ `_outline_review_loop` + ppt_create 接线 diff（:3191↔:3204 之间）。
3. **W4** → 关键词→符号映射表全表（14 组，全几何/数学符号，含单字兜底）+ `_icon_for_keyword` + `_add_icon_badge` font 参数化 diff + 调用点改法。
4. **W1a/W2** → SKILL.md 重写后的实际文案（三要素块、好坏 few-shot 实际文本、4 套页序紧凑表、:27/:81 大类名修正 diff）。
5. **W3/W5/W7/W8/W9** → 各自近似 diff（末卡吃剩余、`focus` 参数、`delete` 参数、进度条片段、日志一次性输出）。

### B. 新确定的 schema / 常量 / 文案
- W6 调色板顺序：`[secondary_rgb, accent_rgb, highlight_rgb, primary_rgb]`（冻结）。
- W1b issue schema：`{page, ok, action∈{ok,deepen,rewrite,split,merge}, issues[], suggestion{title?,subtitle?,bullets[],split_into[],merge_with}}`（冻结）。
- W1b 上限：单轮 split ≤2 页、总 ≤18 页、每 bullet ≤30 汉字。
- W4 映射表 14 组（冻结）。
- 大类名三词：`高级色`/`高级简约`/`通用商务`（逐字核对 picker）。
- 新 config 键：`[ppt].outline_review`（默认 True，与 `[ppt].visual_review` 并列）。
- 新 import：`XL_LABEL_POSITION`（W6）。
- 新函数参数默认值（均向后兼容）：`_place_cover(focus="center")`、`_add_icon_badge(font_name="Segoe UI Symbol")`、`_clear_design_body_slots(delete=False)`。

### C. 行号复核（本轮 Read 二次确认，与第 1 轮一致）
`_add_icon_badge`:632 · `_render_bullet_v2`:1193(末卡 :1224) · `_place_cover`:1340 · `_render_chart_v2`:1651(上色插入点 :1704↔1705) · `_is_conclusion_slide`:1769 · `_add_footer`:1777 · `_resolve_template_for_render`:1979 · `_clear_design_body_slots`:2390 · `_best_design_content_page`:2397 · `_select_design_page`:2454 · `_fill_design_bullets`:2644(末槽 :2662) · `ppt_create`:3152(parse :3191/dry_run :3204/分派 :3249) · `_ppt_visual_review_enabled`:3358 · vision_chat(ppt_visual_review):74 · `_parse_review_json`:142。

### D. 仍存疑、待第 3 轮敲死
1. **W6 在 WPS 的真实渲染**：`dPt` 逐点上色 + `show_percentage` + `OUTSIDE_END` 在 WPS COM 渲 PNG 是否真生效——必须真截图，可能要为 pie 准备「WPS 退化白名单」。
2. **W1b apply 的 split 页号倒序算法**：多条 split + merge 混合时的精确索引重排（本轮给了「先 deepen→merge→split、倒序应用」原则，第 3 轮要写出确定性的逐步实现 + 测试矩阵）。
3. **W7.2 del 余槽是否默认开**：需真机看几套模板「空槽残留底色块」严重程度后决定（本轮只做能力不默认开）。
4. **W5 真渐变 `a:gradFill`**：本轮降级为纯色半透明窄条；若真机观感不足，第 3 轮再评估手搓 gradFill 的精确 XML。
5. **W8 section 水印 / 标题装饰线强度**：默认弱，具体 alpha/字号待真机微调。
6. **W4 单字兜底「增/升/扩/强」**：可能误命中（如「特强调」），第 3 轮按真机产物决定是否保留单字组。
