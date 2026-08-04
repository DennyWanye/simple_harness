# TC-WB-08 — SettingsView 设置页面化与逐项保留

> 对应 AC：WB-8 ｜ 行为契约：B6、B12（自启开关入设置页）
> manual_required: true
> 权威快照（裁决第 2 条，`plans/2026-08-04-workbench-ui/oracle-clarifications.md`）：以改版前 SettingsPanel 分区为准，核对表见步骤 2；「桌宠形象」为**唯一**预期消失项。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 点击侧栏底部「⚙️设置」。 | 设置以**页面**呈现于内容区：无 backdrop 遮罩、无浮层定位。 |
| 2 | 按下方分区核对表逐项打勾：<br>① Provider/模型配置区（SettingsProviders）<br>② Embedder 状态卡<br>③ 模型上下文卡<br>④ 数据目录设置<br>⑤ Agent 有效执行预算<br>⑥ 云端 API key 管理<br>⑦ 自启开关（改版后新迁入）<br>⑧ 「桌宠形象」区块 | ①–⑦ **逐项存在**（缺任一项即 FAIL）；⑧「桌宠形象」区块/下拉**不存在**（唯一预期消失项，出现即 FAIL）。 |
| 3 | 核对自启开关（核对表第⑦项）可实际切换。 | 「开机自启」开关在设置页内存在且可切换（B12：从 Toolbar 移入）。 |
| 4 | **canonical 保存生效项（写死为自启开关）**：切换「开机自启」开关并保存。 | 保存无报错；系统侧核对：`ls ~/Library/LaunchAgents` 出现对应 plist **或** 系统设置·通用·登录项出现本应用条目——**以改版前既有自启机制为准，实测观察面（plist 或登录项）原样入账**；两处均无变化 = FAIL（仅 UI 态非真实生效）。 |
| 5 | 完全退出应用并重启，回到设置页。 | 自启开关状态保持步骤 4 的切换值（持久化）。 |
| 6 | 将自启开关切回原值保存。 | 步骤 4 入账的同一观察面反向核对（plist 移除或登录项条目消失）——自清理完成。 |
| 7 | **数据兼容·升级配方（裁决第 10 条；详细脚本见 `plans/2026-08-04-workbench-ui/upgrade-fixture.md`）**：① `git worktree add <tmp> 644ab16` 检出改版前基线；② 以 `DESKPET_USER_DATA_DIR=<共享测试目录>` 启动**基线构建**，造数：1 个 provider 配置 + 1 个密钥 + 2 个会话（各发一条可区分消息），关闭；③ 用**改版构建**以同一 `DESKPET_USER_DATA_DIR` 启动，核对。 | 改版构建下：2 个会话在侧栏可加载且消息原文在；provider 配置在设置页 Provider 区在位；密钥可用（不要求回显明文，以"provider 可正常发起一次往返"为生效证据）。任何丢失/报错 = FAIL（SessionDB/config.toml/keychain 兼容破坏）。 |

判定：步骤 1–5、7 全部满足才 PASS（步骤 6 为清理步）。
