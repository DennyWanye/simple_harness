# Relay 账户面板 Phase A = WI-C — 余额 ¥→USD bug 修复 + email + 测试账号徽章 手工测试（自包含·单文档可执行）

> **被测范围**：relay-edition 账户面板 `AccountSettingsPanel`（plan `plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md` §WI-C）。
> 招牌 bug：旧版面板写死 `¥` 标签（`formatCny`）展示 `¥713.17` —— 实际钱包早已迁为 **USD 本位**，应显示 `$X.XX`。
> WI-C 三件事：①**修 ¥→USD**（按响应 `balance.currency` 决定符号，缺省 USD→`$`）②**显示登录账号 email**③**测试账号徽章**（`is_test_account===true` → email 旁橙色「测试账号」）。
>
> **本次被测实现文件**：
> - `tauri-app/src/auth/AccountSettingsPanel.tsx` —
>   - `formatMoney(amount_minor, currency)`（:77-86）：`currency?.trim().toUpperCase() || "USD"` → `CURRENCY_SYMBOLS` 查符号（USD→`$`、CNY→`¥`、缺/未知→`<CODE> ` 前缀），`amount_minor/100` 两位小数；`amount_minor==null` → `—`。
>   - `UsageDetails`（:298-326）：`balanceCurrency = usage.balance?.currency || "USD"`；**钱包余额**`formatMoney(balance.amount_minor, balanceCurrency)`、**本月已用**`formatMoney(period.used_minor, balanceCurrency)`、**速率上限**`rpm req/min`。
>   - header（:205-225）：`<span>{user.email}</span>` + `user.is_test_account === true && <span 测试账号 橙徽章>`（样式 `testAccountBadgeStyle` :516-525 边框 `#fdba74`/底 `#fff7ed`/字 `#c2410c`）。
> - `tauri-app/src/auth/types.ts`（:20-36）：`User.email`(必填)、`balance_minor?`、`is_test_account?`。
> - 入口链路：`Toolbar.tsx`（:114-125）relay-edition 账户图标按钮 `testId="relay-account-pill"`（icon=user，title「账户设置」）→ `App.tsx:2287` `openAccountRef.current?.()` → `RelayEdition.tsx:216-248` 渲染 `role="dialog" aria-label="账户设置"` modal（标题「账户设置」）→ 内嵌 `AccountSettingsPanel`。
>
> **目的**：在桌宠真机 UI 链路上验证 —— 登录 dev 测试账号后打开账户面板，**余额/本月已用/速率上限全部按 USD 渲染（绝不出现 `¥`）**、**email 与登录账号一致**、**测试账号橙徽章显示**、**余额 0 时显示 `$0.00` 而非 `¥0.00`**；并对面板任何位置做 `¥` 负向扫描，回归招牌 bug。
>
> **需 windows-mcp 真测**：是（触发词「真测/手工测试」已命中，本纪律强制生效）。判定 = 真坐标点击打开面板 + 真截图 + 截图文字 / DOM(`get_page_text`/eval) / 渲染断言，**不允许**用 `import`/脚本调 `formatMoney()`、WebSocket 直注、纯读源码 当 UI 证据。
>
> **通过判据**：§0.3 全部 ★ 必过项 PASS；面板余额/本月已用/速率上限三处货币符号均为 `$`（或 USD 前缀），面板内**无任何 `¥` 字符**；email 文本 == 登录账号；橙「测试账号」徽章可见。
>
> **对应 plan**：[plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md](../../plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md) §WI-C（:236-243）+ §WI-7 E2E-1（:265）。
> **最后更新**：2026-06-26

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 环境（必须先满足）

