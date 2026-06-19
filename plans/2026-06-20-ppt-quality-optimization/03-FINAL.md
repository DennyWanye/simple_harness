# DeskPet PPT 质量优化 plan v3（第 3/3 轮 · 最终可施工 FINAL）

> 2026-06-20 · 上游 [02-iteration2.md](./02-iteration2.md) · [01-iteration1.md](./01-iteration1.md) · [00-PLAN.md](./00-PLAN.md) · [STATUS/PPT.md](../../STATUS/PPT.md)
> 本轮目标：**敲死第 2 轮留下的 5 个存疑点**（每点：最终决定 + 代码/XML + 理由），产出**单一最终计划**——每个 W1–W9 含最终函数签名/diff、数据结构、配置、逐项测试用例清单、WPS 真机截图验收点；附执行顺序依赖图、feature-parity 清单、统一验收协议、风险登记。
> 约束：**W1–W9 一个功能不删**。本文自包含，可直接据以施工。
> 本轮所有 python-pptx API、ppt_tools.py 行号锚点均已用 Read 在 venv 源码 + 真实文件二次核对（见文末「源码复核」）。

---

## 第 0 部分 · 5 个存疑点的最终结论（敲死）

### ① W6 — pie 逐点上色（`dPt`）+ `show_percentage` 在 WPS 是否真渲染

**源码确认**（venv `pptx/chart/`）：
- `Point.format`（`point.py:74-81`）→ `self._ser.get_or_add_dPt_for_point(idx)` → `ChartFormat(dPt)`。即逐点上色写的是标准 `<c:dPt><c:idx><c:spPr><a:solidFill>` —— 这是 OOXML 规范结构，**WPS 完整支持**（WPS 自己导出的饼图就是这个结构）。
- `ChartFormat.fill`（`chtfmt.py:24-31`）→ `get_or_add_spPr()` → `FillFormat`，`fill.solid()` + `fore_color.rgb` 走标准 `a:solidFill/a:srgbClr`。**无 WPS 兼容问题。**
- `plot.data_labels`（`plot.py:52-60`）：**必须先 `plot.has_data_labels = True`**，否则抛 `ValueError("plot has no data labels, set has_data_labels = True first")`。第 2 轮代码已遵守（先置 True 再取 `dl`），✅。
- `DataLabels.show_percentage`（`datalabel.py:120-122`）→ `get_or_add_showPercent().val` → 标准 `<c:showPercent val="1"/>`，**饼图专用、WPS 支持**。
- `DataLabels.number_format`（`datalabel.py:47-50`）setter 会**自动**把 `number_format_is_linked` 置 False，所以第 2 轮里 `dl.number_format="0%"` 后再显式 `dl.number_format_is_linked=False` 是**冗余但无害**，保留无妨。
- `XL_LABEL_POSITION.OUTSIDE_END`（`enum/chart.py:341` → `outEnd`）。

**头号风险 = `OUTSIDE_END` 用在 pie 上**：OOXML 规范中饼图 `dLblPos` 合法值仅 `{ctr, inEnd, outEnd, bestFit}`。`outEnd` 对 pie **语义合法**，但部分 WPS 版本对 pie 的 `outEnd`（带引导线）渲染不稳，可能触发"文件修复"或退化为默认位置。

**最终决定（敲死）**：
1. **pie 的 data label 位置改用 `BEST_FIT`（`bestFit`）而非 `OUTSIDE_END`** —— `bestFit` 是 PowerPoint/WPS 对饼图的默认推荐位，兼容性最高，不会触发修复提示，且无引导线渲染负担。bar/line 不设 position（用各自默认 = bar 在 outEnd、line 在默认），避免给柱状/折线设非法位。
2. **逐点上色 + `show_percentage` 保留**（结构标准，WPS 支持已确认）。
3. **WPS 退化兜底（白名单机制）**：新增模块级常量 `_WPS_PIE_DLBL_SAFE = True`（默认 True）。若真机截图发现某环境 pie 标签渲染异常，把它置 False → 退化为**只上色 + 关 number_format、不强制 position**（即 `show_percentage` 仍开但不设 `dl.position`，让渲染器自选）。这是"可一键回退"的开关，不是删功能。
4. 每个子步骤独立 `try`（已在第 2 轮代码中），任一失败只退默认外观，不拖垮整图。

**最终 W6 代码** → 见 §W6（已据本结论把 `OUTSIDE_END` 改 `BEST_FIT` + 加 `_WPS_PIE_DLBL_SAFE` 开关）。

---

### ② W1b — split + merge 混合的确定性页号算法（倒序处理防 index 漂移）

**问题**：一轮评审可能同时返回多条 `split`（增页）、`merge`（减页）、`deepen`/`rewrite`（不改页数），每条带 1-based `page`。若按返回顺序逐条 `insert`/`del`，先处理的会让后处理条目的 `page` 失效（index 漂移）。

**最终算法（敲死）—— 三阶段 + 倒序应用**：

```
输入: slides(list, 0-based 内部索引), issues(list, 每条含 1-based page, action, suggestion)
快照: N0 = len(slides); 所有 issue 的 page 一律按"评审时的原始页号"解释(快照页号), 不随处理变化

阶段 A — deepen / rewrite (不改页数, 任意顺序安全):
  for issue in issues where action in {deepen, rewrite}:
    i = issue.page - 1
    if 0 <= i < len(slides):
      覆写 slides[i] 的 title/subtitle/bullets (bullets 每条截断 ≤ max_chars_per_bullet)

阶段 B — merge (减页):
  收集 merge 条目 → 仅保留"相邻"(abs(page - merge_with)==1)且 page/merge_with 都在 [1,N0] 内的; 其余忽略
  按 page 降序排序  ← 关键: 倒序删, 大 index 先删, 不影响小 index
  for issue in sorted(merges, key=page, reverse=True):
    src = issue.page - 1; dst = issue.merge_with - 1
    slides[dst].bullets += slides[src].bullets      # 内容并过去
    del slides[src]                                  # 删源页
  (注: merge 后页数变, 但因为是倒序删 + 后续 split 用"独立的相对锚定", 不会错乱; 见阶段 C)

阶段 C — split (增页):
  split 条目按 page 降序排序  ← 关键: 倒序插, 先处理大 index, insert 不影响更小 index 的定位
  for issue in sorted(splits, key=page, reverse=True):
    if len(slides) >= max_total_pages: break        # 总页硬上限 18
    if 本轮已 split 页数 >= 2: break                  # 单轮 split ≤ 2 页
    # 用"内容指纹"重定位(防 merge 阶段已改页数): 见下"重定位"
    i = _relocate(slides, issue)                      # 找回该页当前真实 index
    if i is None: continue
    parts = issue.suggestion.split_into[:2]
    slides[i] = SlideOutline(title=parts[0].title, bullets=parts[0].bullets, layout=原layout).normalize()
    slides.insert(i+1, SlideOutline(title=parts[1].title, bullets=parts[1].bullets, layout=原layout).normalize())
    本轮已 split += 1

返回 slides
```

