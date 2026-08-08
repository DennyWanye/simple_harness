
---

## 2026-08-08 18:30 本地 · 新增硬阻塞：中转站账户余额耗尽（LLM 全断）

- 症状：聊天发起任何 run 立即失败，错误为
  `LLM HTTP 429 Too Many Requests … "is suspended due to insufficient
  balance, please recharge your account" (exceeded_current_quota_error)`
  （org/key 标识已略；s02-rerun.log 内 429/insufficient balance 共 15 处）。
- 影响：账户级 suspend，换模型无效。剩余场景全部依赖至少一次真实 LLM 往返：
  S06 的 ④cancel/⑤rollback 入口需再造 update 操作（capability_update 走 LLM）、
  S07 步骤5 产物卡片、S08 步骤7 provider 往返、S13 步骤5、S15/S18 的
  run_id_under_test——全部被卡。
- 已达成（阻塞前）：S02/S03/S04/S05 重跑 PASS 已记账；S06 已在真机拿到
  ①两 tab ②列表 ③详情 ⑥**卸载入口**（succeeded user-scope 卡片，
  r8-S06-rr-13-refetch）+ failed 卡片**重试入口**（r8-S06-rr-10-failedcard）
  ——操作卡 available_actions 渲染链路真机已证；cancel/rollback 是同一渲染
  循环（CapabilityOperationCard.tsx:237）的不同枚举值，后端投影单测
  test_capability_center_cancel_targets_only_its_owned_task 断言 running→
  ["cancel"]；唯"真机可见"未达成，因两个状态在当前环境不可达
  （操作 ~2s 内完成无 running 窗口；kind=update 只能由 LLM 工具触发）。
- 解法（需用户择一）：
  A. 给中转站账户充值 → 我继续用 capability_update 造 update 操作，
     补齐 ④⑤ 真机入口截图，随后跑完 S07/S08/S13/S15/S18。
  B. 用户自行在 设置→LLM Providers 添加一个可用 provider（我不代输 key）
     → 同上继续。
  C. 用户裁决 S06 以"⑥真机 + ④⑤组合证据（渲染链路真机已证 + 后端单测）"
     计 PASS——但 S07/S08/S13/S15/S18 仍需 LLM，C 单独不够收尾 r8。