1. **进程清场**：`taskkill /F /IM deskpet.exe` + 杀残留 Vite node（项目坑 #1）。**不要手动起 backend / vite**（坑 #7/#9，Tauri 自管唯一 backend + 唯一 vite）。
2. **跑 worktree / master 当前前端代码（非 frozen）**：本 phase 改动**纯前端**（`AccountSettingsPanel.tsx` 等），由 Tauri 的 `beforeDevCommand` 起 vite 热加载即可生效；为保险仍只给 **Tauri 进程**注入 env（坑 #8）：
   - `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`
   - `DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`
   - `DESKPET_DEV_MODE=1`
   - `DESKPET_USER_DATA_DIR=G:\projects\deskpet\backend\userdata`
   - `DESKPET_CLOUD_API_KEY=<根目录 .env 里的 tsk_ key>`（relay 链路；token 常过期需重登，见 [`LOCAL-DEV-CREDENTIALS.md`](../../LOCAL-DEV-CREDENTIALS.md)）
   - `NO_PROXY=*`（坑：Clash 7897 掐空闲长连，登录/usage 拉取误判挂起）
   - `VITE_AUTH_EDITION=relay`（**关键**：必须是 relay edition，否则根本没有账户面板 / 账户按钮。OSS 默认 manual 不渲染任何 relay UI）。
   启动后 log **必须**出现 `[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`；
   若见 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **登录凭据**：从 [`LOCAL-DEV-CREDENTIALS.md`](../../LOCAL-DEV-CREDENTIALS.md)（gitignored）读 dev 账号。
   - ⚠️ 该 dev 账号是 **`deskpettest+...@...` 测试账号** → 服务端 `/v1/me`/`/v1/usage/summary` 应回 `is_test_account=true` → **徽章应显示**（TC-2 招牌）。
   - ⚠️ 该测试账号余额**大概率为 $0** → 正好真测 TC-4「余额 0 显 `$0.00` 不是 `¥0.00`」。
   - ⚠️ token 常过期：若登录后面板「账户余额」区显示红色错误文案（getUsage 失败），先重登拿新 token；usage 拉不到时余额会退化为 `暂无用量数据。`（见 §5 边界 B5）。
4. **日志落盘**：启动命令把 tauri dev 输出重定向到
   `plans/manual-results-2026-06-26-relay-provider-wiC/tauri-dev.log`（backend structlog 走 stderr → Tauri pipe → 落这份；前端 console 看 webview，必要时用 windows-mcp 的 DOM eval 取 DOM 文本）。
5. **截图存盘目录**：`plans/manual-results-2026-06-26-relay-provider-wiC/screenshots/`（不存在先建）。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

> 触发词已命中（「真测 / 手工测试」）。本纪律强制生效，完整版见 `~/.claude/knowledge-base/windows-mcp-e2e.md`。

1. **真模拟人**：每个 case 必 `Screenshot`/`Snapshot` → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 截图文字 / DOM 判 PASS/FAIL。
2. **禁绕过（HARD）**：**不允许**用以下任一**替代**真点击作为 UI 证据 —— `import` 调 `formatMoney("75614","USD")`、`ws://127.0.0.1:8100/*` WebSocket 直注 user/usage、纯 `Read` 源码确认 `formatCny` 已删、vitest 跑 `AccountSettingsPanel` 单测。**那些只能当辅助旁证，本 phase 的 PASS 必须建立在真打开面板 + 真截图 + 截图/DOM 文字判定上。**（坑：`import { formatMoney }; formatMoney(0,"USD")==="$0.00"` 在脚本里跑一遍**不算** TC-4 PASS，那是脚本回放，违反 `feedback_real_e2e_not_script_replay`。）
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-26-relay-provider-wiC/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**（登录框输 email/密码、或核对中文文案时）：windows-mcp `Type`(UIA SetValue) 多数可靠；不落则 STA Runspace + `Clipboard.SetText("...")` + 先 Click 输入框聚焦再 Ctrl+V。**登录账号/密码勿截进图**（截图前确保已离开登录框；见 CLAUDE.md 安全约束）。
7. **货币符号逐字核对（本 phase 关键）**：`$` 与 `¥` 必须**放大截图逐字看**；`¥`(U+00A5) 与 `￥`(全角 U+FFE5) 都算违例。优先用 DOM 取文本（windows-mcp `Scrape`/`get_page_text`，或对 webview 注入 JS 读 `[data-testid="account-recharge-btn"]` 的前一个 `<dl>` 文本）减少肉眼误判，但 DOM 取文本**仍须配一张真截图**作物证。

### 0.3 ★ WI-C 一票否决项（任一 FAIL = WI-C 不算完成，回 plan 修）

