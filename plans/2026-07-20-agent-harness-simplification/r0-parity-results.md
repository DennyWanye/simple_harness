# R0 产品回合等价性清点

日期：2026-07-20
来源提交：`961c7d340334927acfa07cfaffe071510f56cff3`

## 冻结清单

生产链路中与产品消息、事件、控制和结果投影有关的调用点共 `141` 个，未映射 `0`。清单覆盖：

- 普通文本消息与分片
- 工具调用、工具结果和完成验证
- 只读、Code、DeepResearch、PPT 路由
- Voice 标签和 TTS 文本
- SessionDB / WebSocket 投影
- 审批、取消、AutoResume 和权限恢复
- Subagent / Team-child
- Skill Codify 和产物卡

## 行为观察

R0 使用严格 `xfail` 固定旧实现的反例，同时保持普通回归绿色。`--runxfail` 会把这些反例转成预期失败，用来证明旧生产链路确实违反目标契约。

重点反例包括：

- 安全/不安全工具批次次序错误
- 两个不安全调用并行
- host context 被请求参数覆盖
- Text / Voice 把 `ok:false` 投影为成功
- 跨会话 Subagent 注入
- Code 场景中的 Research / PPT 路由旁路
- `/stop` 未取消持久 Workflow

## 冻结的 TurnInput 表面

`TurnInput`、能力过滤后的工具快照、请求身份和产品路由输入均被测试固定。后续重构必须保持同一产品能力，不得用减少测试面来制造“简化”。

## 结果

- 产品调用点：`141`
- 未映射：`0`
- 正常反例套件：`9 xfailed`
- `--runxfail` 证明：`9 failed`，符合预期
