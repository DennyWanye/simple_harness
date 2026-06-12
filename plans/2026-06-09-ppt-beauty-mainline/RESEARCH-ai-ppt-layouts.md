# 调研报告：AI 配图 PPT 的版式多样性 + 构图最佳实践

*2026-06-12 | 来源 6 个 | 置信度：中高*

## 摘要

专业 AI-PPT 工具（Gamma / Beautiful.ai / Slidesgo）的共识：①**一套 deck 只用 3~5 种版式**（多了反而乱），但每种内容配**最贴的版式**，不是一招打天下；②**AI 图绝不嵌文字**（模型生成的字必乱码）——文字永远是 python-pptx 叠在图上的真文本层；③全幅图上的文字靠**半透明深色矩形/渐变暗带**保证可读；④AI 图靠 prompt **主动留负空间**（指定哪一侧/区域空出来给文字）。

deskpet 现状问题正中下怀：image_full 只有「左面板+右图」**一种**版式，且每页同款 → 单调。

---

## 1. 版式分类（综合 Deckary 9 结构 + Gamma + 2026 趋势）

按「AI 配图」适配度，给 deskpet 落地的 **6 种 image-mode 版式**：

| 版式 | 适合内容 | AI 图怎么用 | 文字位置 |
|---|---|---|---|
| **A 全幅封面/转场** (full-bleed) | 封面、章节转场、金句 | 图铺满整页 | 底部暗带 or 居中暗盒,大标题+1 句 |
| **B 左图右文 / 右图左文** (split 50-55%) | 论点+配图、产品/场景说明 | 图占一侧半幅 | 另一侧标题+3~4 要点(交替左右防呆板) |
| **C 上图下文** (top image band) | 概念引入、流程开篇 | 图占上 55~60% | 下方标题+横排要点 |
| **D 全幅背景+卡片** (full-bleed + text card) | 数据/要点 over 氛围图 | 图铺满 | 一个半透明深色卡片(局部),内放标题+要点 |
| **E 三图网格/拼贴** (bento grid) | 三个并列点、组图展示 | 3 张小 AI 图横排 | 每图下小标题+一句 |
| **F 大图引用** (quote over image) | 金句、愿景、结论 | 全幅图(暗) | 居中超大引号+短句 |

**仲裁规则**(关键,治"单调"):
- 一份 deck 在 A~F 里**轮换** 3~5 种,**相邻页不同版式**;
- B 左右**交替**(奇数页图在右、偶数页图在左);
- 封面→A;章节→A/F;内容页→B/C/D 轮换;并列三点→E;结论→F。

> 来源: [Deckary 9 结构](https://deckary.com/blog/powerpoint-layout-ideas)("限 3~5 种版式/套"、全幅图"半透明深色矩形"叠字)、[Gamma 版式指南](https://gamma.app/explore/content/guides/gamma-ai-presentation-tool-flexible-layout-customization-guide)(按内容自动选版式如时间线)、[Slidesgo 2026 趋势](https://deckary.com/blog/powerpoint-layout-ideas)(Bento grid 爆发)。

---

## 2. AI 配图 prompt / 构图技巧

1. **留负空间给文字**(治"图盖住字"): prompt 明确指定空出区域 —— 按版式定:
   - 全幅底部暗带: `...bottom third darker and uncluttered for text overlay`
   - 左图右文(图在右): `...main subject on the right, left third clean and simple negative space`
   - 来源: [DALL-E 负空间](https://community.openai.com/t/dall-e-prompts-for-negative-space)、[Krumzi](https://www.krumzi.com/blog/how-to-add-text-to-ai-images)("leave negative space on the left for headline placement")。
2. **白字可读**: prompt 让留白区**偏暗**(`dark, low-contrast area`)+ python-pptx 再叠**半透明深色矩形/渐变**兜底(Deckary 明确推荐)。
3. **整套风格统一**(治"每页画风不一"): 所有 prompt 共享一段**固定风格后缀**(色调+媒介+光感+情绪),只换主体场景。deskpet 已有 STYLE 常量,继续用。
4. **图里绝不要字**: 每个 prompt 都加 `no text, no letters, no typography, no watermark`(模型生成的字必乱码,这是铁律)。文字**永远**是 python-pptx 叠的真文本。
5. **图像尺寸贴版式**: 全幅/上图用横版 `1536x1024`;左右图用接近该侧比例的图再裁切。

---

## 3. 落地到 deskpet(python-pptx)的实现清单

**新增渲染器**(在 ppt_tools.py,对应版式 A~F):
- `image_full`(已有,= A/D 雏形)→ 拆成 A(纯封面暗带)和 D(背景+卡片)
- 新增 `image_split`(B,带 `image_side: left|right`)
- 新增 `image_top`(C)
- 新增 `image_grid`(E,3 图)
- 新增 `image_quote`(F)

**版式仲裁器** `_assign_image_layouts(slides)`: 给一组 image 页**自动分配/轮换**版式(封面 A、内容 B/C/D 轮、三点 E、结论 F),相邻不重复、B 左右交替 —— LLM 只写内容+image_prompt,版式由代码智能分配(也允许 LLM 显式指定)。

**prompt 自动补留白**: `_autofill_image_prompts` 按每页分到的版式,**自动给 image_prompt 追加对应的负空间指令**(全幅→底部暗;左图→右主体左留白;等),LLM 不用操心构图。

**风格统一**: 整 deck 共享一个 STYLE 后缀(已有)。

---

## 4. 第二块:视觉评估闭环(问题 1)

用户要的「桌宠看每页渲染图→评估→改」需要**多模态工具结果**(把预览 PNG 喂回 LLM)。当前 backend 工具结果是纯文本 `role=tool`,不支持图片回传 —— 这是**独立的多模态改造**,与本版式优化解耦。本轮先做版式多样化(规则驱动,确定性、零额外 LLM 成本);视觉闭环作为下一立项(需改 tool_use_shim 支持 image content block + agent loop 回传 vision)。

---

## 关键 takeaway
1. **不是一种版式打天下** —— 6 种版式按内容轮换,相邻页不同,是"惊艳且不单调"的核心。
2. **AI 图主动留负空间 + python-pptx 叠真文本** —— 治穿帮/盖字的根本。
3. **整套统一风格后缀 + 图里禁字** —— 治画风乱/乱码。
4. 视觉评估闭环是另一条线(多模态),本轮先做确定性的版式多样化。

## 来源
1. [Deckary - 9 PowerPoint Layout Structures](https://deckary.com/blog/powerpoint-layout-ideas)
2. [Gamma - Layout Customization Guide](https://gamma.app/explore/content/guides/gamma-ai-presentation-tool-flexible-layout-customization-guide)
3. [Krumzi - Add Text to AI Images](https://www.krumzi.com/blog/how-to-add-text-to-ai-images)
4. [OpenAI 社区 - DALL-E 负空间 prompt](https://community.openai.com/t/dall-e-prompts-for-negative-space)
5. [Zapier - Best AI Presentation Makers 2026](https://zapier.com/blog/best-ai-presentation-maker/)
6. [Venngage - Presentation Layout Ideas](https://venngage.com/blog/presentation-layout-ideas/)
