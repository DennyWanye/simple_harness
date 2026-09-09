# HM-TO-A6 第 10 次·短旅程 ①（T1–T13，2026-09-09 08:55 起，Host 60ab03a1，Memory 0.6.37，flash，窗口 32000）

证据：`.local-test-evidence/2026-09-09/native-a6-run10a/primary-ui-gd0661ss`。目的：验证 F-E3（分页摘要成本）与事件 W（线上输入预算门）后的预算/分页表现。

| 轮 | 结果 | 备注 |
|---|---|---|
| T1–T5 | COMPLETED | T5 76 s |
| **T6** | **FAILED** `sdk_provider_wire_input_budget_exceeded` | 第 16 次 provider 调用被事件 W 的门拦下：`wire=10731 carry=17143（observed_hidden=14740）floor=27874 > 26752`；工具结果分页已不是主因 |
| T7 | COMPLETED | — |

## 新事件 Y：Run 内 reasoning 回传质量

T6 的 Run 有 15 次带工具调用的助手轮，Host 在出网请求里为每条助手消息回传 DeepSeek 的 `reasoning_content`（`provider.py` ~696 行，来源为消息元数据 `provider_reasoning_content`；持久化的 `request_json` 不含它），15 轮累计约 14.7 K token。这部分不在 Host 的文本估算里、也不可被 F-E2/F-E3 的分页/压桩触及，在 32 K 窗口下一个 15 步的工具循环就把预算耗尽。事件 W 的门把它当作「不可见携带量」推断出来并正确拦截，但真正的修法是：Host 手里有 reasoning 原文，应精确计数；按 DeepSeek 思考模式 + 工具调用的契约决定哪些助手消息必须回传、其余最旧优先丢弃；若契约要求全部回传，则在 reasoning 回传 + 受保护部分超预算时优雅终止（稳定原因码 + 让模型用现有信息作答）而非失败；另评估 flash 关闭 thinking 的参数。→ 已派 Opus 子代理（事件 Y）。
