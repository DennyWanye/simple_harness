# HM-TO-A6 尝试 7（2026-09-09 03:15，Host f161f5a4，Memory 0.6.31，deepseek-v4-flash，窗口 32000）— 第 6 轮停摆

- T1–T5 全部 COMPLETED；T5 的 9 次调用在预算内完成（flash 校准与有序降级生效：回执 `pages_forced` 至 11、`groups_trimmed_for_budget` 1、`budget_headroom` 最低 81，未再出现 `sdk_context_budget_exceeded`）。
- T6 执行中前台驱动连续 4 次 `RuntimeError(primary_message_scope_source_mismatch)` → `foreground.runtime.stalled`，Run 头停在 RUNNING，provider 调用停止（03:20:58）。
- 根因待定：`primary_message_v3` 的终态消息 Scope 来源校验（约 20 个等式合并成一个不透明码）与今晚合入的强制分页 / F-K1 侧记录 / 路由状态 / 工具参数备忘录之一发生生产者-校验者漂移。事件 R，子代理离线复现并修复中；同时把不透明码拆成逐条件稳定码。
- 处理：中止本次；修复合入后做第 8 次。
