# Workbench UI 改版 — 验收测试用例组

> 状态：DRAFT（black-box oracle，定稿于实现落地前；2026-08-05）
> 对应验收：acceptance.md「Workbench UI 改版（去桌宠、工作台化）」WB-1～WB-12 + 非功能条款
> 对应计划：`plans/2026-08-04-workbench-ui/plan.md` ｜ 行为契约：`plans/2026-08-04-workbench-ui/behavior-contract.md`
> gate manifest 草案：`plans/2026-08-04-workbench-ui/gate-manifest-draft.md` ｜ 冒烟清单：`plans/2026-08-04-workbench-ui/smoke-checklist.md`
> 纪律：本组用例按 black-box 铁律编写（未读任何实现代码/diff）；UI 用例（manual_required: true）须真机 MCP 真坐标点击 + 截图留证，禁止 WS 直注/脚本回放代替 UI 输入；WB-9/11/12 为脚本可判，命令输出原样入账。

## 用例清单

| 用例 | 范围 | manual_required | 判定方式 |
|---|---|---|---|
| [TC-WB-01](./TC-WB-01-window-form.md) | 主窗普通窗口形态（标题栏/默认尺寸/min/居中/Dock/不透明） | true | 真机 + 静态辅助 |
| [TC-WB-02](./TC-WB-02-single-window.md) | message-panel 第二窗口移除、运行期单窗 | true | 真机 + grep 辅助 |
| [TC-WB-03](./TC-WB-03-workbench-layout.md) | 侧栏导航 + 四视图切换 + 高亮 + min 尺寸布局 | true | 真机 |
| [TC-WB-04](./TC-WB-04-chat-roundtrip-companion.md) | 消息真实往返 + markdown + mic 占位 + **B5 companion 特权（主窗）** | true | 真机 + 日志对账 |
| [TC-WB-05](./TC-WB-05-session-list.md) | 会话列表倒序/加载/新建/重命名/删除 | true | 真机 |
| [TC-WB-06](./TC-WB-06-skills-view.md) | SkillsView 页面化、能力列表/详情/操作不回归（裁决第 1 条 6 项全集） | true | 真机 |
| [TC-WB-07](./TC-WB-07-artifacts-view.md) | ArtifactsView 倒序列表 + 打开/在文件夹显示 | true | 真机 |
| [TC-WB-08](./TC-WB-08-settings-view.md) | SettingsView 页面化、7 分区核对表逐项保留（裁决第 2 条）、保存生效、数据兼容 | true | 真机 |
| [TC-WB-09](./TC-WB-09-pet-code-removal.md) | 桌宠代码全删（grep/目录/编译兜底） | **false** | 脚本 |
| [TC-WB-10](./TC-WB-10-window-geometry.md) | 几何记忆（拖拽持久化）+ **B8 旧记录回退默认** | true | 真机 + 脚本预置 |
| [TC-WB-11](./TC-WB-11-theme-variables.md) | CSS 变量合规、无硬编码色、变量表单文件 | **false** | 脚本 |
| [TC-WB-12](./TC-WB-12-tests-build-green.md) | vitest/cargo test/tsc+build/cargo check 全绿 | **false** | 脚本 |
| [TC-WB-13](./TC-WB-13-error-state-backend-not-ready.md) | **错误态**：后端未就绪状态条+重试（negative-safety） | true | 真机 |
| [TC-WB-14](./TC-WB-14-tray-menu.md) | **B10 托盘三项**文案与行为 | true | 真机 |
| [TC-WB-15](./TC-WB-15-empty-states.md) | **空态**：无会话引导 / 无产物占位（含 StartupOverlay/onboarding 首启路径） | true | 真机（冷 user-data） |
| [TC-WB-16](./TC-WB-16-close-exit-paths.md) | **B13 红钮与退出路径矩阵**（红钮/Cmd+Q/托盘=完全退出全清 + 各路径几何恢复；裁决第 8 条） | true | 真机 + 进程/端口核对 |
| [TC-WB-17](./TC-WB-17-scale-long-text.md) | **规模与长文本边界**：≥30 会话（120 字符无空格长标题）+ ≥30 产物 @ 800×560 | true | 真机（fixture 造数） |
| [TC-WB-18](./TC-WB-18-session-delete-edge.md) | **会话删除边界**：删当前打开会话/删后归属/删至空态恢复 | true | 真机 |

## 覆盖矩阵（AC → 用例）