**重定位 `_relocate`（解决"merge 改了页数后 split 的快照页号失效"）**：
- 简化但确定的方案：**阶段 B 与阶段 C 不共享被改动的页**。评审 prompt 里**明确要求**："同一页不要既 split 又 merge；merge 的两页与 split 的页不重叠"。apply 侧再加一道保险：**若某页同时出现在 merge 的 src/dst 集合与 split 集合中，丢弃该 split**（记 log）。这样阶段 C 执行时，被 merge 删掉的页不会再被 split 引用。
- `_relocate(slides, issue)`：用 `issue.page` 的快照页号，减去"阶段 B 中所有 page < 本 issue.page 的已删页数"做偏移修正：`i = issue.page - 1 - deleted_before`，其中 `deleted_before = sum(1 for m in applied_merges if m.deleted_index < issue.page-1)`。若修正后 `slides[i].title` 与 `issue` 记录的原 title 不一致 → 返回 None（放弃该 split，安全降级）。

**测试矩阵（敲死，落到 `test_ppt_outline_review_w1b.py`）**：

| 用例 | 输入 issues | 期望 |
|---|---|---|
| T-B1 纯 deepen | `[{page:2,action:deepen,bullets:[x,y]}]` | 第2页 bullets=[x,y]，页数不变，每条≤30字 |
| T-B2 单 split | `[{page:2,action:split,split_into:[A,B]}]` | len+1；原第2页→A，新第3页→B；其余页不动 |
| T-B3 多 split 倒序 | `[{page:1,split},{page:3,split}]` | len+2；page1、page3 各拆开，page1 拆出的不影响 page3 定位 |
| T-B4 单 merge | `[{page:3,action:merge,merge_with:2}]` | len-1；第2页 bullets 含原第3页内容；第3页消失 |
| T-B5 split+merge 混合 | `[{page:2,merge_with:1},{page:4,split}]` | 不重叠 → 先 merge(len-1) 再 split(len+1)，净页数不变，内容正确 |
| T-B6 split 撞上限 | 3 条 split，初始已 17 页 | 最多 split 到 18 页即停（≤2 页/轮 + ≤18 总） |
| T-B7 page 越界 | `[{page:0},{page:99}]` | 全部忽略，slides 原样 |
| T-B8 同页 split+merge 冲突 | `[{page:2,split},{page:2,merge_with:1}]` | split 被丢弃（记 log），仅 merge 生效 |
| T-B9 非相邻 merge | `[{page:5,merge_with:1}]` | 忽略（仅允许相邻） |

---

### ③ W7.2 — del 余槽（连形状删）默认开还是关

**最终决定（敲死）：默认关（`delete=False`），仅做能力 + 单测覆盖，主路径不接线开启。**

**理由**：
1. 设计页（外部大库 250 套）的 body 槽常带**背景色块/边框作为构图的一部分**；空槽"残留底色块"是否难看**高度依赖具体模板**，无法在不看真机的情况下判定普遍开启是净收益。
2. `getparent().remove()` 不可逆，误删构图元素的回归风险 > 残留空槽的观感损失。`_fill_design_bullets`(:2662) 已有"末槽吃剩余 + 余槽清空文字"，**功能上不丢内容、不报错**——del 只是观感优化，不是正确性需求。
3. 第 2 轮已定"能力先做、留真机观察"，本轮维持：施工时只加 `delete` 参数 + 单测，**真机 W7 截图阶段**若发现某类模板残留明显，再由调用方按模板类型有选择地传 `delete=True`（而非全局默认开）。

**lxml 具体操作（敲死）** — 改 `_clear_design_body_slots`(:2390)，现签名 `(info, *, keep=())`：

```python
def _clear_design_body_slots(
    info: dict[str, Any], *, keep: Sequence[Any] = (), delete: bool = False,
) -> None:
    keep_ids = {id(shape) for shape in keep if shape is not None}
    for shape in info.get("bodies", []):
        if id(shape) in keep_ids:
            continue
        if delete:
            try:
                # 与 :2334/2341/494 既有 getparent().remove() 同款; 仅作用于纯文字 body 槽
                shape._element.getparent().remove(shape._element)
            except Exception:  # noqa: BLE001
                _set_text_keep_style(shape, [])   # 删失败 → 退回清空文字
        else:
            _set_text_keep_style(shape, [])
```

约束：**只对 `info["bodies"]`（纯文字槽）生效，绝不碰 `info["decor"]`/背景图/标题。** 主路径调用处 `_clear_design_body_slots(info, keep=...)` 不传 `delete` → 行为零变化。

---

### ④ W5 — 图片边缘软过渡：上真 `a:gradFill` 还是确认降级

**源码确认**：`FillFormat.gradient()`（`fill.py:73`）能建渐变，但 `_GradFill.gradient_stops`（:265）产出的是 python-pptx **默认 2 段灰阶渐变**，**stop 的颜色/位置/alpha 需要逐个 lxml 改**才能做"实色→透明"。而真正的"边缘羽化"需要 stop 带 `<a:alpha>`（透明度渐变），python-pptx 高层 API **不暴露 alpha-per-stop**，必须手搓 lxml。

**最终决定（敲死）：确认降级——不上 `a:gradFill`，用「纯色半透明窄条」。**

**理由**：
1. 手搓带 alpha 的多 stop `a:gradFill` 注入到 picture/shape 的 `spPr`，在 **WPS COM 渲 PNG** 时渐变方向/alpha 插值与 PowerPoint 不一致（本项目实锤过 WPS 渲染差异），调试成本高、收益（边缘"柔和度"）边际。
2. 桌宠场景"软过渡"的核心诉求 = **图文交界不是生硬直角硬切**，一条"与面板同色的半透明窄条"已能达成视觉缓冲，且用现有 `_add_shape` + `_set_fill_alpha`（已支持形状半透明）**零新依赖、WPS 渲染确定**。
3. 留作"第 N 轮再评估"——若未来确有羽化刚需，再单开。本轮**不留 gradFill 代码**，避免半成品。

**最终实现** → 见 §W5（`focus` 参数 + 纯色半透明窄条，无 gradFill）。

---

### ⑤ W4 — 单字关键词兜底误命中风险

**问题**：第 2 轮映射表末行 `(("增","升","扩","强"), "↑")` 单字兜底，会误命中"特强调""升级换代"（语义非"增长")甚至"强制""扩展名"。

**最终决定（敲死）：删除单字兜底组，改为「双字/词组优先 + 无命中回退编号」，零单字。**

**理由**：单字命中召回率提升有限，但误命中（把"风险"页配 ↑、把"强制关闭"配 ↑）直接误导观众，**负价值 > 正价值**。宁可无命中回退编号（安全），不要误命中。

**防误命中规则（敲死）**：
1. **全部用 ≥2 字中文词 / ≥3 字英文词**做 key，删 `("增","升","扩","强")` 整组。
2. **最长匹配优先**：映射表按 key 长度无关、但**列表顺序 = 优先级**；把"风险/警告"等强语义、易混词放在"增长/提升"之前？——不需要，因为已无单字，双字词本身歧义极低。保持现有 14 组顺序，仅删末行单字组 → 13 组。
3. **互斥保护**：对"下降/降低"组放在"增长/提升"组之后无所谓（一条 bullet 同时含"增长"和"下降"极罕见，先命中"增长"可接受）。
4. 无命中 → 回退 `fallback`（调用方传的编号字符如 "1"/"2"），**永不留空、永不破版**。