| Case | 验收点 | 类别 |
|---|---|---|
| **TC-1** | 登录 dev 账号 → Toolbar 点账户按钮(`relay-account-pill`) → 出现 `aria-label="账户设置"` 面板，标题「账户设置」+「账户余额」区可见 | ★ 面板可达（前置链路） |
| **TC-2** | 「钱包余额」显示 **`$X.XX`**（美元符号 `$`），**面板任何位置无 `¥`/`￥`** | ★ 招牌 bug 回归（¥→USD） |
| **TC-3** | dev 是 `deskpettest+` 测试账号 → email 旁显**橙色「测试账号」徽章** | ★ 测试账号徽章 |
| **TC-4** | 余额为 0（dev 测试号常态）→ 显示 **`$0.00`**（不是 `¥0.00`、不是 `—`、不是 `¥0`） | ★ 0 余额仍 USD |
| **TC-6** | 「本月已用」「速率上限」也按 USD 渲染、**无 `¥`** | ★ 全字段统一 USD |

> TC-2 与 TC-4 互补：TC-2 验「符号是 $」，TC-4 验「0 值不退化成 ¥0.00 / —」。若 dev 账号余额恰好非 0，TC-2 仍必过，TC-4 降级为 §5 边界 B1（单测已覆盖，真机标 env-limited）。
> 任一 ★ FAIL → WI-C 未完成。

---

## 1. 真实 DOM / 文案锚点速查（执行时判定用，已核实源码 2026-06-26）

| 元素 | 锚点 | 代码位置 | 判定用途 |
|---|---|---|---|
| 账户按钮（Toolbar） | `data-testid="relay-account-pill"`，icon=user，title=「账户设置」 | Toolbar.tsx:117-122 | TC-1 点它打开面板（**仅 relay edition 渲染**） |
| 面板 modal | `role="dialog" aria-modal="true" aria-label="账户设置"`，标题 `<h3>账户设置` | RelayEdition.tsx:216-237 | TC-1 面板出现的硬锚点 |
| email | header `<span>{user.email}</span>`（fontWeight 600） | AccountSettingsPanel.tsx:216 | TC-5 文本 == 登录账号 |
| 测试账号徽章 | `<span>测试账号</span>` 橙色（边框 `#fdba74` / 底 `#fff7ed` / 字 `#c2410c`），仅 `is_test_account===true` 渲染 | :217-219 + :516-525 | TC-3 可见性 + 颜色 |
| plan/id 副行 | `{user.plan ?? "prepaid"} · #{id 后6位}` | :221-224 | 旁证（区分账号） |
| 账户余额区标题 | `<h4>账户余额</h4>` | :237 | 定位余额区 |
| 钱包余额值 | `<dd>` = `formatMoney(balance.amount_minor, balance.currency||"USD")` | :305-308 / formatMoney :77-86 | **TC-2/TC-4 招牌** |
| 本月已用值 | `<dd>` = `formatMoney(period.used_minor, balanceCurrency)` | :310-313 | TC-6 |
| 速率上限值 | `<dd>` = `{rpm} req/min` 或 `—` | :318-323 | TC-6（无 ¥，是 req/min） |
| 充值按钮 | `data-testid="account-recharge-btn"` 文案「去充值 →」 | :247-255 | 定位锚（余额 `<dl>` 在它正上方） |
| 登出按钮 | `data-testid="account-logout-btn"` 文案「退出登录」 | :226-233 | 旁证 / 多账号切换用 |
| usage 拉取失败 | 余额区红字（`color:#b91c1c`），或 `UsageDetails` 收到 `null` → 「暂无用量数据。」 | :239-241 / :299-301 | 边界 B5（token 过期） |

**取 DOM 文本的可靠手段（减少肉眼 $/¥ 误判）**：用 windows-mcp `Scrape`（抓当前前台 webview 文本）或对桌宠 webview eval JS：
```js
// 取整个余额 dl 的纯文本，逐字核对货币符号
document.querySelector('[data-testid="account-recharge-btn"]')
  ?.closest('section')?.querySelector('dl')?.innerText
// 取 email + 徽章文本
document.querySelector('[role="dialog"][aria-label="账户设置"] header')?.innerText
```
> ⚠️ DOM eval **只是辅助**；每个 ★ case 仍须存一张真截图作物证（纪律 0.2.2/0.2.7）。
> ⚠️ `formatMoney` 缺 currency 时**缺省 USD**（`|| "USD"`）。所以即便后端没回 `balance.currency`，面板也应是 `$` —— 这正是修 bug 的核心，TC-2 必须看到 `$`。

