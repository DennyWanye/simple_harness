# HM-TO-A6 第 10 次·短旅程 ②③④（T1/2/4/7/15/17/19/20/21/22，2026-09-09 09:45 起，Host edd12245 源码 + bundle 60ab03a1，Memory 0.6.37，flash，窗口 32000）

目的：关系形态（事件 T + F-S1b，看 T15 `applies_to`）、目标逐字记录（事件 U，看 T17 goal.set 与 README bounded）、争议通知（事件 V + 0.6.37，看 T21 争议组与 T22 要求确认）。

证据：`.local-test-evidence/2026-09-09/native-a6-run10b/primary-ui-*`。

## 首次启动失败（09:30）

MM-D1/D2 合入（`08881887`）在 `main.py` 注册了 `memory_display_invalidation` 服务，但未加入 `backend/context.py::_VALID_SERVICES`，后端 `Application startup failed`，T1 发送失败。修复 `edd12245`（`test_context.py` 28 项通过）。教训：改 `main.py` 服务注册的交付必须跑一次后端启动冒烟（子代理未启动应用时也要 `python -c` 级别导入/注册检查）→ 记为 F-MMD-1（补一个「main.py 注册名 ⊆ _VALID_SERVICES」的测试）。

## 进展（10:05）

| 轮 | 结果 | 观察 |
|---|---|---|
| T1/T2/T4/T7 | COMPLETED | — |
| **T15** | COMPLETED，分析批（`host-analysis-prompt/v9`）落地后 **`cognitive_relations` +1：`applies_to` / knowledge，`relation_memory_id` 非空，新建 procedure 节点 1 个** | **A6-6 关系形态首次在原生跑通**（事件 T 的 v9 策略 + 0.6.35/0.6.36/F-S1b） |
| T17 | FAILED | `context_route` 成功 → `task_scope_update` 被 `refs_outside_scope` 拒绝（事件 U 的披露回执已带可引用 id）→ 第 3 次 provider 调用被线上门拦下：`wire=21625 carry=12846 floor=34471 > 26752`。18 KB 目标原文 + 模型把它回显进工具参数（7.6 KB）+ 两轮 reasoning 回传，在 32 K 窗口下本身就超预算 → 归事件 Y |
