# Layer 1B 偏好记忆(BGE-M3)真机 E2E 报告

> **日期**: 2026-06-02
> **被测**: 决策1/2 的"第一次问/确认，后续相同的直接做" —— 计划记忆让
> plan-confirm 硬门对**语义相似且以往批准过**的任务自动确认。
> **方法**: CDP 真机(test-research-helper tile, deepseek-v4-pro)+ backend 日志 +
> 文件系统 + 单元测试。真 BGE-M3 embedder(is_mock=False, device=cuda)。

---

## 实现(改动文件)

- **新组件** `backend/deskpet/agent/preference_memory.py`：`PreferenceMemory`
  (计划/意图两类 + BGE-M3 cosine 匹配 + JSON 持久化 + list/clear)。
- **单测** `backend/tests/test_preference_memory.py`：7 例（cosine、相似命中、
  不相似不命中、kind 隔离、去重、持久化重载、清除）→ **7/7 PASS**。
- **main.py 接线**：lifespan 构造 PreferenceMemory(复用 `embedder.embed`)入
  service_context；plan 块挂门前 `match(_text,"plan")` 命中 → 自动确认(跳过等待)；
  plan_confirm `go` → `record(task, "approved", "plan")`。
- **config.py** `features.preference_memory`（默认 False，dev on）。
- **context.py** ServiceContext 加 `preference_memory` 字段 + 入 `_VALID_SERVICES`。

### 调试中踩的坑(已修)
- `service_context["x"]=v` 不支持 → 改 `register("preference_memory", v)`。
- register 有 allowlist → "preference_memory" 不在 `_VALID_SERVICES` 报 ValueError →
  加字段 + 加 allowlist。

---

## TC-1B-1 · 首次任务:走硬门 + 记忆(record)

| 阶段 | 证据 |
|---|---|
| Task A | "请在项目根目录创建 PREF_ALPHA.md…创建后读回确认内容正确" |
| 走硬门(记忆空) | `plan_confirm_gate_awaiting` → 弹 [执行]/[取消](首次无匹配，正确) |
| 点[执行] | `plan_confirm_received decision=go` → `plan_confirm_gate_go` → ReAct 跑 |
| **记入计划记忆** | `preference_memory recorded kind=plan label=approved text='请在项目根目录创建 PREF_ALPHA.md…'`;`preference_memory.json` 1 条 |
| **判定** | ✅ PASS — 首次正常走门 + 用户批准被记下 |

## TC-1B-2 · 相似任务:自动确认(match→auto-confirm)

| 阶段 | 证据 |
|---|---|
| Task B | "请在项目根目录创建 **PREF_BETA**.md…创建后读回确认内容正确"(只改文件名) |
| **语义匹配命中** | `plan_confirm_auto_approved score=**0.936**`（BGE-M3 cosine ≥ 0.86 阈值） |
| **没暂停** | **无 `plan_confirm_gate_awaiting`** —— 没弹按钮、没等确认(autocheck: buttons_appeared=False) |
| **直接执行** | tile 立即 inflight；`todo_write → list_directory → …`;`PREF_BETA.md` 创建成功(155B，内容 "# ResearchFlow…") |
| **判定** | ✅ PASS — 相似任务自动确认，免点[执行]直接跑(决策2 "后续相同直接做") |

---

## 结论

- **TC-1B-1 + TC-1B-2 全 PASS** + 单测 7/7。真 BGE-M3 语义匹配(0.936)驱动自动确认。
- 决策1/2 的"第一次问/确认→记下→后续相同直接做"对**计划记忆**端到端跑通。
- flag 默认 OFF → 出厂不构造 PreferenceMemory，门每次都等确认（字节级不变）。

## 本轮范围 / 留待下一步(诚实标注)
- **已落地**:计划记忆 → plan-confirm 自动确认(决策2，强测)。组件已支持 intent kind。
- **未接线(下一 slice)**:**意图记忆**(决策1 "问模型这类纯提问记成 ask、后续免澄清")
  —— 需要 intent-gate 钩子 + 从一轮 outcome 记录(用没用工具)+ persona hint 注入。
  组件 API 已就绪(kind="intent")，只差接入点。
- **不相似任务不会误命中**:单测覆盖(orthogonal→no match);真机负例(如"帮我重构")
  cosine 远低于 0.86 会正常走门，未单独录屏（阈值 + 单测已证）。
- `/prefs` 查看/清除入口未做(list_entries/clear API 已就绪)。
