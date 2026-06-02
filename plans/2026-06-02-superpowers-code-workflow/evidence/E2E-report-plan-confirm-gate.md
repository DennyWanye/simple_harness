# plan-confirm 硬门(item ①)真机 E2E 报告

> **日期**: 2026-06-02
> **被测**: 决策2 — code 模式非平凡任务先出 plan + **等用户点[执行]再跑 ReAct**(硬门)。
> **方法**: CDP 驱动真实 code-panel UI(test-research-helper tile, deepseek-v4-pro)。
> 真 textarea 注入 + 点真「发送」→ 真 plan-confirm 栏渲染 → 点真 [执行]/[取消] 按钮。
> 全栈非注入。证据 = 截图 + backend 日志时序 + 文件系统。

---

## 实现(改动文件)

- **backend**: `config.py` 加 `features.plan_confirm_gate`(默认 False，ship-safe);
  `main.py` 加模块级 `_PLAN_CONFIRM_WAITERS` + plan 块后挂确认门(后台 task await
  Future 不阻塞 WS recv loop)+ `plan_confirm` WS handler。
- **frontend**: `sessionsStore.ts` Message 加 `plan_awaiting_confirm`/`plan_sid` +
  `resolve_plan` action;`ws.ts` chat_v2_plan 带 awaiting + `chat_v2_plan_cancelled` 处理;
  `MessageBubble.tsx` PlanCard 加按钮(全屏视图);**`SessionGridView.tsx` tile 加
  plan-confirm 栏 + [执行]/[取消] 按钮**(grid tile 不走 MessageBubble，单独渲染)。
- **dev config**: `plan_confirm_gate = true`。

### 调试中发现的真问题(已修)
- grid tile 的消息预览(SessionGridView)**用自己的内联 renderer，过滤只留
  user/assistant/error**，把 "plan" 角色丢了 → 最初 plan 卡在 tile 不渲染。
  修复:tile 内单独渲染 awaiting plan 的确认栏(不依赖 MessageBubble)。

---

## TC-GATE-1 · GO 路径(点[执行]→执行)

| 阶段 | 证据 |
|---|---|
| 派明确任务 | "请在项目根目录创建 GATE_OK.md…创建后读回文件确认内容正确" |
| 弹确认栏 | 截图 `plan-gate-go-awaiting.png`:**📋 计划 (4 步) — 确认后执行** + [▶ 执行][取消] |
| **暂停证据** | `05:41:53.016 plan_confirm_gate_awaiting` → 到点击前**无任何 tool dispatch** |
| 点[执行] | `05:41:53.988 plan_confirm_received decision=go` → `plan_confirm_gate_go` |
| **执行才开始** | `05:41:59 todo_write` → `05:42:03 list_directory` → write_file → read_file 读回 |
| 产物 | `GATE_OK.md` 创建成功，内容 "# test-research-helper\n\n一个用于测试…示例项目。" |
| **判定** | ✅ **PASS** — 计划暂停等确认，点[执行]后才跑工具，任务完成 |

## TC-GATE-2 · CANCEL 路径(点[取消]→不执行)

| 阶段 | 证据 |
|---|---|
| 派任务 | "请在项目根目录创建 SHOULD_NOT_EXIST_layer1b.md…然后读回验证" |
| 弹确认栏 | [▶ 执行][取消] 按钮出现 |
| 点[取消] | `05:46:37.601 plan_confirm_gate_awaiting` → `05:46:37.758 plan_confirm_received decision=cancel` → `plan_confirm_gate_cancelled` |
| **零执行证据** | cancel 后**无任何 tool dispatch**;`SHOULD_NOT_EXIST_layer1b.md` **未创建** |
| **判定** | ✅ **PASS** — 取消彻底阻断执行，不碰文件 |

---

## 结论

- **2/2 路径 PASS**,三重证据(截图 + backend 日志时序 + 文件系统)。
- 治好 Layer 1A TC-3 的 gap:明确任务现在**也先出计划等用户确认**(决策2严格版)。
- 后台 task await Future 的最小改动设计成立 — 不阻塞 WS recv loop,无需重构 ReAct 块。
- flag 默认 OFF → 出厂行为字节级不变(auto-confirm);dev 开 ON 验证。

## 已知 / 留待 Layer 1B
- plan 消息是前端临时态(未持久化)→ 面板 rehydration(F5/HMR)会清掉 awaiting plan。
  正常使用无影响(发任务→立即确认);仅影响"发任务后刷新面板再确认"的边角。
- **Layer 1B 计划记忆**将让"同类任务"自动确认(免每次点),与本门耦合。