**最终映射表（13 组，敲死）** → 见 §W4。

---

## 第 1 部分 · 架构定位（最终行号，本轮 Read 复核）

| 层 | 文件 / 函数 | 动作 | 真实行号（本轮核对） |
|---|---|---|---|
| Skill | `ppt-generate/SKILL.md` | W1a/W2 重写 | :27 决策表 / :71 质量句 / :81 参数 |
| 编排 | `ppt_tools.py::ppt_create` | W1b 接线 | parse :3191 / dry_run :3204 |
| 内容评审(新) | `ppt_outline_review.py` | W1b 新建 | — |
| 图表 | `_render_chart_v2` | W6 | :1651（插入点 :1704↔:1705） |
| 图标 | `_add_icon_badge` | W4 | :632 |
| 配图 | `_place_cover` | W5 | :1340（crop :1360-1369） |
| 密度 | `_render_bullet_v2` | W3 | :1193（items[:6] :1224） |
| 设计页槽 | `_clear_design_body_slots` / `_best_design_content_page` / `_fill_design_bullets` | W7 | :2390 / :2397 / :2644（末槽 :2662） |
| 装饰 | `_add_footer` | W8 | :1777 |
| 模板库根 | `ppt_template_picker.template_library_root` | W9 | picker :88-91 |
| 共享原语 | `ppt_visual_review.vision_chat` | W1b 复用 | :74 |

既有事实（施工前必知）：`_in_pytest()`:61、`_ppt_visual_review_enabled()`:3358、`_parse_review_json`:142、Theme 字段名 `highlight_rgb`（非 highlight）、import 块 :132-138（`RGBColor`/`Pt` 已在，仅需加 `XL_LABEL_POSITION`）、大类名三词逐字 `高级色`/`高级简约`/`通用商务`。

---

## 第 2 部分 · 最终工作项（W1–W9）

> 每项含：最终 diff/签名 → 数据结构/常量 → 配置/向后兼容 → 测试用例清单（单测函数名 + 断言要点）→ WPS 真机截图验收点。

---

### W1a — 重写 `ppt-generate/SKILL.md`（内容深度治本）★P0

**最终 diff**（按 SKILL.md 真实行号，总增量 ≤40 行，守 gpt-5.5 8000 窗口）：

**改动 1（替换 :71）**：
```diff
- - **不要**把整段长文本塞进 bullet——bullet 是提示词，不是讲稿。详细内容用 `notes` 字段写进备注页（演讲者可见）。
+ **内容深度三要素（每页都要过这三关，否则重写）**：
+ 1. **具体 > 概念**：每条 bullet 必须承载一个【数字 / 专有名词 / 机制 / 因果】，禁止只写概念标签。详细解释放 `notes`，bullet 只留最实的那一句。
+ 2. **一页一论点（one-message-per-slide）**：每页围绕一个可被记住的核心结论，bullet 是支撑它的证据。
+ 3. **数据上图**：出现对比 / 趋势 / 占比的数字，用 `chart` 页而非堆进 bullet。
```

**改动 2（schema 块 :59 后加 few-shot，≤6 行）**：
```markdown
**好 / 坏 bullet 对照（同主题「深海生态」）**：
- ❌ 空泛：「极端环境的生存智慧」「黑暗中的生命奇迹」「丰富的生物多样性」
- ✅ 具体：「热泉口 2℃↔400℃ 温差带，管虫靠化能合成菌共生固碳」
- ✅ 具体：「6000m 深海 600 个大气压，狮子鱼靠 TMAO 稳定蛋白质」
判据：删掉主题名后这句还有信息 = 合格；只剩形容词 = 空泛，重写。
```

**改动 3（:69-70 节奏建议升级为 4 套页序紧凑表）**：
```markdown
**分场景页序模板（按用途选一套，再按内容增减）**：
| 场景 | 页序骨架 |
|---|---|
| 汇报/总结 | title→toc→现状(bullet)→数据(chart)→问题(bullet)→对策(two_column)→section→结论 |
| 教学/科普 | title→为什么重要(bullet)→概念(bullet)→机制(image/chart)→案例(bullet)→小结 |
| 产品发布 | title(image_full)→痛点(bullet)→方案(section)→功能(two_column)→数据(chart)→号召(conclusion) |
| 方案/提案 | title→背景→目标→路径(two_column)→里程碑(chart)→风险→总结 |
```

**配置/兼容**：纯文本，无 config。**测试**：SKILL.md 不渲染，无单测；靠 W1b + 真机验。**WPS 验收点**：无（靠 W1b 与真机对比）。
**真机验收**：同主题（深海）跑新旧 SKILL.md 各产 outline，逐条 bullet 数"含数字/专名"占比应显著↑，截图存证。

---

### W2 — 修复 stale SKILL.md 模板名 ★P0（10 分钟，必先做）

**最终 diff**：

**:27 决策表「模板填充」单元格**：
```diff
- 传 `template=<精选模板名>` —— **只能**从 schema 里列出的精选模板按主题选最贴的(教育/文化/政务→商务深蓝-水墨;科技/商业/产品→高级感-蓝;设计/品牌/高端→简约高级-灰),**别自己编模板名**。页面**不要**写 image_prompt。
+ 传 `template=<大类名>`，三选一：**高级色**(科技/商业/产品/营销/通用职场)、**高级简约**(设计/品牌/方案/学术/高端)、**通用商务**(无外部库时兜底)。具体设计页由桌宠看预览图自动挑——**只传这三个词之一，别编模板名、别传文件名**。页面**不要**写 image_prompt。
```

**:81 参数说明**：
```diff
- - `template`: 仅「模板填充」模式传(bundled 模板名或 .pptx 绝对路径)
+ - `template`: 仅「模板填充」模式传，值为大类名三选一：`高级色` / `高级简约` / `通用商务`（也接受 .pptx 绝对路径，但常规用大类名）
```

**配置/兼容**：`_resolve_template_for_render`(:1979) 仍接受路径/stem，旧"直传 .pptx 路径"不破。**测试**：无单测（纯文本）。**WPS 验收点**：无渲染差异。
**真机验收**：说「做个商务/专业 PPT」→ backend log 出 `pick_template: vision chose id=...` + design-pages 成功（**不是** `template not found - falling back`）。

---

### W6 — 图表配色随主题 + 去 chart junk ★P0（API 已 grounded，最高性价比）

**新增 import**（:138 行尾）：
```diff
- from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
+ from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
```

**模块级常量**（放调色板逻辑附近，作 WPS 退化开关）：
```python
_WPS_PIE_DLBL_SAFE = True   # ① 结论: True=pie 用 bestFit+show_percentage; 若真机异常置 False 退化
```

