# 原生 r16：登记恢复成功，直接答复被 pending 提醒拦截

更新：2026-09-06。固定Host `eaa210b39e5a545824951819fcfc1885d52263f0`，H078/M618/S0313，合入已独审时间运行链acf3e8a6（四项新增控制4PASS/7.15s），未重复旧绿色测试。沿用原生二进制、r14原userdata，未修改数据库或重新造提醒。

**端到端结果：FAIL。**

- 启动后13:23:32UTC实际日志 `prospective_runtime_time_applied count=1`，表明r14没有登记/计时的生产缺口已推进到实际触发。
- 真CUA发送“28加15等于多少？”，不在问题中提示提醒。13:24:17UTC实际SDK失败：`NoRecallBlockedError: sdk_no_recall_blocked_pending_occurrence: 1 pending occurrence(s)`。界面恢复空闲，只留下用户问题，没有本轮回答或提醒。
- Run `1c08275f-fba6-5962-9644-dbb9fc5a9f10`。只读Host有occurrence阶段记录claimed/presented各1，但mandatory-exit投影`occurrence_presented`仍0。这不是用户看过或ACK；记录数不等提醒数，不能删pending或自动ACK来绕过no_recall检查。实际前台展示/ACK路径继续修复。

正常Cmd+Q退出后PG17276清空：exit0、98.951秒、峰1,323,760KiB、remaining=[]、cleanup_error=null；最低磁盘2,703MiB。r14/r16失败保留，不归因于锁屏或内存阻挡。本轮并未覆盖新建提醒修复后的前台措辞、系统通知、关闭应用唤醒或完整长旅程。

本机ignored证据与SHA-256：

- `.local-test-evidence/2026-09-06/native078618/primary-ui-rn7ycdwy/launch.json`：`19ca54e1fffddccb639afe5b0216f5fd3c7dd0f150a88510b153593e440d0980`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-rn7ycdwy/01-reopened.png`：`a5e83f0ee78bd3fceb439341226501c59681e67f58d83885ae9097a38db6b223`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-rn7ycdwy/01-reopened.ax.txt`：`b2afd5cb7188e648fb0a136f859a4b0f2c91ff460250522a56c904939f2276b5`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-rn7ycdwy/02-pending-blocked.png`：`b780be3cc7ca0a0894d8f3ebd8d6d14cfe86a64626fb6852331dc9fb4fc41f2e`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-rn7ycdwy/02-pending-blocked.ax.txt`：`48168c00e051dda2796982981dc830fc8f73242c37d6f962a85c399c53cd6a29`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-rn7ycdwy/native.log`：`80af14c8a30ca571261932bf0f62307aae9e088350a491e60d4d9a98a00f9e2a`
- `.local-test-evidence/2026-09-06/native078618/primary-ui-rn7ycdwy/reminder-observation.json`：`06f08b9f1f9baefb94761b4e028f6c58466ba77bda1574d87cb4ebd070c59ec8`
- `.local-test-evidence/2026-09-06/native078618/r16-reminder/resource.json`：`da6d384358a47e904541de2554c9f90052f27a4bd056bf43bad48ea72c7a707f`
