# Oracle 澄清裁决（7 条含糊条款）

> 裁决人：主编排者。依据：plan/侦察报告/行为契约。这些是"给 black-box 作者补齐的
> 环境事实与判定口径"，不构成行为语义变更；acceptance 对应条款按此口径执行。

1. **WB-6 操作全集**（基线操作清单）：能力中心 = capabilities/operations 两个 tab 切换、
   能力列表渲染、打开一项能力详情、operations tab 的 cancel/rollback/uninstall 三动作
   入口存在（不要求真机执行 uninstall）、「旧 Skill Store」入口可达。以上 6 项即
   "详情/操作不回归"的判定全集。
2. **WB-8 原设置项清单**（权威快照）：以改版前 SettingsPanel 的分区为准——
   Provider/模型配置区（SettingsProviders）、Embedder 状态卡、模型上下文卡、
   数据目录设置、Agent 有效执行预算、云端 API key 管理、自启开关（改版后新迁入）。
   「桌宠形象」区块为唯一预期消失项。
3. **WB-11 判据修正**：本仓已有双文件 token 体系（theme/tokens.ts + theme/components.ts），
   "集中单文件"按其本意（颜色有唯一权威来源）落为可判定口径：**色值字面量只允许出现在
   theme/ 目录内**；扫描范围内其他文件零硬编码色值即 PASS。
4. **冷启动阈值**：采用 plan T16 口径 ±3s（acceptance"不显著劣化"的量化）。
5. **WB-1 首启居中**：单显示器手工判定——窗口几何中心落在屏幕中心 ±10% 区域内即 PASS；
   多显示器情形不做断言（记录观察即可）。
6. **WB-10 旧几何文件位置**（环境准备事实，非 oracle）：`<user_data>/window_geometry.json`；
   dev 模式 user_data = `tauri-app/src-tauri/target/debug/userdata/`。预置旧记录即写
   `{"width":500,"height":640}` 到该文件。
7. **B5 canonical 触发配方**（避免依赖 LLM 随机出卡片）：两层判定——
   (a) 后端 `uv run pytest tests/companion/ -q` 全绿（凭据链单测层）；
   (b) 真机层：应用启动完成后，控制连接以 scope=companion_action 建立成功
   （后端日志无 `window_scope_denied`、无 scope 降级记录），且
   `get_window_control_credential` 在主窗调用返回成功。若会话中自然出现 companion
   卡片则追加一次真实卡片确认作为加分证据，非必需。

> 与 acceptance 的关系：第 3/4 条是量化澄清（原文含糊处的可判定化），其余为环境事实
> 与判定配方。无任何一条反转 before/after 行为表。

## QA 第 1 轮追加裁决（8–10）

8. **红钮语义（M1）**：完全退出（= 托盘「退出」），沿用现状 Destroyed→exit(0) 连带回收
   后端的生命周期设计（防 8100 孤儿端口是原始设计动机，改 hide-to-tray 会破坏它）。
   已补入行为契约 B13。TC-WB-16 按"裁决为退出"分支书写，禁止分裂态。
9. **E1 取证配方（B5 第 (b) 层）**：① 日志取自 dev.sh 终端 stdout（后端 uvicorn 日志
   合流）；② 断言用正向证据：`grep -aE "companion_action" <dev.sh 输出捕获文件>` 检索
   连接建立行，**把实际命中行原样入账并断言含 scope=companion_action 且无
   window_scope_denied**（字段样式以实测行为准，入账即判定）；③ canonical 触发：
   真机打开 devtools 控制台执行一次
   `await window.__TAURI__.core.invoke("get_window_control_credential")`（withGlobalTauri
   已开启），记录返回对象非 error 即 (b) 层后半判定 PASS——消除 vacuous pass。
10. **E2/E3 基线锚点配方**：建 `plans/2026-08-04-workbench-ui/upgrade-fixture.md` 脚本
   步骤——`git worktree add <tmp> 644ab16` + `DESKPET_USER_DATA_DIR=<共享测试目录>` 启动
   基线构建（预热编译后测第二次启动计时，写入 baseline.md 补记行）→ 造数（1 provider
   配置 + 1 密钥 + 2 会话）→ 关闭 → 改版构建同 env 启动核对。冷启动口径：进程拉起→
   窗口可点击，预热后第二次启动计时。