**`_render_chart_v2` 最终代码**（替换 :1701-1704，插在 `chart = gframe.chart` 后、`except`:1705 前；据①把 `OUTSIDE_END`→`BEST_FIT`、加退化开关）：

```python
        chart = gframe.chart
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False

        # ── W6: 配色随主题 + 去 chart junk ──────────────────────────
        palette = [
            theme.secondary_rgb, theme.accent_rgb,
            theme.highlight_rgb, theme.primary_rgb,
        ]  # 字段名: highlight_rgb 不是 highlight; 顺序冻结
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
                    if ctype == "line":            # 折线"线色"走 line 而非 fill
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
        # (c) data_labels: pie 显百分比(bestFit, WPS 安全), bar/line 显数值
        try:
            plot.has_data_labels = True          # 必须先开, 否则 plot.data_labels 抛 ValueError
            dl = plot.data_labels
            dl.font.size = Pt(10)
            dl.font.color.rgb = RGBColor(*theme.text_rgb)
            if ctype == "pie":
                dl.show_percentage = True
                dl.number_format = "0%"           # setter 会自动置 is_linked=False
                if _WPS_PIE_DLBL_SAFE:
                    dl.position = XL_LABEL_POSITION.BEST_FIT   # ① WPS 安全位; outEnd 改 bestFit
                # _WPS_PIE_DLBL_SAFE=False 时不设 position, 让渲染器自选(退化兜底)
        except Exception as exc:  # noqa: BLE001
            log.debug("chart data labels failed: %s", exc)
        # ── /W6 ───────────────────────────────────────────────────
    except Exception as exc:  # noqa: BLE001  (既有最外层兜底, 不动)
        log.warning("chart render failed, degrading to cards: %s", exc)
        _render_bullet_v2(slide, outline, theme)
```

**数据结构**：调色板顺序冻结 `[secondary_rgb, accent_rgb, highlight_rgb, primary_rgb]`，`% len` 循环兜底超 4 系列。
**配置/兼容**：仅 `XL_LABEL_POSITION` 新 import；所有新代码在原 try 内，失败只退默认外观，不影响 chart 已 add 成功。

**测试用例清单**（`tests/test_ppt_chart_theme_w6.py`，渲染后用 lxml 解析 .pptx 断言）：
- `test_bar_series_colored_with_theme`：3 系列 bar → 每 series `c:spPr/a:solidFill/a:srgbClr@val` ∈ 主题 hex 集；柱状 `c:valAx` 下无 `c:majorGridlines`。
- `test_line_series_line_color`：line → 每 series `c:spPr/a:ln/a:solidFill/a:srgbClr` = 主题色。
- `test_pie_points_colored`：pie → `c:ser/c:dPt/c:spPr/a:solidFill/a:srgbClr` 出现 N 个（逐点）。
- `test_pie_show_percentage_and_bestfit`：pie → `c:dLbls/c:showPercent@val="1"`；`_WPS_PIE_DLBL_SAFE=True` 时 `c:dLblPos@val="bestFit"`。
- `test_chart_title_deleted`：所有类型 → `c:chart/c:autoTitleDeleted@val="1"`（`has_title=False`）。
- `test_chart_degrades_to_cards_on_bad_spec`：空 categories → 走 `_render_bullet_v2`，不抛。
- `test_wps_safe_off_no_position`：`monkeypatch _WPS_PIE_DLBL_SAFE=False` → pie 无 `c:dLblPos`，但仍有 `showPercent`。

**WPS 真机截图验收点**（必须肉眼，不能只信 XML）：
1. **bar**：3 系列三柱分别 secondary/accent/highlight 色，无默认蓝橙灰；柱顶有数值；纵向无横网格线。
2. **line**：线色随主题（非默认蓝），标记点同色；无网格线。
3. **pie**：各扇区不同主题色；每扇区显 `xx%` 标签且位置自然（bestFit）；**无"文件需修复"提示**。若 pie 标签异常 → 置 `_WPS_PIE_DLBL_SAFE=False` 重测，记为 WPS 退化项。

---

### W1b — 新增 `ppt_outline_review.py`（大纲内容评审闭环）★P0

**文件**：`backend/deskpet/tools/ppt_outline_review.py`

**最终函数签名**：
```python
def review_outline(slides, topic, *, timeout: float = 90.0) -> list[dict]:
    """outline 序列化纯文本 → 一次 vision_chat 文本调用 → 解析 JSON。返回 issue 列表; 失败/空 → []。从不抛。"""

def apply_outline_fixes(slides, issues, topic, *, max_total_pages: int = 18, max_chars_per_bullet: int = 30) -> list:
    """对 deepen/rewrite/merge/split 纯本地应用(无 LLM)。三阶段+倒序(见 ② 算法)。任何异常 → 返回原 slides。"""

def _parse_outline_review_json(text: str) -> list[dict]:
    """照 ppt_visual_review._parse_review_json 结构(re.search r'\[.*\]' DOTALL + 逐字段强转)。"""

def _serialize_outline(slides, topic: str) -> str:
    """页号|layout|title|bullets 多行纯文本。"""
```

**issue JSON schema（冻结）**：
```jsonc
[{
  "page": 2, "ok": false,
  "action": "deepen",        // ok | deepen | rewrite | split | merge
  "issues": ["bullet 全是概念标签"],
  "suggestion": {
    "title": "...", "subtitle": "",
    "bullets": ["6000m 深海 600 大气压，狮子鱼靠 TMAO 稳定蛋白", "管虫化能合成菌共生固碳"],
    "split_into": [{"title":"高压适应","bullets":["..."]}, {"title":"化能生态","bullets":["..."]}],
    "merge_with": 3
  }
}]
```

**apply 语义（据 ② 敲死）**：三阶段 deepen/rewrite → merge → split；merge 按 page 降序倒序删；split 按 page 降序倒序插 + `_relocate` 偏移修正 + title 校验；同页 split/merge 冲突丢弃 split；单轮 split ≤2、总 ≤18、每 bullet ≤30 字。所有产出页经 `SlideOutline(**fields).normalize()` 入列；单条 issue 异常 skip 不影响其余。

**`_OUTLINE_SYSTEM` prompt（最终，含"split/merge 不重叠"约束）**：
```text
你是资深 PPT 内容编辑。下面给你一份 PPT 大纲(纯文本, 每页含 页号/版式/标题/要点)。
请逐页审查【内容质量】(不管视觉排版), 只挑出真正有问题的页, 合格页不要输出。

判定一页「有问题」(命中任一):
1. 空泛: bullet 是概念标签/形容词堆砌, 删掉主题名后几乎无信息(如「极端环境的生存智慧」)。
2. 缺数据: 该页本应有数字/专名/机制却只泛泛而谈。
3. 信息过载: 一页要点 > 5 条, 或多个不相关论点挤一页。
4. 标题党: 标题与要点不符, 或要点之间无逻辑。
5. 该拆该并: 一页讲两件事(拆), 或两页内容单薄重复(并)。

对每个有问题的页给一个动作(只能选一):
- "deepen": 方向对但太空 → suggestion.bullets 给【更具体】的新要点(带数字/专名/机制), 每条 ≤30 汉字, ≤5 条。换更实的内容, 不要加长。
- "rewrite": 标题/要点跑题或逻辑乱 → suggestion 给整页新 title + bullets。
- "split": 一页两主题 → suggestion.split_into 给两页(各 title+bullets)。
- "merge": 与相邻页单薄重复 → suggestion.merge_with 给要合并到的页号。

重要约束: 一页不要既 split 又 merge; split 的页与 merge 涉及的页不要重叠; 整份大纲最多拆 2 页。

输出严格 JSON 数组, 不要任何其它文字。合格页不出现在数组里。示例:
[{"page":3,"ok":false,"action":"deepen","issues":["全是概念标签"],
  "suggestion":{"bullets":["6000m 深海 600 大气压，狮子鱼靠 TMAO 稳定蛋白","管虫化能合成菌共生固碳"]}}]
```

