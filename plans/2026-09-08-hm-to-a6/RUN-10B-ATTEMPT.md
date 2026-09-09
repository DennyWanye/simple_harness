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

## 结果（10:20，10 轮跑完，应用已停）

`a6_verify.py`（`RUN-10B-a6-verify.json`，部分轮次按未跑记 INCONCLUSIVE）：**A6-6 PASS、A6-7 PASS、A6-8 PASS、NC-4 PASS**——四项首次在原生通过：

| 项 | 数字 |
|---|---|
| A6-6 | 关系 3 条，其中 `applies_to` 知识边 1 条，target 为本 plan 新建 procedure 节点 |
| A6-7 | 1 条记忆 revision 1→2，amends 边 |
| A6-8 / NC-4 | 争议组 1，T22 两次调用，回复要求用户确认；`context_route_tool_invocations` 记录 `recall_conflict.conflict_status=contested` |
| A6-5 | INCONCLUSIVE：T17 被线上门拦下（事件 Y） |

失败 Run：T17、T21（均为线上门；事件 Y 已合入 main）。

## 事件 Y 结论（合入 main）

DeepSeek 实测：回传的 `reasoning_content` 按普通输入计费（≈772 token/块）；**省略旧的回传不报错、不降质**（三种回传策略均 6 轮完成同一工具循环）；`thinking={"type":"disabled"}` 真实生效（prompt 4580 → 591）。实现：精确计数 + 预算压力下最旧优先丢弃 Run 内回传（`reasoning_relay_dropped*` 回执）+ `model_overrides.toml` 的 `reasoning_mode`。**第 10 次整跑决定**：保持 32 K 窗口并在 `model_overrides.toml` 加 `reasoning_mode = "fast"`，把回传变量拿掉，让整跑专测分页/裁剪/压桩；回传路径由 17 项单测 + 回执覆盖。
