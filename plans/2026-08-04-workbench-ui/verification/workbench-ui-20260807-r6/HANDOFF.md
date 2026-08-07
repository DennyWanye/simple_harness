# r6 交接状态（2026-08-07）

基线：**9a19261**（干净树，r5 六缺陷修复已提交）。run_dir = `verification/workbench-ui-20260807-r6`。
manifest 已升级到 r6（baseline head 9a19261，新增 behavior_change **WBUI-BC-03**，用户 2026-08-07 批准）。

## 已完成并入账（9 场景，24 条证据）

| 场景 | 道 | 结果 |
|---|---|---|
| S02 单窗形态 | script | PASS（manual 半边未跑） |
| S04 chat+companion | script + **manual-mcp** | PASS（BC-03 修正判据） |
| S05 会话列表 | manual-mcp | PASS |
| S09 桌宠代码全删 | script | PASS |
| S10 几何记忆 | script | PASS（manual 半边未跑） |
| S11 主题变量 | script | PASS |
| S12 测试构建全绿 | script | PASS |
| S17 规模长文本 | manual-mcp | PASS |
| S18 会话删除边界 | manual-mcp | PASS |

阶段：script-lane 已 phase-end；manual-lane **phase-start 后未 end**（finalize 前须 phase-end）。
timing 已覆盖：两轮 user_wait（钥匙串授权）+ S04/S05/S17/S18 四段 manual_e2e。

## ⚠️ 2026-08-07 晚：manifest 中途改动导致全量复测（编排者失误）

init 之后为追加 BC-03 改了 `verification-manifest.json` → 运行时指纹失配。
`re-attest --reason "..."` 判定为 **behavioral**（manifest 定义 oracle，按设计不算 doc-only），
后果：**全部 required 场景必须重跑并 record-run**，晚于本次 attestation 才算数。
- 已自动重跑：script lane 六项（S02/S04/S09/S10/S11/S12 脚本半边）
- **需重跑**：S04/S05/S17/S18 的 manual-mcp 半边（证据文件仍有效，只需重测后重新 record-run；
  attach-evidence 用 `--replace`）
**教训（下次务必）**：任何 oracle/manifest 修正必须在 `init` **之前**定稿；跑起来之后不要碰 manifest。

## 剩余未跑（9 项）

S01 窗口形态（需**全新隔离 user-data** 冷启动）、S02 真机半边、S03 四视图导航（含 ≥10 轮切换压力 +
「更多」三入口开关 + devtools 无报错）、S06 技能中心、S07 产物库、S08 设置、S10 真机半边（拖拽+重启恢复）、
S13 后端未就绪、S14 托盘菜单、S15 空态（冷环境 onboarding，**需用户本人输入登录凭据**）、S16 退出路径矩阵。

## S06 当前卡点（用户已定方向：造进行中操作再测）

判定项④⑤⑥要 cancel/rollback/uninstall 入口可见。本环境 17 个 operation 全 `succeeded`，
后端 `ui_projection.py:234-243` 只在 `status==running` 给 cancel、`succeeded && pack_id &&
rollback_available/uninstall_available` 给 rollback/uninstall → 当前一个按钮都不渲染。
**下一步**：在 kimi-k3 会话真机发「帮我生成一个新技能：…」触发 build/install operation，
趁其 running 切技能中心→操作 tab 截图 cancel 入口；uninstall 走能力详情页（`available_actions` 含
uninstall 时渲染，见 `CapabilityCenterPanel.tsx:551-571`）。
未完成前 S06 不得记 PASS。

## 环境与操作要点（本轮实测新增，务必遵守）

1. **必须用 relay 模式启动**：`launch-app.sh` 已改为
   `npm exec tauri dev -- --config '{"build":{"beforeDevCommand":"npm run dev:relay"}}'`。
   普通 `npm run dev` 跑的是 OSS 版：`getAuthAdapter()` 不返回 RelayAuthAdapter →
   侧栏「更多」无账户图标、`/v1/me` 恒 401、companion_profile_bind 被拒。
2. **坐标换算**：screencapture 出的是 Retina 像素（2940×1912），cliclick 吃**逻辑点**（1470×956）——
   量到的像素坐标要 **÷2**。窗口截图（drive.sh shot）同理：crop 图内像素 ÷2 + 窗口原点 = 屏幕点。
   本轮因此空点了十几次，务必先算再点。
3. **删除确认框的「删除」按钮位置随标题长度浮动**（长标题把对话框撑高）——每次都要重新截图定位，
   不能复用上一次坐标。
4. **中文输入法会拦截 cliclick 的 `t:` 键入**（"kimi" 被拆成拼音候选）。所有文本一律走
   `pbcopy` + Cmd+V；粘贴前先 `kd:cmd t:a ku:cmd` 清空。
5. **新会话默认模型是 `sf-glm-5.2`（账号无额度→402）**，必须经头部模型按钮切到 kimi-k3 才能真往返。
   402 时消息流显示 `provider_dispatch_unknown_after_handoff — failed`（显式失败，非黑洞）。
6. 侧栏纵坐标随「会话」视图是否展开列表而变：展开时技能中心≈y1029(逻辑514)、产物库≈y1099；
   紧凑时会话≈y204、技能≈y287、产物≈y367。**每步先截图定位**。
7. 已知缺陷（r5 已挂 task_74bf936a，不阻塞判定）：清空保留会话 default 后主面板仍渲染其已删消息。

## 收尾清单

1. 跑完剩余 9 项 → 各自 record-timing / attach-evidence(`--ui-action`) / record-run
2. 负向场景补 `--negative-assertion` 证据；应创建 Run 的场景补 `--run-id-under-test`
3. `phase-end --phase manual-lane` → `finalize`
4. r6 SHIPPABLE 后用 CLI **正当退役** r3/r4/r5：
   `retire --run-dir <旧> --reason "..." --superseded-by <r6 dir>`
5. 更新 ARCHITECTURE/PROJECT_STATUS.md 与 memory