**一次批量调用**：整份 outline 序列化进一个 `content_parts=[{"type":"text","text":<大纲正文>}]`，`vision_chat(content, system=_OUTLINE_SYSTEM, max_tokens=1500, timeout=90)` 一次。apply 全本地 → 整闭环每轮 1 次 LLM 调用。

**开关 + 闭环**（放 ppt_tools.py，复用 `_in_pytest`）：
```python
def _ppt_outline_review_enabled() -> bool:
    try:
        import config as _cfg  # type: ignore[import-not-found]
        return bool((_cfg.config.raw.get("ppt") or {}).get("outline_review", True))
    except Exception:  # noqa: BLE001
        return True

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

**接线 ppt_create**（parse :3191 之后、dry_run :3204 之前）：
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

**配置/兼容**：新 config 键 `[ppt].outline_review`（默认 True，与 `visual_review` 并列）。vision 不可用 → 全链路返原 slides，零影响。

**测试用例清单**（`tests/test_ppt_outline_review_w1b.py`，照 visual_review monkeypatch 风格）：
- 解析：`test_parse_outline_json_tolerates_fences` / `test_parse_outline_garbage_returns_empty`。
- apply（即 ② 测试矩阵 T-B1~B9）：`test_apply_deepen_truncates_30` / `test_apply_single_split` / `test_apply_multi_split_reverse_order` / `test_apply_single_merge` / `test_apply_split_merge_mixed_disjoint` / `test_apply_split_hits_18_cap` / `test_apply_page_out_of_range_ignored` / `test_apply_same_page_split_merge_conflict_drops_split` / `test_apply_nonadjacent_merge_ignored`。
- 闭环降级：`test_loop_skips_in_pytest`（默认 `_in_pytest=True` → 不调 LLM）；`test_loop_vision_empty_returns_original`（mock `vision_chat`→"" → 原样、不抛）；`test_loop_disabled_by_config`（monkeypatch `_in_pytest=lambda:False` + config off → 不调 LLM）。
- 闭环正路：`test_loop_applies_one_round`（mock `review_outline` 返 1 条 deepen → 第2轮返 [] 收敛 → bullets 已深化）。

**WPS 验收点**：无渲染差异（纯数据层）。验看 backend log + 最终 .pptx 内容。
**真机验收**：喂故意空泛 outline → 闭环后 bullet 变实（截图 + log `outline_review done pages=.. issues=..`）。

---

### W3 — 信息密度失控（W1b split 根治 + 渲染层不丢内容）★P1

**最终 diff `_render_bullet_v2`（:1224，把静默丢弃改"末卡吃剩余 + warning"）**：
```diff
-    for idx, item in enumerate(items[:6]):
+    if len(items) > 6:
+        log.warning("bullet page overflow: %d items > 6 slots, merging tail", len(items))
+        items = items[:5] + ["；".join(items[5:])]   # 末卡承载剩余, 不丢内容
+    for idx, item in enumerate(items[:6]):
         row, col = divmod(idx, 2)
```
callout 分支(:1208 `items[:4]`)本轮**不改**（已有 `rest` 兜底，改动收益小回归风险大）。模板路径 `_fill_design_bullets`(:2662) 本就不丢。

**配置/兼容**：无 config，仅行为从"丢"改"并"+warning。
**测试**（`tests/test_ppt_bullet_overflow_w3.py`）：`test_8_bullets_no_loss`（8 条渲染后解析 .pptx 文本框，断言 8 条字符全在）；`test_overflow_logs_warning`（caplog 含 "bullet page overflow"）；`test_le6_unchanged`（≤6 条行为不变）。
**WPS 验收点**：塞 8 条 bullet 的 from-scratch 页，截图确认末卡含合并文本、无 bullet 消失。

---

### W4 — 图标语义化（系统符号字体 Unicode 字形，零依赖）★P1

**最终映射表（13 组，删单字兜底，据 ⑤）+ 函数**（放 `_add_icon_badge`:632 之前）：
```python
# W4: bullet 关键词 → 几何/数学符号(Win 自带 Segoe UI Symbol 可渲, 不用彩色 emoji)
# 全部 ≥2 字中文 / ≥3 字英文, 无单字兜底(防误命中, 见 ⑤)
_ICON_KEYWORD_MAP: list[tuple[tuple[str, ...], str]] = [
    (("增长", "提升", "上升", "增加", "提高", "growth", "increase"), "↑"),
    (("下降", "降低", "减少", "缩减", "下滑", "decline"), "↓"),
    (("目标", "方向", "聚焦", "定位", "goal", "target", "focus"), "◎"),
    (("时间", "周期", "阶段", "时长", "效率", "time", "speed"), "⏱"),
    (("对比", "比较", "对照", "权衡", "compare"), "⇄"),
    (("风险", "警告", "注意", "隐患", "挑战", "risk", "warning"), "⚠"),
    (("完成", "达成", "成功", "通过", "优势", "success", "advantage"), "✓"),
    (("想法", "创新", "灵感", "策略", "方案", "idea", "innovation"), "✦"),
    (("数据", "指标", "统计", "占比", "metric", "data", "stat"), "▦"),
    (("用户", "客户", "团队", "人群", "user", "team", "people"), "◍"),
    (("成本", "收入", "营收", "预算", "cost", "revenue", "budget"), "¤"),
    (("流程", "步骤", "环节", "链路", "process", "step", "flow"), "→"),
    (("全球", "市场", "范围", "网络", "global", "market", "network"), "✺"),
]  # 注: 删第2轮末行 ("增","升","扩","强") 单字组; "vs"/"up"/"down"/"money" 等≤2字英文也删, 防误命中

def _icon_for_keyword(text: str, fallback: str = "") -> str:
    """bullet 文本 → 语义符号; 无命中返回 fallback(通常编号字符)。从不抛。"""
    try:
        t = str(text or "")
        for keywords, glyph in _ICON_KEYWORD_MAP:
            if any(k in t for k in keywords):
                return glyph
        return fallback
    except Exception:  # noqa: BLE001
        return fallback
