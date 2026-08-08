# r8 当前阻塞与续跑状态（2026-08-08 15:3x +0800 刷新，HEAD=fea8c11）

## 唯一硬阻塞（需用户，AI 不代办）——16:0x 实测后根因修正
relay 身份拒绝循环的**真实杀伤面**已实测确认（probe-send.log + r8-S07-card-*.png）：
- LLM 链路本身**可用**：探针消息真实往返成功（「收到」）；excel_create 真产出
  OutPut/Excel/excel_create-default-excel_create_0.xlsx
- 但 `companion_profile_bind` 拒绝循环（window_credential_required rechallenge /
  trusted_relay_identity_unavailable）导致**控制 WS 连接约每 30 秒被踢**：
  ① 发送窗口间歇失效（「消息发送失败：控制通道未连接」实测复现）
  ② 长任务结果帧丢失（excel 工具结果/产物卡片永未到达前端，后端报
     Cannot call "send" once a close message has been sent — RuntimeError 横幅）
  ③ 输入框启动期「正在恢复身份…」几秒后降级放行（有迷惑性，能打字但通道不稳）
⇒ 所有聊天依赖场景（S03/S04/S05/S07/S08/S13/S18、S02/S15 重跑、S06 尾项）在此
环境下**无法产出未污染的判定证据**（r7 即死于此类污染），维持等待。
**恢复方式：用户在钥匙串弹窗点「始终允许」+输密码，或重新登录 relay。**

## 两个缺陷已修复并提交（等真机重跑闭环）
- S07 契约漂移 → aa8ddb1（presenter 泄漏 NormalizedToolOutcome 包装；已单测）
- S03 布局溢出 → 05f72ec（隐式 grid 轨道 min-content 下限；浏览器道复测 0 溢出）
  ⚠️ 浏览器道仅诊断证据，不作场景判定（本项目是 Tauri 桌面应用）。

## 已闭合场景（16:40 状态，全部晚于 15:58:42 attestation 的重跑 PASS）
S01（冷启七步）/ S09 / S10（11步全套）/ S11 / S12（基线等值）/ S14 / S16 / S17
——8 场景 record-run PASS 且从 check-only 诊断中清零。
注意教训：re-attest 必须在改码后、跑场景前执行（详见全局知识库
plan-test-reattest-ordering.md）；本轮因次序失误付了 8 场景重跑的代价，已还清。

## 剩余 10 场景 = r8 全部剩余诊断，全部硬依赖聊天链路
S02（步骤3发消息+devtools探针）/ S03（步骤6后半）/ S04 / S05 / S06（判定项④⑤⑥
需 capability-pack 造 op）/ S07（步骤5 消息流产物卡片）/ S08（步骤7 provider 往返）
/ S13（步骤5 真实往返）/ S15 / S18。
16:1x 实测已确认：LLM 链路可用（探针往返成功、excel 真产出文件），但身份拒绝
循环每 ~30s 踢 WS ⇒ 结果帧丢失（产物卡片帧实测丢失 + Cannot call send once
close 横幅）、发送窗口间歇失效 ⇒ 无法产出未污染判定。等 relay 恢复后一次跑完。

## 环境/驱动备忘（新增坑）
- cliclick 拖拽移动窗口 OK；角落 resize 拖拽不生效（两次实测）——S10 步骤1 硬需
  拖拽缩放，先排障再跑
- 旧 SkillStore「安装」走 legacy 技能管线，不产生 capability operation——别再用它
  给 S06 造数
- 托盘驱动：`click menu bar item 1 of menu bar 2` + `menu item "<名>" of menu 1
  of menu bar item 1 of menu bar 2`，需先 click 展开再取 menu item
- 几何锚点断言可用 drive.sh geom 四元组直接对比（S16 实证 ±0 恢复）

## 记账纪律提醒（不变）
不预写 PASS/FAIL、不记 BLOCKED 进账本（永久粘性）；NOT_READY 是如实中间态。
