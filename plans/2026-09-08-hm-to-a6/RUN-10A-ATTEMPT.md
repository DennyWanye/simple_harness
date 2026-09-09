# HM-TO-A6 第 10 次·短旅程 ①（T1–T13，2026-09-09 08:55 起，Host 60ab03a1，Memory 0.6.37，flash，窗口 32000）

证据：`.local-test-evidence/2026-09-09/native-a6-run10a/primary-ui-gd0661ss`。目的：验证 F-E3（分页摘要成本）与事件 W（线上输入预算门）后的预算/分页表现。

| 轮 | 结果 | 备注 |
|---|---|---|
| T1–T5 | COMPLETED | T5 76 s |
| **T6** | **FAILED** `sdk_provider_wire_input_budget_exceeded` | 第 16 次 provider 调用被事件 W 的门拦下：`wire=10731 carry=17143（observed_hidden=14740）floor=27874 > 26752`；工具结果分页已不是主因 |
| T7 | COMPLETED | — |

## 新事件 Y：Run 内 reasoning 回传质量

T6 的 Run 有 15 次带工具调用的助手轮，Host 在出网请求里为每条助手消息回传 DeepSeek 的 `reasoning_content`（`provider.py` ~696 行，来源为消息元数据 `provider_reasoning_content`；持久化的 `request_json` 不含它），15 轮累计约 14.7 K token。这部分不在 Host 的文本估算里、也不可被 F-E2/F-E3 的分页/压桩触及，在 32 K 窗口下一个 15 步的工具循环就把预算耗尽。事件 W 的门把它当作「不可见携带量」推断出来并正确拦截，但真正的修法是：Host 手里有 reasoning 原文，应精确计数；按 DeepSeek 思考模式 + 工具调用的契约决定哪些助手消息必须回传、其余最旧优先丢弃；若契约要求全部回传，则在 reasoning 回传 + 受保护部分超预算时优雅终止（稳定原因码 + 让模型用现有信息作答）而非失败；另评估 flash 关闭 thinking 的参数。→ 已派 Opus 子代理（事件 Y）。

## 结果（09:35，13 轮跑完，应用已停）

`a6_verify.py`（13/24 轮，A6-1 等按未跑完记 INCONCLUSIVE）：PASS 4 / FAIL 3 / INCONCLUSIVE 11（`RUN-10A-a6-verify.json`）。

| 项 | 判定 | 数字 | 说明 |
|---|---|---|---|
| A6-3 | FAIL（形式上） | 57 次调用中 1 次 usage 26773 > 26752（超 21 token），**0 次超 32000 窗口**；组装侧 `sdk_context_budget_exceeded` **0 次**（第 9 次为 4 次） | F-E3 + 事件 W 生效：超预算的请求不再出门；被拦下的两轮（T6/T8）根因是事件 Y（reasoning 回传质量） |
| A6-2 | FAIL | 分页引用 21、翻页成功 ≥3，ANCHOR 行未在回复中出现 | T11 失败（7 次调用，无 provider 错误码，见下） |
| A6-4 | PASS | 59 次请求无孤立 tool 消息 | — |
| NC-1 | FAIL | T3 改写走 `context_tool` | 第 9/10 次连续出现，flash 行为；记 F-NC1，考虑 PERSONA 对「改写上一句」的 no_recall 提示 |

失败 Run：T6（16 次调用，线上门拦下）、T8（14 次，同）、T11（7 次，原因待查——无 provider 错误码）。
