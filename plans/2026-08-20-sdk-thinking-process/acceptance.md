# Slice 验收：SDK 公开思考过程与终态自动折叠

事实源：仓库根 `acceptance.md` 的“2026-08-20 增量验收：运行中思考区与完成后折叠”。
本 slice 不修改或替代既有 AC-1～AC-7，只交付以下两条新增 MUST AC。

| ID | 功能点 | 验收条件 | 优先级 |
|---|---|---|---|
| AC-8 | SDK 公开思考投影 | SDK tool-turn 的公开 assistant 工作叙述生成 durable reasoning summary，`context_visibility=exclude`；隐藏 `reasoning_content`/CoT 不进入公开正文或模型后续上下文 | 必须 |
| AC-9 | 思考区展开/自动折叠 | 真实任务运行时默认展开；Run 终态自动折叠并显示耗时；用户可点击展开/再收起；历史终态默认折叠 | 必须 |

## 适用性

- `input_sensitive=false`：确定性 UI/投影行为，不按回答语义质量验收。
- `llm_payload_driven=true`：公开 assistant/tool 事件驱动 UI，覆盖重复、乱序、缺字段、超长、无叙述。
- `stateful_init=true`：SessionDB hydration 必须恢复终态默认折叠。

## Required obligations

- TO-A8：SDK tool run 产生 exclude-context durable public reasoning summary。
- TO-A9：running 展开、terminal 自动折叠、点击 toggle 与历史恢复组件测试。
- TO-R4：出站上下文不含 reasoning summary/reasoning_content。
- TO-R5：重复/乱序/缺时间不产生重复、负耗时或终态复活。
- TO-R6：Computer Use 真实验证运行展开、完成折叠、点击再展开。
