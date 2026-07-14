# PPT 多构图与 Session 单卡进度验收结果

> 日期：2026-07-11  
> 环境：Windows 11；源码 Tauri；backend 8100；Vite 5173  
> 方法：Windows Computer Use 真实鼠标/键盘 + pytest/vitest/tsc + PPTX 结构检查

## 结论

PASS。PPT Pro 的进度事件不再逐条刷成聊天气泡，同一个 run 始终原位更新一张固定高度进度卡。最终真实生图运行成功交付 6 页整页图片 PPT，视觉审查 6/6 通过，无模板回退、无质量警告。

## 最终真实运行

- Session：`7a378779-c3f6-4392-a762-8bedacce7914`
- Run：`371e34dd90c044c0963c748f7a3ce6c9`
- 用户通过真实 Session 输入发起 6 页“深海探索与未来科技”PPT。
- 大纲阶段只有一张进度卡，显示 `等待确认大纲 / 6/12 / 50%`。
- 真实点击“确认生成”后，大纲卡显示“已确认生成”，run 从 waiting 恢复。
- 最终同一张进度卡显示 `发布 PPT / 12/12 / 100% / 已完成`。
- Session 最终回复了真实附件路径 `deskpet-ppt-1783755078.pptx`。
- 完成时用户视口仍停在原历史位置，没有被后台更新强制拉到底部。

## PPT 产物

- [final-full-page-deck.pptx](./final-full-page-deck.pptx)：8,937,725 bytes，6 页。
- [final-full-page-montage.png](./final-full-page-montage.png)：六页视觉总览。
- [final-structure.json](./final-structure.json)：结构与审查结果。
- 六页均为 `shapes=1`、`pictures=1`，即每页只有一张完整页面图。
- 六页覆盖封面底栏、左文右图、右文左图、底部信息带、场景标题带、居中总结等明显不同构图。
- Durable artifact：`render_mode=full_page_images`，`review_score=1.0`，`quality_warning=null`。

## UI 证据

- [final-session-progress-complete-v2.png](./final-session-progress-complete-v2.png)：通过 Windows UI 的 Alt+PrintScreen、画图粘贴和明确选择 PNG 类型另存为；已校验 PNG 文件头且可正常渲染，画面可见单张完成态进度卡 `12/12 / 100%`。
- 真实点击还覆盖了“大纲修改”语义：点击修改后显示“正在修改 PPT 大纲”，不会误报“正在生成 PPT”。
- 另起短 run 验证滚动边界：用户停在历史中部时，后台从 4/12 更新到 waiting 6/12，视口不跳；随后真实点击取消，未进入生图。

## 本轮真测发现并修复

1. 视觉修订期图片 provider 临时失败时，旧逻辑会静默回退模板却报告成功。显式 `full_page_images` 现禁止模板回退，并对连接错误按未完成页面跨 checkpoint 重试最多 3 次。
2. 视觉模型可能把 Pillow 精确绘制的中文误判为 OCR 错误。修订预算耗尽后保留整页图产物并附质量警告，不再降级模板；本次最终运行 6 页均被判定为 `ok`，未触发警告。
3. Seedream 请求显式关闭 provider watermark；最终 montage 未观察到“AI生成”角标。
4. 补齐 CJK 字体缺失、单字形缺失、中文标点、中英混排和长 token 原文守恒测试；无法安全排版时 fail closed。

## 自动化

- `pytest backend/tests -k "workflow or ppt"`：450 passed，3641 deselected。
- PPT/Graph/Image/Runner 聚焦回归：73 passed。
- TypeScript：PASS。
- `sessionsStore`、WS adapter、progress component：34 passed。

## 交互边界

- 同 run 以 `run_id` 聚合，按 `seq` 水位线忽略重复和乱序事件。
- final 锁定终态，迟到 progress 不能把完成态退回 running。
- 并发 run 各自保留进度卡，互不覆盖。
- waiting、running、completed、failed、cancelled 有独立视觉状态与 ARIA 进度属性。
- 阶段文本单行截断、错误最多两行，卡片高度稳定，更新不会改变消息流布局。
