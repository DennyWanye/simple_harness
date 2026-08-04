# PPT 多构图与 Session 单卡动态进度手工测试

> 日期：2026-07-11  
> 环境：Windows 11、源码 Tauri、backend 8100、Vite 5173  
> 要求：使用 Computer Use 真鼠标/键盘；脚本只能验证结构，不能替代 UI 点击。
> 执行结果：TC-1、TC-2、TC-4、TC-5 已于 2026-07-11 使用 Windows Computer Use 执行；TC-2 额外用独立 run 验证停在历史中部时从 4/12 连续更新到 6/12 不自动拉底。TC-3 的并发 run 隔离由 reducer 自动化覆盖。完整证据见 `plans/manual-results-2026-07-11-ppt-layout-progress-ui/RESULTS.md`。

## TC-1 单卡动态进度

1. 新建 Session，发起一个 PPT Pro 任务。
2. 观察从 accepted、调研、大纲、等待确认到生成页面的变化。
3. 在大纲卡点击“确认生成”。

预期：同一个 run 始终只有一张进度卡；阶段、数字和进度条在原位置更新，不连续新增“PPT进度”文字气泡。waiting 显示等待态，确认后恢复 running。

## TC-2 进度交互边界

1. 在进度运行时向上滚动，停留在较早消息处。
2. 等待至少两次阶段更新。
3. 刷新或重开消息面板，返回同一 Session。

预期：更新不强制拉回底部，卡片高度稳定；重连后仍只有一张卡，阶段不回退。若任务完成，卡片显示 100%/完成并停止动画。

## TC-3 并发与终态

1. 在两个不同 Session 分别运行长任务。
2. 来回切换 Session。
3. 至少让一个任务完成；失败/取消场景可由自动化故障注入旁证。

预期：每个 run 独立一张卡，互不覆盖；完成/失败/取消使用不同状态色，最终回答与 Artifact 仍单独显示。

## TC-4 PPT 多构图

1. 生成不少于 6 页的 image-mode PPT。
2. 打开成品并逐页查看。
3. 对照结构报告和版式清单。

预期：至少出现 5 种构图，相邻页不重复，左右文字页不连续同侧；不再全是左文右图。封面、内容页、重点页、引用/结尾页有明显视觉节奏。

## TC-5 文字与结构安全

1. 抽查长中文、中英混排、来源文字和最后一页。
2. 解析 PPTX shapes。

预期：中文无乱码、裁切、静默省略或文字越界；每页仍只有 1 个铺满 slide bounds 的 picture，0 个文本 shape；DeskPet 合成层不制造黑块或遮挡正文。第三方生图服务自带标识单独记录为 provider 边界。

## 判定

- TC-1、TC-2、TC-4、TC-5 已真实点击通过；TC-4/5 的成品结构与 montage 由真实运行生成的 first-pass full-page deck 提取验证。
- reducer 乱序、final-before-progress、epoch fence、字体缺失和 typed failure 由自动化故障测试覆盖。
- 任一 run 出现多张阶段气泡，或 6 页以上 deck 少于 5 类构图，均判 FAIL。
