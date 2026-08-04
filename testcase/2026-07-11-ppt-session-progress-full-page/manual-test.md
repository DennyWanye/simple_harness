# PPT 大纲语义、Session 进度与整页图片 PPT 手工测试

> 日期：2026-07-11  
> 环境：Windows 11、源码 Tauri、backend 8100、Vite 5173  
> 要求：使用 Computer Use 真鼠标/键盘，不用 WebSocket 注入或脚本回放替代 UI。

## TC-1 修改 PPT 大纲

1. 在新 Session 发起 PPT Pro。
2. 等待大纲卡，填写修改意见并点击“提交修改”。
3. 确认卡片显示“已提交修改，正在生成新版大纲”。
4. 确认会话回复“正在修改 PPT 大纲”，且新大纲卡出现后再次等待确认。

预期：修改不显示成“大纲已确认，正在生成 PPT”；修改期间不进入生图和渲染。

## TC-2 PPT 实时进度

1. 在大纲卡点击“确认生成”。
2. 观察同一 Session 的消息流。

预期：依次显示 1/12 至 12/12 的安全阶段文案，至少覆盖理解需求、调研、大纲、等待确认、生成完整页面、组装、预览、质量检查和发布；不显示 run/checkpoint/span id。

## TC-3 整页图片 PPT

1. 打开生成的 `.pptx`。
2. 抽查所有页面并解析 PPTX 结构。

预期：image-mode 产物每页只有一张 picture，铺满 16:9 slide bounds，无文本 shape；中文准确可读，无裁切、乱码、水印或明显修补块。

## TC-4 DeepResearch 实时进度

1. 新建 Session，发送一个最多一轮的短 DeepResearch 请求。
2. 等待完成并滚动消息流。

预期：同一 Session 显示 1/7 至 7/7，覆盖理解、规划、搜索、综合、引用检查、保存报告、准备交付；历史重载不重复。

## TC-5 复杂代码任务实时进度

1. 打开 Code Mode 并选中仓库项目。
2. 发送明确的 repository test/action 请求，使 `code_complex` 接管。
3. 在消息面板 Session 下拉中选择该代码项目 Session。

预期：代码项目基础 Session 出现在普通 Session 列表；消息流显示 1/9 至 9/9，审批前显示“等待你的操作”，批准后继续执行修改、测试、审计与交付。内部 code memory id 不进入列表。

## TC-6 恢复与去重

1. 在 waiting 状态刷新或重启应用。
2. 重开对应 Session。

预期：历史进度仍在；相同阶段事件不因重连、resume 或 recover 重复刷屏；等待/失败/取消使用明确状态。

## TC-7 短 deck 页数契约

1. 调用 `ppt_pro` 并明确指定 1 页或 2 页。
2. 检查工具参数和最终页数。

预期：schema 接受 1-20 页，Durable Graph 不把显式短页数抬到 3 或默认页数。工具入口与 Graph normalize 自动化均已覆盖；完整 image-mode 点击复测待下一轮执行。

## 判定

TC-1 至 TC-5 为必过；TC-6 由自动化恢复/Outbox/SessionDB 集成测试旁证。任一任务只在 Trace 可见、Session 不可见，或 PPT 页面仍含多个可见 shape，均判 FAIL。