---

## 2. 取坐标 & 通用操作模板（执行时每次重启后重填）

> 桌宠主窗 + Toolbar + modal 每次重启位置会漂。**每个 case / 每次重启前**先 `Screenshot`/`Snapshot` 取真坐标：

| 控件 | 占位坐标(重启后实测填) | 用途 |
|---|---|---|
| 登录框 email 输入 | `(email_x, email_y)` | 登录（首启 onboarding） |
| 登录框 密码 输入 | `(pw_x, pw_y)` | 登录 |
| 登录提交按钮 | `(login_x, login_y)` | 提交登录 |
| Toolbar 账户按钮 | `(acct_x, acct_y)` | 打开账户面板（icon=user，最左 Group0） |
| 面板关闭 ✕ | `(close_x, close_y)` | 关面板 |

**打开账户面板通用步骤**（后文各 case 引用为「打开账户面板」）：
1. `坐标=(acct_x,acct_y) | 动作=click | 期望=弹出 role=dialog aria-label="账户设置" 的 modal，标题「账户设置」`
2. 截图 → 确认面板出现（「账户余额」标题可见）。

---

## 3. 测试用例

> 每条：前置 → 操作步骤（declare 坐标|动作|期望）→ 判定标准（截图/DOM/日志）→ 能逼出的 bug。

---

### TC-1 ★ 登录 + 打开账户面板（前置链路，E2E-1 一半）

**前置**：§0.1 环境全绿，`VITE_AUTH_EDITION=relay`，桌宠已启动。

**步骤**
1. 截图 `TC-1-00-launch.png`，确认出现 relay 登录弹窗（`RelayAuthModal`）。若已是登录态（restoreSession 成功，直接进主界面），跳到第 5 步。
2. `坐标=(email_x,email_y) | 动作=click | 期望=email 输入框聚焦`，输入 dev 账号 email（剪贴板粘贴，勿截图账号）。
3. `坐标=(pw_x,pw_y) | 动作=click+type | 期望=密码框填入`（剪贴板，勿截图）。
4. `坐标=(login_x,login_y) | 动作=click | 期望=登录成功，弹窗关闭，进入桌宠主界面`。**登录成功后再截图** `TC-1-01-loggedin.png`（此时无账号密码可见）。
5. `坐标=(acct_x,acct_y) | 动作=click | 期望=Toolbar 账户按钮(relay-account-pill)点开，弹出账户面板`。
6. 截图 `TC-1-02-panel.png`。

**判定标准**
- ✅ PASS：截图出现 modal，标题「账户设置」，内有「账户余额」「账户安全」「已登录设备」三段；DOM `document.querySelector('[role="dialog"][aria-label="账户设置"]')` 存在。
- ❌ FAIL：点账户按钮无反应 / 无该按钮（→ 八成不是 relay edition，回 §0.1.2 检查 `VITE_AUTH_EDITION=relay`）/ 面板报错空白。

**能逼出的 bug**：relay edition 没接 `openAccountRef`、Toolbar Group0 没渲染账户按钮、面板挂载崩溃。

---

### TC-2 ★ 钱包余额显示为美元 `$X.XX`，面板无 `¥`（招牌 bug 回归）

**前置**：TC-1 面板已打开；账户余额区已加载完（非 loading 骨架、非红色错误。若一直 loading/error → token 过期，先重登，retry≥3 仍不行标 env-limited）。

**步骤**
1. 截图整个面板 `TC-2-01-panel-full.png`。
2. **放大截图余额区**（钱包余额那一行），单独截图 `TC-2-02-balance-zoom.png`，逐字核对货币符号。
3. DOM 取文本辅助核对：
   ```
   坐标=N/A | 动作=eval JS 取余额 dl.innerText | 期望=文本含 "钱包余额" + "$" 前缀数字，不含 "¥"/"￥"
   ```
   抓 `[data-testid="account-recharge-btn"]?.closest('section')?.querySelector('dl')?.innerText` 存为文本证据。

