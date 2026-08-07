# r8 当前阻塞点（2026-08-08 04:15）

## 状态
- 脚本道 **6/6 全绿**（S02/S04/S09/S10/S11/S12），已入账
- 真机道 **0/15**，卡在第一个场景 S01 的冷启动
- `finalize --check-only` = NOT_READY —— **这是预期的**，r8 正在进行中，不是失败

## 阻塞原因：需要用户输入钥匙串密码（AI 不代输）
用全新隔离目录 `.testenv/cold-S01-r8` 冷启动后，macOS 弹出：

> "simple-harness" 想要使用你储存在钥匙串中的 "deskpet-relay" 中的机密信息。
> 若要给予许可，请输入"登录"钥匙串的密码。
> [?] [始终允许] [拒绝] [允许]

该弹窗未获授权即消失（SecurityAgent 窗口数归 0），结果：
`app=1 backend=0 port8100=0 主窗=0`，日志只有
`get_shared_secret failed: No secret available (backend not started?)`，
**`[backend_launch]` 一行都没有** —— relay 密钥没拿到 → backend 没起 → 无窗口。

已清理：app/backend/port/vite 全部归 0；半途的 `.testenv/cold-S01-r8` 已删除
（S01 要求全新冷数据，脏目录会污染"首启居中"断言）。

## 续跑需要什么
1. 用户在弹窗出现时点 **「始终允许」**（不要点「允许」）并输入登录钥匙串密码。
   r8 真机道 15 个场景涉及几十次重启，点「允许」会每次重弹，把整轮拖长。
2. 授权后重跑 `artifacts/launch-cold-s01.sh`，等 `[backend_launch]` 出现、
   `drive.sh geom` 返回有效几何，再按 TC-WB-01 步骤 1-7 取证。

## 未在账本记 BLOCKED 的理由（如实说明）
S01 **没有**记 `blocked` run。这个阻塞是"等用户点一下"的临时性阻塞，不是
"判据前置结构上不可得"。r7 就是因为 S06 记了 blocked 而永久无法 finalize
（`compute_scenario_status` 里 BLOCKED 同样让 required 场景不达标），
不能为了让 Stop 门好看就把临时阻塞写成永久结论。

## 附：r3–r7 出现 FROZEN_ORACLE_CHANGED 是预期的
本轮按 WBUI-BC-04 修改了冻结 testcase
`testcase/workbench-ui/TC-WB-04-chat-roundtrip-companion.md`（"全绿(0 failed)"
→ "与基线等值"）。r3–r7 的 testcase_lock 记的是旧内容且它们的 manifest 里
没有 BC-04，故报 `FROZEN_ORACLE_CHANGED`。**r8 自身不报**（init 在改动之后，
BC-04 已登记）。r3–r7 都是已被取代的轮次（r6 有永久 root FAIL、r7 有 BLOCKED），
待 r8 finalize 后用 `retire --superseded-by r8` 正当退役，该诊断随之消解。