```

**`_add_icon_badge`(:654) font 参数化**：
```diff
 def _add_icon_badge(
     slide, label, *, left, top, size, theme,
     fill=None, text_color=None,
+    font_name: str = "Segoe UI Symbol",   # 覆盖箭头/几何/对勾; 缺字回退普通字符不破版
 ):
     _add_shape(slide, MSO_SHAPE.OVAL, ...)
     return _add_text(
         slide, label, ...,
-        font_name="Calibri",
+        font_name=font_name,
         align="center", anchor="middle", margin=0.0,
     )
```

**调用点改 badge（保留编号作 fallback）**：`_render_bullet_v2`:1213 `badge=str(idx+1)`→`badge=_icon_for_keyword(item, str(idx+1))`；:1231 同理用 `item`。toc(:1647) 保持编号（目录用数字合理，不改）；two_column A/B 标识保留字母（语义就是 A/B 方案，不走映射）。

**配置/兼容**：无 config；`font_name` 默认参数，旧调用不破。
**测试**（`tests/test_ppt_icon_keyword_w4.py`）：`test_growth_arrow`（`_icon_for_keyword("营收增长 30%")=="↑"`）；`test_risk_warning`（`"市场风险"=="⚠"`）；`test_no_match_fallback`（`"普通文本","3"=="3"`）；`test_no_single_char_false_positive`（`"特强调重点","9"=="9"` —— 验证删单字组后"强"不误命中 ↑）；`test_empty_safe`（`_icon_for_keyword("","X")=="X"`）。
**WPS 验收点**：增长页前 `↑`、风险页 `⚠`、对比页 `⇄`——截图确认是符号不是方框/豆腐块；删 Segoe UI Symbol 模拟缺字测一次回退编号不破版。

---

### W5 — 图片裁切焦点 + 软过渡（确认降级，无 gradFill，据 ④）★P2

**`_place_cover`(:1340) 加 `focus` 参数**：
```diff
-def _place_cover(slide, img, *, left, top, width, height) -> bool:
+def _place_cover(slide, img, *, left, top, width, height, focus: str = "center") -> bool:
```
裁上下分支(:1365-1369) 用 focus：
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
封面变体（`_fullbleed_picture`/cover）传 `focus="upper-third"` 给标题区让位；内容图保持 `center`（默认零回归）。

**软过渡（最终：纯色半透明窄条，无 gradFill）**：split 版式图文交界用 `_add_shape`(RECTANGLE) + `_set_fill_alpha` 叠一条与面板同色、约 0.06in 宽的半透明窄条（alpha≈40%），置于图与文之间。**不手搓 `a:gradFill`**（④ 确认降级，WPS 渲染不稳、收益边际）。

**配置/兼容**：`focus` 默认 `center`，现有调用不传则完全不变。
**测试**（`tests/test_ppt_place_cover_focus_w5.py`）：`test_focus_top_crop_top_zero`（`focus="top"` 后 `pic.crop_top==0`）；`test_focus_center_unchanged`（`center` 与原逻辑一致）；`test_focus_upper_third`（crop_top < crop_bottom）。（注：需真图，可用 PIL 造小图或 monkeypatch 尺寸。）
**WPS 验收点**：封面人像主体（脸）不被裁出框、底部 1/3 留标题；split 交界有可见过渡条（非硬切边）；改前后对比。

---

### W7 — 内容数 ≠ 模板槽数（选页优先 + del 余槽默认关，据 ③）★P1

**W7.1 选页评分**：`_best_design_content_page`(:2397) 现 key 含 `n_body<wanted` 惩罚 + `abs` 差，**判定够用，不改**（避免动排序引回归）。仅真机发现选页明显不对再调。

**W7.2 del 余槽**：`_clear_design_body_slots`(:2390) 加 `delete=False` 参数（③ 已给完整代码）。**默认关，主路径不传 delete → 零回归**；能力 + 单测覆盖，真机观察后由调用方按模板类型选择性开启。

**W7.3 clone 槽**：维持缓办（clone 重叠定位在任意模板必翻车 + spid 唯一性）。主路径靠 W7.1 选对页 + `_fill_design_bullets` 末槽吃剩余(:2662，不丢内容)。

**配置/兼容**：`delete` 默认 False，旧行为不破；`getparent().remove()` 仅作用 `info["bodies"]`，绝不碰 decor/背景。
**测试**（`tests/test_ppt_design_del_slots_w7.py`）：`test_delete_removes_shape`（mock 含 bodies 的 info，`delete=True` 后 shape `_element` 已从 parent 移除）；`test_default_clears_text_only`（`delete=False` 调 `_set_text_keep_style`、形状仍在）；`test_keep_protects_shape`（`keep=(shape,)` 不删/不清该 shape）。
**WPS 验收点**：5 条内容进 4 槽页 → 截图确认 5 条全在（末槽合并无丢失）；2 条进 6 槽页（若手动启 delete）→ 余 4 槽无残留空底色块。

---

### W8 — 细节装饰 ★P2

**`_add_footer`(:1777) 加细线进度条**（页码 `_add_text` 后追加）：
```python
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
section 大号序号水印 / 标题装饰线：**列为可选，默认弱**（强度参数），本轮先只落进度条（确定收益、零风险），水印待真机微调。

**配置/兼容**：无 config；进度条置页面最底 0.04in，不与正文（从 1.4in 起）重叠。`_add_footer` 已被所有非 title 页调用(:3326)，自动全覆盖。
**测试**（`tests/test_ppt_footer_progress_w8.py`）：`test_progress_bar_exists`（渲染后断言每页底部存在 `top≈slide_height-0.04in` 的矩形）；`test_progress_width_grows`（page_number 越大矩形 width 越宽）。
**WPS 验收点**：逐页底部有 accent 色进度条且宽度随页号递增；视觉闭环不报新 occlusion。

---

### W9 — 打包 / 兜底说明（日志一次性 + 文档）★P2

**`template_library_root()`(picker:88-91) 加源日志（模块级布尔只 log 一次，防刷屏）**：
```python
_logged_root_once = False

def template_library_root() -> Optional[Path]:
    global _logged_root_once
    ext = _external_root()
    if ext is not None:
        if not _logged_root_once:
            log.info("ppt template lib: external=%s", ext); _logged_root_once = True
        return ext
    bundled = _bundled_fallback_root()
    if bundled is not None:
        if not _logged_root_once:
            log.info("ppt template lib: bundled fallback=%s (external missing)", bundled); _logged_root_once = True
        return bundled
    if not _logged_root_once:
        log.warning("ppt template lib: none (external+bundled both missing)"); _logged_root_once = True
    return None
```

**文档**：STATUS/PPT.md §4 + SKILL.md 补一句「打包 app/新机器仅 3 套 bundled 兜底」。

**配置/兼容**：纯日志 + 文档，无功能改动。
**测试**（`tests/test_ppt_template_root_log_w9.py`）：`test_logs_external_once`（设 `DESKPET_PPT_TEMPLATE_ROOT` 指真库 → caplog 含 `external=`，多次调用只一条）；`test_logs_bundled_when_external_empty`（指空目录 → caplog 含 `bundled fallback`）。注意测试间需 `monkeypatch.setattr(picker,"_logged_root_once",False)` 重置。
**WPS 验收点**：无。

---

## 第 3 部分 · 执行顺序与依赖图

