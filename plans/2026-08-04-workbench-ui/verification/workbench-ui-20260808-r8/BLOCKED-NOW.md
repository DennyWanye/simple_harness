# r8 当前阻塞与续跑状态（2026-08-08 15:3x +0800 刷新，HEAD=fea8c11）

## 唯一硬阻塞（需用户，AI 不代办）
relay 身份失效仍未恢复：backend 持续 `companion_profile_bind` 拒绝循环
（`window_credential_required` rechallenge / `trusted_relay_identity_unavailable`，
最后确认 07:06Z）。前端输入框「正在恢复身份…」禁用，控制 WS 徽章间歇「未连接」。
**恢复方式：用户在钥匙串弹窗点「始终允许」+输密码，或重新登录 relay。**

## 两个缺陷已修复并提交（等真机重跑闭环）
- S07 契约漂移 → aa8ddb1（presenter 泄漏 NormalizedToolOutcome 包装；已单测）
- S03 布局溢出 → 05f72ec（隐式 grid 轨道 min-content 下限；浏览器道复测 0 溢出）
  ⚠️ 浏览器道仅诊断证据，不作场景判定（本项目是 Tauri 桌面应用）。

## 本段新增真机结论（fea8c11 已落账）
- S14 托盘三项：root run PASS（负向断言含 execution_runs 零新增）
- S16 退出路径矩阵：root run PASS（三路径全清退出+几何锚点恢复一致+隐藏反证）
- S06：步骤1/2/3/4/6 真机全过（证据 r8-S06-manual.txt），判定项④⑤⑥被数据态卡住：
  操作 tab 三入口 = 后端 available_actions 投影（cancel 仅 running；rollback/uninstall
  仅带 pack 绑定 succeeded）；现 17 op 无绑定，legacy SkillStore 安装不产生 op。
  闭合路径：待 relay 恢复后经 capability-pack 安装/生成链路造一个可操作 op。
  未记任何 S06 结论。

## 待跑清单（按依赖分组）
- 不依赖 relay、下一段可直接跑：S10（需先解决 cliclick 角落拖拽不触发 resize 的
  问题——本机 WKWebView 窗口边缘拖拽两次未生效，考虑先 m: 悬停让 resize 光标出现
  再 dd，或用底边/右边中点）、S17（重 fixture：30 会话+120 字符标题+30 产物）、
  S01/S02/S15 重跑（TESTED_RUNTIME_MISMATCH 作废；注意 S02 步骤3 与 S15 需发消息
  ⇒ 其实也卡 relay）
- 依赖 relay：S03（全量重跑）、S04、S05、S07、S08（步骤7 provider 往返）、
  S13（步骤5 真实往返）、S18、S06 判定项④⑤⑥
- 其余：S09/S11/S12 脚本道已绿但需按 impact 复核是否重跑

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