| AC | 用例 | 备注 |
|---|---|---|
| WB-1 | TC-WB-01、TC-WB-14、TC-WB-16 | 窗口形态 + 品牌/托盘面（plan T14 归 WB-1）+ B13 退出矩阵 |
| WB-2 | TC-WB-02 | 含 webview 计数盲区补枪 |
| WB-3 | TC-WB-03、TC-WB-17 | 含 ≥10 轮快速切换压力（步骤 6）与规模边界 |
| WB-4 | TC-WB-04 | 含 B5 两层判定专门步骤（裁决第 7 条：pytest 层 + 真机 scope 证据层；卡片确认为加分） |
| WB-5 | TC-WB-05、TC-WB-15(步骤1/3)、TC-WB-17、TC-WB-18 | 删除边界与规模边界补面 |
| WB-6 | TC-WB-06 | |
| WB-7 | TC-WB-07、TC-WB-15(步骤2)、TC-WB-17(步骤5) | |
| WB-8 | TC-WB-08 | |
| WB-9 | TC-WB-09 | 脚本可判 |
| WB-10 | TC-WB-10、TC-WB-16 | 含 B8 回退默认专门步骤（步骤 5–7）+ 畸形 JSON/高于 min 对照（步骤 8–10）+ 三退出路径几何恢复 |
| WB-11 | TC-WB-11 | 脚本可判 |
| WB-12 | TC-WB-12 | 脚本可判 |
| 非功能·错误态 | TC-WB-13 | negative-safety |
| 非功能·空态 | TC-WB-15、TC-WB-18(步骤4) | |
| 非功能·数据兼容 | TC-WB-08(步骤7) | 升级配方（裁决第 10 条，见 upgrade-fixture.md）：644ab16 基线造数→改版构建同 env 核对；TC-WB-05 挂名已移除（其不含跨版本升级面，属覆盖虚标） |
| 行为契约 B13 | TC-WB-16 | 红钮=完全退出（裁决第 8 条） |
| 非功能·冷启动性能 | TC-WB-12（步骤 7） | 阈值 ±3s（裁决第 4 条量化） |

## 覆盖矩阵（用例 → AC）

| 用例 | 覆盖 AC / 契约 |
|---|---|
| TC-WB-01 | WB-1 / B1 |
| TC-WB-02 | WB-2 / B2 |
| TC-WB-03 | WB-3 / B6(入口) |
| TC-WB-04 | WB-4 / B4、**B5**、B9 |
| TC-WB-05 | WB-5 / B3 |
| TC-WB-06 | WB-6 / B6 |
| TC-WB-07 | WB-7 / B7 |
| TC-WB-08 | WB-8、非功能·数据兼容 / B6、B12 |
| TC-WB-09 | WB-9 / B11 |
| TC-WB-10 | WB-10 / **B8** |
| TC-WB-11 | WB-11 |
| TC-WB-12 | WB-12 |
| TC-WB-13 | 非功能·错误态 |
| TC-WB-14 | WB-1 / **B10** |
| TC-WB-15 | 非功能·空态、WB-5、WB-7 |
| TC-WB-16 | WB-1、WB-10 / **B13**（裁决第 8 条） |
| TC-WB-17 | WB-3、WB-5、WB-7（规模/长文本边界面） |
| TC-WB-18 | WB-5、非功能·空态（删除边界面） |

## 含糊条款清单 — **已全部裁决**（见 `plans/2026-08-04-workbench-ui/oracle-clarifications.md`，用例已按裁决回填）

1. **WB-6「详情/操作不回归」** — 已裁决（裁决第 1 条）：6 项判定全集（两 tab 切换 / 列表渲染 / 详情打开 / cancel·rollback·uninstall 三入口存在 / 旧 Skill Store 入口可达）已写入 TC-WB-06。
2. **WB-8「原设置项逐项保留」** — 已裁决（裁决第 2 条）：7 分区核对表（Provider/模型、Embedder 状态卡、模型上下文卡、数据目录、执行预算、云端 API key、自启开关）+「桌宠形象」为唯一预期消失项，已写入 TC-WB-08 步骤 2。
3. **WB-11「变量表集中单文件」** — 已裁决（裁决第 3 条）：双文件 token 体系合法；判据改为「色值字面量只允许出现在 theme/ 目录内」，已写入 TC-WB-11 步骤 1（主判据）。
4. **非功能·性能「冷启动不显著劣化」** — 已裁决（裁决第 4 条）：阈值写死 ±3s，已写入 TC-WB-12 步骤 7。
5. **WB-1「首启居中」** — 已裁决（裁决第 5 条）：单显示器手工判定，窗口几何中心落在屏幕中心 ±10% 区域内即 PASS；多显示器仅记录不断言。已写入 TC-WB-01 步骤 2。
6. **WB-10 旧几何文件位置** — 已裁决（裁决第 6 条）：dev 模式路径 `tauri-app/src-tauri/target/debug/userdata/window_geometry.json`，预置内容 `{"width":500,"height":640}`。已写入 TC-WB-10 步骤 5。
7. **B5 canonical 触发配方** — 已裁决（裁决第 7 条 + 第 9 条取证配方）：两层判定——(a) `cd backend && uv run pytest tests/companion/ -q` 全绿 + (b) dev.sh stdout 捕获文件 `grep companion_action` 命中行原样入账（含 scope=companion_action、零 window_scope_denied）+ devtools `await window.__TAURI__.core.invoke("get_window_control_credential")` 返回非 error；真实卡片确认为加分非必需。已写入 TC-WB-04 步骤 4/5/5b。

### QA 第 1 轮追加（裁决 8–10，已回填）

8. **红钮语义（B13）** — 裁决第 8 条：红钮=完全退出（等价托盘退出，沿用 Destroyed→exit(0) 防 8100 孤儿设计）。落 TC-WB-16 全用例。
9. **B5 取证配方** — 裁决第 9 条：落 TC-WB-04 步骤 5 与 smoke 第 8 项（见上第 7 条）。
10. **升级/冷启动基线锚点** — 裁决第 10 条：644ab16 worktree + DESKPET_USER_DATA_DIR 共享目录造数配方，落 `plans/2026-08-04-workbench-ui/upgrade-fixture.md`，消费方 TC-WB-08 步骤 7 与 TC-WB-12 步骤 7（预热后第二次启动计时）。