```
                    ┌─────────────── 可并行（无相互依赖）───────────────┐
P0:  W2(stale修复,10min) ─┐
     W6(chart配色)        ─┤  三者互不依赖, 可并行 spawn
     W1a(SKILL重写)       ─┘
                           │
     W1b(outline闭环) ── 依赖 W1a(SKILL 改完再验内容深度), 接线独立可先写代码; 与 W6/W2 无冲突可并行开发
                           │
P1:  W3(密度兜底) ── 与 W1b split 联动验收(W1b 先拆, W3 兜底), 代码独立可并行
     W4(图标符号) ── 完全独立, 可随时并行
     W7.2(del余槽) ── 完全独立(默认关), 可随时并行
                           │
P2:  W5(图片focus) ── 独立
     W8(进度条)    ── 独立
     W9(日志+文档) ── 独立(改 picker, 与 ppt_tools 不冲突)
```

**依赖说明**：
- **唯一硬依赖**：W1a → W1b 的**真机验收**（SKILL 改完才能验"内容深度↑"）；但 W1b 代码可与 W1a 并行编写。
- **联动（非阻塞）**：W1b(split) ↔ W3 —— W1b 先在数据层拆过载页，W3 在渲染层兜底；二者独立实现、联合验收。
- **文件隔离**：W6/W3/W4/W5/W8 改 `ppt_tools.py`（同文件，建议串行 commit 或单 worktree 顺序改，避免 diff 冲突）；W1b 新文件 + 接线；W9 改 `ppt_template_picker.py`（独立文件，可并行）。
- **推荐落地批次**：①批 W2+W6+W9（高性价比、文件多数独立）→ ②批 W1a+W1b（内容主线）→ ③批 W3+W4+W7.2（ppt_tools 串行改）→ ④批 W5+W8（ppt_tools 串行改）。每批跑 `-k ppt` 全绿 + commit。

---

## 第 4 部分 · 功能不丢清单（feature-parity checklist）

逐条对照 00-PLAN 每个功能点 → 在本最终计划的落实位置，证明一个没少。

| 00-PLAN 功能点 | 最终计划落实 | 状态 |
|---|---|---|
| W1a SKILL 重写：内容深度三要素 | §W1a 改动1 | ✅ |
| W1a 好/坏大纲 few-shot | §W1a 改动2 | ✅ |
| W1a 4 套分场景页序模板 | §W1a 改动3 | ✅ |
| W1a 数据型必上 chart + one-message-per-slide | §W1a 三要素 2/3 条 | ✅ |
| W1b `review_outline` 文本评审 | §W1b `review_outline` | ✅ |
| W1b `apply_outline_fixes`（deepen/split/merge/rewrite） | §W1b apply + ② 算法 | ✅ |
| W1b 接线 ppt_create + ≤2 轮 + 开关 + pytest 跳过 | §W1b `_outline_review_loop` + 接线 | ✅ |
| W1b 复用 vision_chat 纯文本调用 | §W1b 一次批量调用 | ✅ |
| W2 决策表改大类名 + 删旧 3 名 | §W2 :27 diff | ✅ |
| W2 :81 参数说明更新 | §W2 :81 diff | ✅ |
| W3 outline split 拆过载页 | §W1b split（W3 引用） | ✅ |
| W3 渲染层每页 bullet 硬上限不丢内容 | §W3 :1224 diff | ✅ |
| W3 留白参数化（呼吸感） | 降级为低优先，默认不改坐标（避回归，第1轮已定） | ✅(降级) |
| W4 `_icon_for_keyword` 关键词→图标 | §W4 映射表 + 函数 | ✅ |
| W4 `_add_icon_badge` 圆底+居中字形+Segoe UI Symbol | §W4 font 参数化 diff | ✅ |
| W4 调用点 badge 改语义符号 | §W4 调用点改法 | ✅ |
| W4 嵌入字体 / SVG→EMF 评估 | 第1轮**否决**（不支持嵌入/重依赖），选 Unicode 字形 | ✅(已决) |
| W5 裁切焦点参数化（封面偏上 1/3） | §W5 `focus` 参数 | ✅ |
| W5 图片边缘软过渡 | §W5 纯色半透明窄条（④ 确认降级，非 gradFill/羽化） | ✅(降级) |
| W5 split 图文交界分隔装饰 | §W5 窄条 | ✅ |
| W6 调色板随主题（series/point 上色） | §W6 (a) | ✅ |
| W6 去 chart junk（关网格/关标题） | §W6 (b) | ✅ |
| W6 data_labels（pie 占比） | §W6 (c) + ① bestFit | ✅ |
| W7 选页按槽数匹配 | §W7.1（判定够用不改） | ✅ |
| W7 del 余槽（连形状删） | §W7.2 + ③（能力做、默认关） | ✅ |
| W7 clone 槽补不足 | §W7.3 缓办（重叠定位+spid 风险，降级，功能不删保留路径） | ✅(降级) |
| W8 页脚进度条 | §W8 `_add_footer` diff | ✅ |
| W8 标题装饰线 / section 序号水印 | §W8 可选默认弱（待真机微调） | ✅(降级) |
| W9 不改打包（2.8GB 不进包） | §W9（结论维持） | ✅ |
| W9 库根解析启动日志 | §W9 一次性 log | ✅ |
| W9 STATUS/SKILL 分发说明 | §W9 文档 | ✅ |

**降级项均为"实现方式降级、功能保留"**（留白/gradFill/clone/水印），无任何功能删除。

---

## 第 5 部分 · 统一验收协议

### 5.1 单测命令
```bash
cd /g/projects/deskpet/backend
# 新增 7 个测试文件
.venv/Scripts/python.exe -m pytest \
  tests/test_ppt_chart_theme_w6.py \
  tests/test_ppt_outline_review_w1b.py \
  tests/test_ppt_bullet_overflow_w3.py \
  tests/test_ppt_icon_keyword_w4.py \
  tests/test_ppt_place_cover_focus_w5.py \
  tests/test_ppt_design_del_slots_w7.py \
  tests/test_ppt_footer_progress_w8.py \
  tests/test_ppt_template_root_log_w9.py -v
# 回归: 全量 ppt
.venv/Scripts/python.exe -m pytest -k ppt -v   # 期望全绿
```

### 5.2 三路径冒烟脚本要点（`scripts/e2e_ppt_smoke_v3.py`，新建）
对**同一主题（深海）**跑三条渲染路径，各落盘一份 .pptx + WPS 渲 PNG，肉眼对比改前后：
1. **设计页路径**：`ppt_create(outline, template="高级色")` → 验 log `vision chose id=...` + design-pages（非 fallback）。
2. **AI 图路径**：含 `image_prompt`/`image_full` 的 outline → 验 gpt-image-2 出图 + `_fullbleed_picture(focus="upper-third")` 封面主体完整。
3. **from-scratch 路径**：无 template、含 chart 页 + 8 条 bullet 页 → 验 chart 配色随主题 + 进度条 + 8 条不丢 + 图标符号。
脚本断言层面只检 .pptx XML（主题色 srgbClr / 进度条矩形 / bullet 全在）；**WPS 渲染观感必须真机肉眼**（不可只信 XML，本项目实锤 WPS 渲染差异）。

