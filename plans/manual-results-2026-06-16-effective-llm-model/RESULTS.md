# P-B 有效模型解析 — windows-mcp 真测结果（2026-06-16）

> 环境：config 种子 `[llm] model=gemma4:e4b`（stale 条件成立）+ runtime `llm_runtime.json model=gpt-5.5`
> + `compaction_enabled=true` + model_overrides gpt-5.5=1M。跑当前 checkout（commit `84e4c25`）。

## 结果汇总：核心 + UI 全 PASS，无 bug

| TC | 被测点 | 类型 | 判定 | 证据 |
|---|---|---|---|---|
| **TC-01** | ★招牌：种子 gemma + runtime gpt-5.5 → 压缩窗口按 gpt-5.5 | 日志 | ✅ PASS | `wi4_0_compaction_enabled context_window=1000000` + `model_context_resolved model=gpt-5.5 window=1000000`（**非 32000/gemma**）|
| **TC-02** | 反例对照：种子 gemma 但解析 gpt-5.5（证非巧合） | 日志 | ✅ PASS | `llm_runtime_overrides_applied model=gpt-5.5` + 上行；config 种子确为 gemma4:e4b |
| **TC-03** | UI：context_usage 环按有效模型大窗口算 | windows-mcp | ✅ PASS | 真机点开消息大框，上下文环显 **1%**（按 1M 算才低；若 gemma 32K 会偏满）|
| **TC-04** | UI：模型按钮 + 模型与参数弹框显模型-上下文窗口 K/M | windows-mcp | ✅ PASS | 真机选 gpt-5.5 → 按钮 **"gpt-5.5-1M"**、弹框上下文窗口 **"1M"**（纯 K/M,无 raw tokens）|
| **TC-10** | 回归：聊天/出站正常不破坏 | windows-mcp | ✅ PASS | 真机发消息 → `POST chinzy.com/v1/chat/completions 200 OK`（=gpt-5.5）→ content_chars=112 end_turn,无报错 |
| TC-05~08 | 4 边界（无 runtime/缺 model/损坏 JSON/改档） | 单测覆盖 | ✅ | `test_effective_llm_model.py` 8 分支全过（effective 4 + standalone 4） |
| TC-09 | PPT standalone（改 runtime 验跟随） | 单测+间接 | 🟡 | standalone 4 分支单测覆盖；真机 live 解析 standalone→gpt-5.5 已验 |

## 关键证据链（P-B 根治实锤）
```
config 种子: [llm] model = "gemma4:e4b"          ← stale 条件
llm_runtime_overrides_applied ... model='gpt-5.5'  ← onboarding 覆盖
model_context_resolved model=gpt-5.5 window=1000000 source=global  ← 读到有效模型
wi4_0_compaction_enabled context_window=1000000    ← 压缩窗口=gpt-5.5 的 1M(非 gemma 32K)
```
修复前：会是 `context_window=32000`（gemma `_default`）→ 过早压缩浪费大窗口。

## 结论
P-B 根治验证通过：种子 gemma 但所有读模型的链路（压缩窗口/stub→前端环/模型显示）统一读到有效出站
模型 gpt-5.5 及其 1M 窗口。windows-mcp 真测 4 条 UI PASS，无新 bug，无需修复复测。
