# Gate Manifest 草案 — Workbench UI 改版

> 状态：DRAFT（oracle 定稿于实现前；2026-08-05）
> 来源：acceptance.md「Workbench UI 改版」节 + behavior-contract.md + plan.md（未读实现代码）
> 用例组：`testcase/workbench-ui/`（TC-WB-01～18）

## 场景矩阵草案

确定性 UI 需求：除错误态（negative-safety）外全部 gate_type=positive-value。

| scenario_id | 用例 | required | ui | gate_type | required_lanes | min_root_runs | input_class | cold_start | expected_run_created |
|---|---|---|---|---|---|---|---|---|---|
| WBUI-S01-window-form | TC-WB-01 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui | **true**（首启居中需冷 user-data） | false |
| WBUI-S02-single-window | TC-WB-02 | true | true | positive-value | [manual-mcp, script] | 1 | deterministic-ui | false | false |
| WBUI-S03-layout-nav | TC-WB-03 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui | false | true（步骤 6 切换压力后一次真实往返） |
| WBUI-S04-chat-companion | TC-WB-04 | true | true | positive-value | [manual-mcp, script] | 1 | deterministic-ui（固定消息文本；B5 按裁决第 7/9 条两层配方：(a) `cd backend && uv run pytest tests/companion/ -q` 全绿 + (b) dev.sh stdout 捕获文件 grep companion_action 命中行原样入账（含 scope=companion_action、零 window_scope_denied）+ devtools `invoke("get_window_control_credential")` 非 error；真实卡片确认为加分非必需） | false | **true**（真实消息往返创建 run） |
| WBUI-S05-session-list | TC-WB-05 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui | false | true（新建会话内一条真实往返） |
| WBUI-S06-skills-view | TC-WB-06 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui | false | false |
| WBUI-S07-artifacts-view | TC-WB-07 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui（预置文件 fixture） | false | false |
| WBUI-S08-settings-view | TC-WB-08 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui | false | false |
| WBUI-S09-pet-removal | TC-WB-09 | true | false | positive-value | [script] | 1 | static-scan | false | false |
| WBUI-S10-geometry-memory | TC-WB-10 | true | true | positive-value | [manual-mcp, script] | 1 | deterministic-ui（B8 步骤含文件 fixture） | **true**（重启恢复 + 旧记录回退） | false |
| WBUI-S11-theme-vars | TC-WB-11 | true | false | positive-value | [script] | 1 | static-scan | false | false |
| WBUI-S12-build-green | TC-WB-12 | true | false | positive-value | [script] | 1 | build-test | false | false |
| WBUI-S13-backend-not-ready | TC-WB-13 | true | true | **negative-safety** | [manual-mcp] | 1 | fault-injection（进程级，非 mock 前端） | **true**（后端未就绪即启动态） | false |
| WBUI-S14-tray-menu | TC-WB-14 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui | false | false |
| WBUI-S15-empty-states | TC-WB-15 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui | **true**（零会话/零产物需冷 user-data） | true（步骤 3 空态转非空态的一次真实往返） |
| WBUI-S16-close-exit-paths | TC-WB-16 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui（B13：红钮/Cmd+Q/托盘三路径退出矩阵 + 进程/端口核对） | **true**（每条路径退出后重启验几何恢复） | false |
| WBUI-S17-scale-long-text | TC-WB-17 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui（fixture：≥30 会话含 120 字符无空格长标题 + ≥30 产物；800×560） | false | false |
| WBUI-S18-session-delete-edge | TC-WB-18 | true | true | positive-value | [manual-mcp] | 1 | deterministic-ui（删当前打开会话/删后归属/删至空态） | false | true（步骤 2/5 各一次真实往返验证归属） |

说明：
- `min_root_runs=1` 全表统一（确定性 UI，无多样本需求）。
- `expected_run_created`：**以矩阵字段为准**（true 的场景其用例步骤含真实消息往返：S03/S04/S05/S15/S18）；字段为 false 的场景不得以"顺手发消息"制造 run 计数噪声。
- lanes 命名沿用现有惯例语义：`manual-mcp` = 真机 MCP 真坐标点击 + 截图；`script` = 可复跑命令、输出入账。

## impact_paths 映射草案

> 原则：宁缺勿滥；写不准就留空并注明 **fail-closed 回退全量**（该场景在任何变更下都进入必跑集）。
> 路径为仓库根相对 glob，取自 plan.md 文件影响清单（plan 可读；未从实现代码反推）。