**判定标准**
- ✅ PASS：钱包余额值形如 `$756.14` / `$0.00`（`$` 开头，两位小数）；**面板任何位置（含余额区、本月已用、副行）都不出现 `¥` 或 `￥`**。
- ❌ FAIL：出现 `¥713.17` / `¥0.00` / `￥...`（招牌 bug 复发，旧 `formatCny` 残留或硬编码 `¥`）。
- 判定主依据 = 截图逐字 + DOM 文本双证；二者任一见 `¥` 即 FAIL。

**能逼出的 bug**：`formatCny` 未删干净、`CURRENCY_SYMBOLS` 缺省没落到 USD、某处硬编码 `¥` 字面量。

---

### TC-3 ★ 测试账号徽章（dev 是 `deskpettest+` 测试号）

**前置**：TC-1 面板已打开；用的是 §0.1 的 dev 测试账号（`deskpettest+...`）。

**步骤**
1. 截图面板 header 区（email 行）`TC-3-01-header.png`，放大。
2. DOM 辅助：eval `document.querySelector('[role="dialog"][aria-label="账户设置"] header')?.innerText` → 期望含 `测试账号`；并 eval 取徽章 `<span>` 的 `getComputedStyle().color` ≈ `rgb(194,65,12)`(=#c2410c) / background ≈ `rgb(255,247,237)`(=#fff7ed)。

**判定标准**
- ✅ PASS：email 文本右侧紧挨一个**橙色**「测试账号」徽章（圆角小标签，橙字橙底橙边）；DOM header 文本含「测试账号」。
- ❌ FAIL：无徽章（→ 后端 `/v1/me`/usage 没回 `is_test_account=true`，或前端判定写成宽松真值而非 `=== true` 时反而可能误显，本 case 要求严格 true 时显示）/ 徽章非橙色 / 徽章串到错误位置。

**能逼出的 bug**：`is_test_account` 字段没透传、徽章渲染条件写错、徽章样式漂移。
> 诚实标注：若 dev 账号服务端**没有**回 `is_test_account=true`（字段缺失/后端 PR-1 未上线），徽章不会显示 —— 这**不是前端 bug**。此时本 case 标 **env-limited（依赖后端字段）**，并在 §5 记录：「徽章渲染逻辑由 vitest 覆盖 `is_test_account===true → 显示 / undefined → 不显示`」。需 retry≥3（重登/换设备/确认 token 对应测试号）后才可标 env-limited。

---

### TC-5 email 正确显示（与登录账号一致）

**前置**：TC-1 面板已打开。

**步骤**
1. 截图 header `TC-5-01-email.png`。
2. DOM eval 取 `header span` 第一个文本，与 §0.1 登录用 email **逐字符比对**（比对在内存里做，**勿把账号截进图**；可只在文本日志记「email 匹配 = 是/否」不记明文，或记打码版 `de***@***`）。

**判定标准**
- ✅ PASS：面板 header 显示的 email == 登录账号 email（含大小写、`+` 子地址 `deskpettest+xxx`）。
- ❌ FAIL：显示 username/id 而非 email / 显示空 / 显示别的账号（多账号串号）。

**能逼出的 bug**：`user.email` 没透传、header 渲染取错字段、多账号切换后未刷新（与 WI-C §4 余额刷新挂点相关，但 email 来自 `currentUser()`/login 事件）。

---

### TC-4 ★ 余额为 0 时显示 `$0.00`（不是 `¥0.00`、不是 `—`）

**前置**：TC-1 面板已打开；dev 测试账号余额为 0（常态）。**若余额非 0** → 本 case 不可真测，降级 §5 B1（标 env-limited，单测覆盖）。

**步骤**
1. 确认 dev 账号余额确为 0（充值页/控制台旁证，或服务端 usage `balance.amount_minor==0`）。
2. 截图余额区 `TC-4-01-zero-balance.png`，放大。
3. DOM eval 取钱包余额 `<dd>` 文本。

**判定标准**
- ✅ PASS：钱包余额值 == **`$0.00`**（`$` + `0.00`）。
- ❌ FAIL：
  - `¥0.00` / `￥0.00` → 招牌 bug 残留（货币符号写死）。
  - `—`（破折号）→ `amount_minor` 被当成 null（0 与 null 没区分好；`formatMoney(0,...)` 应返回 `$0.00` 不是 `—`，因为 `0 != null` 且 `Number.isFinite(0)===true`）。
  - `$0` / `0` → 没走 `toFixed(2)`。

**能逼出的 bug**：`formatMoney` 把 `0` 误判成 nullish（若用 `!amount_minor` 而非 `amount_minor == null` 会把 0 吞成 `—`）、0 值时货币符号丢失。
> 这是 0 与 null 的经典边界，dev 测试账号 $0 余额正好真机命中，价值高。

---

### TC-6 ★ 本月已用 / 速率上限按 USD 渲染、无 `¥`

**前置**：TC-1 面板已打开，余额区加载完。

**步骤**
1. 截图整个「账户余额」`<dl>`（钱包余额 / 本月已用 / 下次重置 / 速率上限 四行）`TC-6-01-dl-full.png`。
2. DOM eval 取整个 `dl.innerText`，逐行核对。

**判定标准**
- ✅ PASS：
  - 「本月已用」值形如 `$X.XX`（与钱包余额同币种 `balanceCurrency`），**无 `¥`**。
  - 「速率上限」值形如 `N req/min` 或 `—`（**不是货币、不带 ¥**）。
  - 「下次重置」值形如 `YYYY-MM-DD` 或 `—`。
- ❌ FAIL：本月已用出现 `¥` / 速率上限被错误格式化成货币 / 任意行出现 `¥`/`￥`。

**能逼出的 bug**：`UsageDetails` 只改了钱包余额却漏了本月已用（两处都该用 `balanceCurrency`）；`period.used_minor` 用了别的币种。

---

## 4. 负向专项 — 面板全域 `¥` 扫描（招牌 bug 一票否决）

**前置**：TC-1 面板已打开并加载完。

**步骤**
1. 截全屏面板 `NEG-01-panel-full.png`。
2. DOM eval 全面板文本扫描：
   ```js
   const t = document.querySelector('[role="dialog"][aria-label="账户设置"]')?.innerText || "";
   ({ has_yen: /[¥￥]/.test(t), text: t })
   ```

**判定标准**
- ✅ PASS：`has_yen === false`（整个账户面板文本不含 `¥` U+00A5 / `￥` U+FFE5）。
- ❌ FAIL：`has_yen === true` → 定位是哪一处（余额 / 本月已用 / 其它硬编码），招牌 bug 未根除。

**说明**：这是 TC-2/TC-4/TC-6 的兜底全域扫描，把「某个不起眼角落还写着 ¥」一网打尽。

---

## 5. 边界 / 反例（标注：哪些可真测、哪些只能单测 / 构造）

> 以下场景**活账号难复现**，诚实标注「单测已覆盖 / 环境受限需构造」，不假装真点了。这些是 `formatMoney`/`UsageDetails` 的逻辑分支，由 vitest 覆盖（plan §WI-C「测试(vitest)」要求渲染 `$`/email/徽章/0 警示/currency 驱动符号）。

| ID | 场景 | 期望 | 可真测? | 处置 |
|---|---|---|---|---|
| **B1** | 余额非 0（dev 账号若被充值） | `$X.XX` | 部分（若恰好非 0） | 非 0 时 TC-2 仍真测 `$`；TC-4(0 值) 此时 **env-limited**，靠单测 `formatMoney(75614,"USD")==="$756.14"`。 |
| **B2** | 后端响应**缺 `balance.currency` 字段** | 缺省 USD → `$X.XX` | 否（活账号通常带 currency=USD） | **单测构造**：`formatMoney(123,undefined)==="$1.23"` + `UsageDetails` `balance.currency` 缺省走 `\|\| "USD"`。真机难构造（要后端故意删字段）。标 env-limited / 单测覆盖。 |
| **B3** | 非 USD currency（如 `CNY`）→ 应显 `¥` | `¥X.XX`（**此时 ¥ 是合法的**，因 currency 真的是 CNY） | 否（钱包早已迁 USD，无 CNY 钱包接口） | **单测构造**：`formatMoney(71317,"CNY")==="¥713.17"`。说明：本 phase 的 bug 不是「永远不能有 ¥」，而是「USD 账户被错误显示成 ¥」。CNY 显 ¥ 是对的。真机无 CNY 账号 → env-limited。 |
| **B4** | 未知 currency code（如 `XYZ`） | 前缀降级 `XYZ 1.23`（`CURRENCY_SYMBOLS[code] ?? `${code} ``） | 否 | 单测构造 `formatMoney(123,"XYZ")==="XYZ 1.23"`。env-limited。 |
| **B5** | `getUsage()` 失败（token 过期 / 网络） | 余额区红色错误文案，或 `UsageDetails(null)` → 「暂无用量数据。」 | 部分（可断网/用过期 token 构造） | 可真测：断网或塞过期 token 打开面板 → 截图红字 / 「暂无用量数据。」。注意 header 的 email/徽章**仍应显示**（来自 `currentUser()` 内存，不依赖 usage）。 |
| **B6** | **prod 账号（非测试号）无徽章** | email 旁**无**「测试账号」徽章 | 否（手头只有 dev 测试号） | 需 prod 账号构造。单测覆盖 `is_test_account===undefined/false → 不渲染徽章`。**env-limited（无 prod 账号）**，诚实标注不伪造。 |
| **B7** | `amount_minor == null`（usage 有但 balance 缺 amount_minor） | 钱包余额显 `—` | 否 | 单测 `formatMoney(null,"USD")==="—"` / `formatMoney(undefined,...)==="—"`。env-limited。 |

> **诚实口径**：B2/B3/B4/B6/B7 真机几乎不可复现（依赖后端故意构造异常响应或另备 prod 账号），**一律标 env-limited 并指向对应 vitest**，**不在真测里假装点过**。真机能稳定覆盖的核心是 TC-1~6 + NEG + B1(部分)/B5。

---

## 6. 真测结果（待执行填写）

> 执行时填。每行附截图相对路径（`plans/manual-results-2026-06-26-relay-provider-wiC/screenshots/<file>.png`）+ DOM/文本证据摘要。

| Case | ★ | 输入 / 操作 | 期望 | 实测结果 | 截图 | PASS/FAIL/env-limited |
|---|---|---|---|---|---|---|
| TC-1 | ★ | 登录→点账户按钮 | 弹出「账户设置」面板，三段可见 | | | |
| TC-2 | ★ | 看钱包余额 | `$X.XX`，面板无 `¥` | | | |
| TC-3 | ★ | 看 email 旁徽章 | 橙色「测试账号」 | | | |
| TC-4 | ★ | 看 0 余额 | `$0.00`（非 `¥0.00`/`—`） | | | |
| TC-5 | | 看 email | == 登录账号 | | | |
| TC-6 | ★ | 看本月已用 / 速率上限 | USD 渲染、无 `¥` | | | |
| NEG | | 全面板 `¥` 扫描 | 不含 `¥`/`￥` | | | |
| B1 | | 余额非 0（如有） | `$X.XX` | | | |
| B5 | | usage 失败 | 红字/「暂无用量数据。」，header email/徽章仍在 | | | |
| B2/B3/B4/B6/B7 | | 异常响应 / prod 账号 | 见 §5 | （env-limited，单测覆盖） | — | env-limited |

### 执行环境记录（填）
- 日期 / 执行人：
- backend_launch log（Dev python 非 Bundled）：
- `VITE_AUTH_EDITION`：relay
- dev 账号（打码）：`de***@***`，登录成功：是/否，token 是否过期重登：
- 余额实测值：`$____`（是否为 0）：
- `is_test_account` 服务端是否回 true：

### ★ 一票否决汇总
- TC-1 / TC-2 / TC-3 / TC-4 / TC-6 全 PASS = WI-C Phase A 真机通过，可勾 plan §WI-C + §WI-7 E2E-1（账户显示部分）。
- 任一 ★ FAIL（尤其 TC-2/TC-4 见 `¥`）= 招牌 bug 未根除，回 `AccountSettingsPanel.tsx` 修。
