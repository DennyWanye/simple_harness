# 实现计划 — Layer 1B 偏好记忆 + plan-confirm 硬门 + verify_gate strict

> **日期**: 2026-06-02
> **前置**: Layer 1A 已落地 + 真机 E2E 3/3 PASS([E2E 报告](evidence/E2E-report-layer1a.md))。
> **本计划覆盖**: 用户选定的三项下一步(任务 #2/#3/#4)。
> **取向**: spec-first。先看现有零件,最大化复用,标清需写的新代码 + 决策点。

---

## 0. 现有零件盘点(调研结论 — 又是"大半已存在")

| 能力 | 现状 | 复用度 |
|---|---|---|
| **计划生成** | `agent/plan.py::maybe_extract_plan`(结构化输出 1-8 步)**已实现 + 已接**(main.py:4792-4833 发 `chat_v2_plan` + 注入 system msg) | ⭐⭐⭐ 直接用 |
| **计划确认门** | plan.py:15-17 **自己注释**:"Auto-confirm by default… **gate execution on user click is a future enhancement**" | 需加门(就是决策2) |
| **前端计划展示** | `chat_v2_plan` 在 sessionsStore / ws.ts / MessageBubble.tsx **已处理** | 需加确认按钮 |
| **BGE-M3 embedder** | memory 层 `is_mock=False device=cuda` 真跑;可复用 embed 接口 | ⭐⭐⭐ 直接用 |
| **意图分类** | `assembler/classifier.py` TaskClassifier(rule→embed→llm)+ `code_mode/intent_detector.py`(关键词"想开项目") | 部分复用 |
| **verify_gate** | 已接 + dev shadow 在跑;`claim_patterns.yaml` 只覆盖产物 claim | 需补 patterns / 翻 strict |

**核心洞察**:plan-confirm 硬门 = 接现成的 `maybe_extract_plan` + 加"等确认"(plan.py 自己标的 future enhancement);不是从零造。

---

## 1. 构建顺序(三项有耦合,按此序最省)

```
① plan-confirm 硬门(机制) → ② Layer 1B 偏好记忆(给①②加"记忆免问") → ③ verify strict(独立)
```

**为什么这个序**:决策1/2 的"记下来后续直接做"中,**计划记忆直接喂给 plan-confirm 门**(同类任务自动确认)、**意图记忆喂给 persona 意图门**(同类提问免问)。所以先建门(①),再用记忆(②)让门变智能。③ verify strict 独立,可任意时候插。

---

## 2. ① plan-confirm 硬门(任务 #3)

### 改动点
- **backend** main.py:4792-4833:发 `chat_v2_plan` 后**不再立即跑 ReAct**,改为:
  1. 把 `_plan` + 待执行的 `_msgs`(已注入 plan system msg)+ provider_chain 存进**待确认状态**(per session,内存 dict 或复用 session store)
  2. emit 新事件 `chat_v2_plan_await_confirm`(带 plan)
  3. **return**(本 chat_v2 turn 到此结束,不跑 ReAct)
- **新 WS handler** `plan_confirm`:`{session_id, decision: "go"|"edit"|"cancel"}`
  - `go` → 取出待确认状态 → 跑 ReAct(原 4886+ 逻辑搬过来 / 抽成函数复用)
  - `cancel` → 丢弃,emit 取消
  - `edit`(可选) → 让用户改计划文本再确认
- **frontend** MessageBubble plan 卡片加 **[执行] [取消]** 按钮 → 发 `plan_confirm`

### 触发条件(避免每句都拦)
- 仅 code 模式 + `maybe_extract_plan` 真出了 plan(已有 `_PLAN_MIN_CHARS=40` + in_code_mode 门)
- 短指令 / 提问(persona 意图门判定为非派活)不触发 plan → 自然不拦

### 风险
- ⚠️ ReAct 执行逻辑要从 inline 抽成可复用函数(plan 时存、confirm 时调)——main.py 这段很长,抽取要小心保持现有行为。
- ⚠️ 待确认状态的生命周期(超时清理 / 会话切换 / 重启丢失)——内存 dict 够用(单机桌宠),不持久化。

## 3. ② Layer 1B 偏好记忆 BGE-M3(任务 #2)

### 新组件 `preference_memory.py`
```
PreferenceMemory:
  - record_intent(request_text, intent: "ask"|"task")    # 意图记忆
  - match_intent(request_text) -> intent | None           # BGE-M3 cosine ≥ 阈值
  - record_plan_approval(task_signature, approved: bool)   # 计划记忆
  - match_plan_approval(task_text) -> bool | None
```
- **存储**:`<userdata>/preference_memory.json`(单机,JSON 够;条目 = {embedding, text, label, ts})
- **匹配**:复用 memory 层 BGE-M3 embed → cosine 相似度 ≥ 阈值(初值 0.85,可调)→ 命中返记忆 label
- **写入时机**:
  - 意图:persona 意图门判定 / 用户澄清后 → record(高置信度才写,防误记)
  - 计划:plan-confirm 门用户点[执行] → record_plan_approval(task→approved)
- **读取时机**:
  - 意图门:收到消息先 match_intent → 命中"ask" 则直接答(免问);命中"task" 直接进工作流
  - plan 门:match_plan_approval 命中 → **自动确认**(跳过等待),emit "已按你以往习惯自动执行"

### 风险(决策1/2 已标)
- ⚠️ 误记:把"提问"记成"派活"→ 后续一直错。**只在高置信度写 + 记忆可查看/可清除**(加个 `/prefs` slash 或设置项)。
- ⚠️ 阈值调参:太低乱命中、太高没用。先 0.85,真机调。

## 4. ③ verify_gate shadow→strict(任务 #4)

- **先观察**:dev 已 shadow 在跑。跑几个真实 code 任务,grep `verify_gate` 日志看会拦什么(有无误报)。
- **补 code patterns**:`claim_patterns.yaml` 现只匹配"已生成 X.pptx"。补 code 场景:如"已修改 X.py"/"已创建 X 文件"/"测试通过"→ tool_hint=[edit_file/write_file/run_shell]。
- **翻 strict**:观察无误杀后,出厂 config 也设 strict(或保 shadow 出厂、dev strict)。
- **可选加深**:接 `outcome_verifier`(真跑 build/test)进 end_turn —— "代码改完真跑测试"级硬卡,比 claim 对账更强。评估后定。

---

## 5. 决策点(需你拍板)

1. **plan 确认的交互方式**:计划卡片上**[执行]/[取消]按钮**(直观),还是接受文字"确认/go"(省 UI)?建议按钮。
2. **plan 记忆的"任务类型"粒度**:按整句相似度,还是抽象成类型(如"创建文件类"/"重构类")?建议先整句相似度 MVP。
3. **verify strict 出厂默认**:出厂就 strict,还是出厂 shadow / 仅 dev strict?建议出厂 shadow(稳),dev/高级用户可开 strict。
4. **偏好记忆可清除入口**:加 `/prefs` slash 命令查看/清除,还是设置面板?建议先 slash(轻)。

---

## 6. 下一步
你对 §5 拍板后,我按 ①→②→③ 序实现,每项落地配真机 CDP E2E(同 Layer 1A)。