| scenario_id | impact_paths | 备注 |
|---|---|---|
| WBUI-S01-window-form | `tauri-app/src-tauri/tauri.conf.json`, `tauri-app/src-tauri/Cargo.toml`, `tauri-app/src-tauri/src/window_geometry.rs` | |
| WBUI-S02-single-window | `tauri-app/src-tauri/tauri.conf.json`, `tauri-app/src-tauri/capabilities/default.json`, `tauri-app/src-tauri/src/commands.rs`, `tauri-app/src-tauri/src/lib.rs`, `tauri-app/src/main.tsx`, `tauri-app/src/App.tsx` | |
| WBUI-S03-layout-nav | `tauri-app/src/components/WorkbenchShell.tsx`, `tauri-app/src/components/Sidebar.tsx`, `tauri-app/src/App.tsx`, `tauri-app/src/views/**` | |
| WBUI-S04-chat-companion | `tauri-app/src/views/ChatView.tsx`, `tauri-app/src/code-panel/controlWs.ts`, `tauri-app/src-tauri/src/webview_permissions.rs`, `backend/deskpet/companion/control_credentials.py`, `backend/deskpet/companion/control_ingress.py`, `backend/deskpet/companion/migrations/**`, `tauri-app/src/chat/**` | 主要矛盾链路：五处硬编码任一变动都必须重跑 |
| WBUI-S05-session-list | `tauri-app/src/components/SessionList.tsx`, `tauri-app/src/components/Sidebar.tsx`, `tauri-app/src/chat/**` | |
| WBUI-S06-skills-view | `tauri-app/src/views/SkillsView.tsx`, `tauri-app/src/components/CapabilityCenterPanel.tsx`, `tauri-app/src/components/SkillStorePanel.tsx` | |
| WBUI-S07-artifacts-view | `tauri-app/src/views/ArtifactsView.tsx`, `tauri-app/src-tauri/src/artifact_ops.rs` | |
| WBUI-S08-settings-view | `tauri-app/src/views/SettingsView.tsx`, `tauri-app/src/components/SettingsPanel.tsx` | |
| WBUI-S09-pet-removal | （**留空 — fail-closed 回退全量**） | 删除面横跨全仓，无法用 glob 收窄；任何变更批次都复跑（脚本秒级，代价可接受） |
| WBUI-S10-geometry-memory | `tauri-app/src-tauri/src/window_geometry.rs`, `tauri-app/src-tauri/src/lib.rs`, `tauri-app/src-tauri/tauri.conf.json` | |
| WBUI-S11-theme-vars | `tauri-app/src/views/**`, `tauri-app/src/components/**` | theme/tokens 文件本身在用例内白名单排除 |
| WBUI-S12-build-green | （**留空 — fail-closed 回退全量**） | 构建/测试门天然全量适用 |
| WBUI-S13-backend-not-ready | `tauri-app/src/views/ChatView.tsx`, `tauri-app/src/code-panel/controlWs.ts`, `tauri-app/src/components/Sidebar.tsx` | 状态源=controlWS.state()（plan 挑战轮 P1）；徽章聚合口径同测 |
| WBUI-S14-tray-menu | `tauri-app/src-tauri/src/lib.rs` | tray 文案/行为均在 lib.rs |
| WBUI-S15-empty-states | `tauri-app/src/components/SessionList.tsx`, `tauri-app/src/views/ArtifactsView.tsx` | |
| WBUI-S16-close-exit-paths | `tauri-app/src-tauri/src/lib.rs`, `tauri-app/src-tauri/src/window_geometry.rs`, `tauri-app/src-tauri/tauri.conf.json` | B13：Destroyed→exit(0) 生命周期在 lib.rs |
| WBUI-S17-scale-long-text | `tauri-app/src/components/SessionList.tsx`, `tauri-app/src/views/ArtifactsView.tsx`, `tauri-app/src/components/WorkbenchShell.tsx`, `tauri-app/src/components/Sidebar.tsx` | |
| WBUI-S18-session-delete-edge | `tauri-app/src/components/SessionList.tsx`, `tauri-app/src/views/ChatView.tsx` | |

## applicability 三维判定（照抄 acceptance 预 declaration）

| 维度 | 值 | 理由（≥10 字） | decided_by |
|---|---|---|---|
| input_sensitive | **false** | 本需求全部为确定性 UI（布局/导航/设置/列表）与既有聊天链路的回归验证；不新增"输出质量随输入语义变化"的功能面。WB-4 为单场景链路回归，不适用 MANUAL_SCENARIO_MATRIX 多类别门。 | user-confirmed-acceptance |
| llm_payload_driven | **false** | 不新增 LLM 输出驱动端侧状态机/卡片/流程的面；聊天回复仅 markdown 文本展示（既有能力）。 | user-confirmed-acceptance |
| stateful_init | **false** | 不新增异步注册服务/远程配置/登录态依赖；dev 冷启动可用性由 WB-1..5 的真机验收覆盖。 | user-confirmed-acceptance |

## 备注 / 待定稿项

1. lanes 的正式命名以 gate 账本 schema 为准（本稿用 manual-mcp/script 语义占位）。
2. ~~WBUI-S04 的 companion 特权动作 canonical 触发配方待定~~ **已裁决**（`oracle-clarifications.md` 第 7 条）：两层配方已同步进 S04 行与 TC-WB-04 步骤 4/5/5b，不再依赖 LLM 随机出卡片。其余 7 条含糊条款也已全部裁决并回填用例（见 `testcase/workbench-ui/index.md` 含糊条款节）。
3. impact_paths 中 `tauri-app/src/chat/**`、`views/**`、`ui.ts` 等为 plan 宣告的新目录/新文件；若实现落位不同，按"目录改名等价映射"修订 glob，不得借机收窄。
4. 冷启动性能对照（±3s）不入场景矩阵，按 plan T16 以账本记录项承接。
