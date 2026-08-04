# TC-WB-11 — 主题 CSS 变量合规（脚本可判）

> 对应 AC：WB-11
> manual_required: false（纯脚本判定；在仓库根目录执行）
> 判据口径（裁决第 3 条，`plans/2026-08-04-workbench-ui/oracle-clarifications.md`）：本仓已有双文件 token 体系（theme/tokens.ts + theme/components.ts），"集中单文件"按其本意落为——**色值字面量只允许出现在 theme/ 目录内**；扫描范围内其他文件零硬编码色值即 PASS。

> 色值字面量定义（QA 第 1 轮扩围）：十六进制 `#xxx…` **以及** `rgb()/rgba()/hsl()/hsla()` 函数字面量；统一正则 `COLOR_RE='(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()'`。

| 步骤 | 命令 | 判定（过/不过） |
|---|---|---|
| 1 | **主判据（2026-08-05 修正，behavior_change WBUI-BC-01，用户批准）**：三段与 acceptance WB-11 同口径——(a) 新增文件零命中：`grep -rnE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' tauri-app/src/views tauri-app/src/components/WorkbenchShell.tsx tauri-app/src/components/Sidebar.tsx tauri-app/src/components/SessionList.tsx tauri-app/src/components/ui.tsx | grep -vE '\.test\.'; true`；(b) 改造组件新增行零命中：`git diff 644ab16..HEAD -- tauri-app/src/components/SettingsPanel.tsx tauri-app/src/components/CapabilityCenterPanel.tsx tauri-app/src/components/SkillStorePanel.tsx | grep -E "^\+" | grep -vE "^\+\+\+" | grep -E '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()'; true`；(c) 存量文件不扫——fork 前遗留色值属基线债务（baseline.md 登记，清理另立任务）。 | **过**：(a)(b) 均零输出。原"全 src 零色值"口径超出 acceptance WB-11（新增/改造零硬编码）范围，连改版前基线都不可满足，经用户批准收敛。 |
| 1b | **src 外 HTML 单独一枪**（`*.html` 可能在 src 外，如根 `index.html`）：`find tauri-app -name "*.html" -not -path "*/node_modules/*" -not -path "*/dist/*" -not -path "*/target/*" -exec grep -lnE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' {} +; true` | **过**：零输出（扫描根扩至 tauri-app/ 全 HTML，排除 dist/node_modules/target 构建产物目录）。 |
| 2 | 新增组件定位辅助扫描：`grep -rnE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' tauri-app/src/views tauri-app/src/components/WorkbenchShell* tauri-app/src/components/Sidebar* tauri-app/src/components/SessionList* tauri-app/src/components/ui.ts 2>/dev/null; true` | **过**：零命中（本次新增文件零硬编码色值）。注：`ui.ts` 若未创建则跳过该路径不计失败。 |
| 3 | 改造组件新增行扫描：`git diff <改版基线commit>..HEAD -- tauri-app/src/components/SettingsPanel.tsx tauri-app/src/components/CapabilityCenterPanel.tsx | grep -E "^\+" | grep -vE "^\+\+\+" | grep -nE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()'; true` | **过**：零命中（variant 分支等新增行不引入硬编码色）。基线 commit 取 plan 落地前最后一次主干提交，执行时填入并记录。 |

判定：步骤 1（主判据）必须**过**，步骤 2–3 为更严子集/定位辅助，同样须过才 PASS。