### 5.3 真机 windows-mcp E2E（铁律，不可脚本/import/WS 替代）
**同主题改前后对比**（深海 + 新能源各一）：
1. 启 Tauri（注 `DESKPET_BACKEND_DIR=<worktree>/backend` + `DESKPET_PYTHON=<主.venv python>`，确认 log 出 `[backend_launch] Dev python=...`）→ onboarding 登录（凭据见 `LOCAL-DEV-CREDENTIALS.md`）→ 进主界面。
2. 每个 testcase 先 declare `坐标=(x,y) | 动作=click/type | 期望=...`；输入框聚焦用 SendInput（WebView2 不响应老 mouse_event），中文用 Clipboard+Ctrl+V。
3. 对话「做个深海主题的专业 PPT」→ 等产物 → WPS 截图存 `plans/manual-results-2026-06-20/screenshots/`。
4. **逐项肉眼 + log 验收**：①大纲密度↑（bullet 含数字/专名，log `outline_review done`）②chart 配色随主题、无 junk、pie 显 %、无修复提示 ③图标符号真渲染非方框 ④进度条递增 + 封面焦点主体完整 ⑤design-pages 命中非 fallback。
5. 报告格式：`case / 坐标 / 动作 / 截图 / log 证据 / 判定 PASS|FAIL|RETRY-N|SKIP`。失败 retry ≥3 次不同 workaround 才可标"环境受限"。
6. 收尾：`taskkill /F /IM deskpet.exe` + Vite；新文件即 `git add`+commit；同步更新 STATUS/PPT.md §3/§4。

---

## 第 6 部分 · 风险登记（剩余风险 + 缓解）

| ID | 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | WPS 对 pie `dPt`/`show_percentage`/`bestFit` 渲染仍异常 | 低 | pie 标签不显/修复提示 | ① 已用 bestFit（最兼容）+ `_WPS_PIE_DLBL_SAFE` 一键退化开关；真机优先验 pie |
| R2 | W1b split/merge 混合页号错乱 | 中→低 | 内容错页/丢失 | ② 三阶段倒序 + 快照页号 + `_relocate` title 校验 + 同页冲突丢 split + 9 用例测试矩阵 |
| R3 | W1b 增 1 次 LLM 调用拖慢/Clash 掐连接 | 中 | 出 PPT 变慢 | 闭环 ≤2 轮、批量一次调用、`timeout=90`、失败返原 slides；与 visual_review 同量级 |
| R4 | ppt_tools.py 多 W 项同文件并发改 diff 冲突 | 中 | 合并冲突 | 第3部分批次串行改 + 每批 commit；或单 worktree 顺序 |
| R5 | W4 符号在缺 Segoe UI Symbol 机器渲成方框 | 低 | 图标退化 | 全几何/数学符号（覆盖率高）+ 缺字回退编号不破版；真机删字体测一次 |
| R6 | W7.2 del 误删设计页构图 | 低（默认关） | 模板破版 | 默认 `delete=False`，仅作用 `info["bodies"]`，真机观察后才选择性开 |
| R7 | W6 line `format.line.color` 在某些 line 子类不存在 | 低 | 线色不变 | 已套独立 try，失败只退默认线色 |
| R8 | SKILL.md 增量挤占 gpt-5.5 8000 窗口 | 中 | 大纲生成 token 不足 | 总增量 ≤40 行、few-shot/模板表压行 |
| R9 | 真机环境（端口/orphan/双 vite） | 中 | 启动失败 | 不手动起 backend/vite，只给 Tauri 注 env；stop 前 taskkill |

---

## 三轮迭代演进记录

- **v1（00-PLAN）**：确定范围（大纲混合方案 + 6 短板 + stale 修复 + 打包）与 W1–W9 框架，给出函数级改法方向，但行号/API 多处未核实，留 5 大空白给迭代。
- **v2（01→02）**：第 1 轮逐项 Read 核对真实代码，纠 6 处事实错误（`_render_chart_v2`:1651、`ppt_create`:3152、`theme.highlight_rgb`、`_add_icon_badge` 实为 OVAL+文字、clone/del 部分已存在、`_render_bullet_v2` >6 静默丢弃），否决 5 个不可行方案（每 issue 调 LLM/字体嵌入/SVG→EMF/fill_alpha 羽化/clone 重叠定位）；第 2 轮落成可写代码的 diff/签名/issue schema/prompt 全文/映射表/配置键，并明确留 5 点给本轮敲死。
- **v3（本轮，03-FINAL）敲死的 5 点结论**：
  - **① W6 pie**：源码确认 `dPt`+`show_percentage` 结构标准 WPS 支持；**位置由 `OUTSIDE_END` 改 `BEST_FIT`**（饼图最兼容、不触发修复）+ 加 `_WPS_PIE_DLBL_SAFE` 一键退化开关。
  - **② W1b 页号**：**三阶段（deepen/rewrite→merge→split）+ 按 page 降序倒序应用 + 快照页号 + `_relocate` title 校验 + 同页 split/merge 冲突丢 split**，配 9 条测试矩阵。
  - **③ W7.2 del 余槽**：**默认关（`delete=False`）**，只做能力 + 单测，主路径不开启（避免误删设计页构图，残留空槽非正确性问题），真机按模板选择性开。
  - **④ W5 软过渡**：**确认降级为纯色半透明窄条，不上 `a:gradFill`**（高层 API 无 per-stop alpha、WPS 渲染不稳、收益边际）。
  - **⑤ W4 单字兜底**：**删除单字组**（`增/升/扩/强`+≤2字英文），改 ≥2 字中文 / ≥3 字英文词，无命中回退编号，杜绝"强制/特强调"误命中（负价值 > 召回收益）。

---

## 源码复核（本轮 Read 二次确认）

- python-pptx（venv）：`Point.format`(point.py:74-81 → `get_or_add_dPt_for_point`)、`ChartFormat.fill/line`(chtfmt.py:24-40)、`plot.data_labels` 需先 `has_data_labels=True`(plot.py:52-85)、`DataLabels.show_percentage`(datalabel.py:120-122)/`number_format` setter 自动置 is_linked False(datalabel.py:47-50)、`XL_LABEL_POSITION.BEST_FIT/OUTSIDE_END`(enum/chart.py:315/341)、`FillFormat.gradient()` 无 per-stop alpha（fill.py:73/_GradFill:221-265 → 确认 ④ 降级）。
- ppt_tools.py：`_render_chart_v2`:1651（插入点 :1701-1705 实测一致）、`_place_cover` crop :1360-1369、`_clear_design_body_slots`:2390（现签名 `(info,*,keep=())` 无 delete）、`ppt_create` parse :3191 / dry_run :3204。
- 测试基线：`test_ppt_visual_review.py` monkeypatch 风格（`monkeypatch.setattr(ppt_tools,"_in_pytest",lambda:False)` + `parse_outline` + mock vision + `_parse_review_json`），W1b/W6 测试照此落地。
