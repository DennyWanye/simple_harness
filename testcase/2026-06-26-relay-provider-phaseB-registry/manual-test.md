# Relay Provider Phase B = WI-1 + WI-2 — registry 收编地基 手工测试（自包含·单文档可执行）

> **被测范围**：后端 LLM provider registry 为「收编 relay device key 成可管理 provider」打的**纯后端地基**（plan `plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md` §WI-1 + §WI-2）：
> - **WI-1**：`ProviderEntry` 加 `source: str = "user"` / `account_ref: str = ""` 两个字段（`backend/llm/provider_registry.py:145-146`）；toml writer 仅**非默认**才 emit 两行（`:242-245`）；loader 透传（`:354-355`）；`add_provider`/`update_provider` 透传两字段（`:482-483` / `:584-587`）；新增 `KeyMissingError`（`:111`）+ `ensure_provider` 幂等 upsert（`:597-628`，registry 空时 priority 严格小于现存 + key 缺失且未带 key → 抛 `KeyMissingError`）+ `_normalize_priorities`（`:659`）。
> - **WI-2**：WS `settings_providers_ensure` / `settings_providers_relay_logout` 两 handler，决策逻辑抽到 `backend/llm/relay_provider_ops.py`（`ensure_relay_provider` 只收 `source=="relay"`、`KeyMissingError → {reason:key_missing}`、日志只打 `key_fingerprint` sha256[:8] 不打明文；`relay_logout` disable + 清 account_ref + 删 keychain key）；`main.py` 只做 `ws.send_json` / 广播薄壳（`main.py:5803-5899`）。
>
> **关键认知（决定本文档结构）**：Phase B 是**纯后端**，**没有独立 UI**。「relay provider 自动出现在设置面板 + 受限三态 + 重置 key」依赖 **WI-3（registration 镜像推送）+ WI-4（UI）**，本 phase **尚未落地**。因此本文档分三块：
> 1. **§3 可现在真测（BC 回归）**：WI-1 给 registry 加了 `source`/`account_ref` 后，必须验证**既有「手填 provider」管理 UI 不受影响** —— 设置面板 → LLM Providers → 手填新增 / 启停 / 编辑 / 拖拽排序 / 删除一条普通 provider 仍正常，且其 toml 行**不被写入 `source`/`account_ref` 两行**（user 行字节不变）。这是 Phase B 在 live UI 上唯一能做的真机点击回归。
> 2. **§4 后端行为验证（pytest 已覆盖，列清单作旁证）**：WI-1 9 测 + WI-2 6 测各验什么，已 **39 passed**（`backend/tests/test_p5s2_provider_registry.py`）。
> 3. **§5 前向引用（延后集成真测）**：relay provider 收编出现在设置面板 + 受限三态 + 重置 key + 登出删 key —— 这些**收编行为的 E2E 在 WI-3/WI-4 集成真测覆盖**，本文档给出将来 TC 占位。
>
> **目的**：在桌宠真机 UI 上确认「WI-1 加 `source`/`account_ref` 两字段没破坏手填 provider 的增删改查拖拽 + 不污染 user 行 toml 字节」；并以 pytest 清单 + 前向引用诚实划清「哪些现在能真机点、哪些只能 pytest、哪些延后到集成」。
>
> **需 windows-mcp 真测**：是（触发词「手工测试 / 真测」已命中，本纪律强制生效）。判定 = 真坐标点击 + 真键盘/剪贴板输入 + 真截图 + 截图/DOM 文字判定 + （持久化项）开 `config.toml` 看真字节 / 抓 backend 日志，**不允许**用 `import` 调 `ensure_provider()` / `ws://127.0.0.1:8100` 直注 / 纯读源码 / pytest 当 UI 证据。
>
> **通过判据**：§0.3 全部 ★ 必过项 PASS —— 手填 provider 能增、能启停、能编辑、能拖拽、能删；其 `[[llm.endpoints]]` toml 行**不含 `source` / `account_ref` 两行**（user 行与改动前字节一致）；全程 backend 日志无 traceback。
>
> **对应 plan**：[plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md](../../plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md) §WI-1（:173-177）/ §WI-2（:179-182）/ §WI-7 E2E（前向，:262-272）。
> **最后更新**：2026-06-26

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 环境（必须先满足）

1. **进程清场**：`taskkill /F /IM deskpet.exe` + 杀残留 Vite node（项目坑 #1）。**不要手动起 backend / vite**（坑 #7/#9，Tauri 自管唯一 backend + 唯一 vite）。
2. **跑 worktree / master 当前后端代码（非 frozen）**：本 phase 改动**纯后端**（`provider_registry.py` / `relay_provider_ops.py` / `main.py`），必须让 Tauri 跑当前仓库的 backend（坑 #8）。**只给 Tauri 进程**注入 env：
   - `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend`
   - `DESKPET_PYTHON=G:\projects\deskpet\backend\.venv\Scripts\python.exe`
   - `DESKPET_DEV_MODE=1`
   - `DESKPET_USER_DATA_DIR=G:\projects\deskpet\backend\userdata`
   - `DESKPET_CLOUD_API_KEY=<根目录 .env 里的 tsk_ key>`（仅 chat 链路用；本 phase 手填 provider CRUD 不依赖它）
   - `NO_PROXY=*`（Clash 7897 掐空闲长连）
   - edition 不限：**手填 provider 管理 UI 在 manual 与 relay edition 都渲染**（`SettingsProviders` 始终挂载）。建议用 `VITE_AUTH_EDITION=manual` 跑本 phase（不必登录、更干净地隔离「手填 provider」这条路径，避免 relay 虚拟项混淆）。若用 relay edition，列表里会多一条只读 `chinzy · 中转站` 虚拟行（来自旧旁路 `relayListToChinzy`，**与本 phase 收编无关**，不要把它当成 WI-3 的产物）。
   启动后 log **必须**出现 `[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`；
   若见 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配**。
3. **日志落盘**：把 tauri dev 输出重定向到 `plans/manual-results-2026-06-26-relay-provider-phaseB/tauri-dev.log`（backend structlog 走 stderr → Tauri pipe → 落这份）。
4. **截图存盘目录**：`plans/manual-results-2026-06-26-relay-provider-phaseB/screenshots/`（不存在先建）。
5. **config.toml 备份**（TC-2 字节对比用）：测试前先复制一份当前 `backend/userdata/config.toml` 为 `config.toml.before`（人工或 `Copy-Item`），以便手填 provider 写入后 diff 出新增的 `[[llm.endpoints]]` 块、逐行确认无 `source`/`account_ref`。

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

> 触发词已命中（「手工测试 / 真测」）。本纪律强制生效，完整版见 `~/.claude/knowledge-base/windows-mcp-e2e.md`。

1. **真模拟人**：每个 case 必 `Screenshot`/`Snapshot` → **真坐标点击 / 真键盘(剪贴板)输入** → 截图验证 → 截图文字 / DOM / 日志 / toml 字节 判 PASS/FAIL。
2. **禁绕过（HARD）**：**不允许**用以下任一**替代**真点击作为 UI 证据 —— `import llm.provider_registry; await reg.ensure_provider(...)`、`ws://127.0.0.1:8100/*` WebSocket 直注 `settings_providers_add`、纯 `Read` 源码确认字段已加、pytest 跑 `test_p5s2_provider_registry.py`。**那些只能当辅助旁证（§4 已列），本 phase 的 PASS 必须建立在真打开设置面板 + 真点击 CRUD 按钮 + 真截图/真 toml 字节判定上。**（坑：`ws.send_json({"type":"settings_providers_add",...})` 直接灌一条 provider 再看列表**不算** TC PASS，那是协议层注入，违反 `feedback_real_e2e_not_script_replay`。）
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/drag/restart | 期望=...`。
4. **截图存盘**：`plans/manual-results-2026-06-26-relay-provider-phaseB/screenshots/<case-id>-NN-*.png`。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；**跳过任何 case** 必须显式声明 + 具体理由 + **等用户确认**。
6. **中文输入 workaround**（填 name / 核对中文文案时）：windows-mcp `Type`(UIA SetValue) 多数可靠；不落则 STA Runspace + `Clipboard.SetText("...")` + 先 Click 输入框聚焦再 Ctrl+V。
7. **拖拽真测**（TC-1d）：`SortableRow` 用 `@dnd-kit` PointerSensor，`activationConstraint.distance=4`（拖动 ≥4px 才激活）。windows-mcp 拖拽走 `Move`(起点)→`left_mouse_down`→`Move`(终点，≥4px)→`left_mouse_up`，中间至少一个中间点，否则 dnd-kit 不识别为 drag。判定看顺序变化 + backend 日志 `provider_registry: reordered`。

### 0.3 ★ Phase B 一票否决项（任一 FAIL = WI-1 BC 破，回 plan 修）

| Case | 验收点 | 类别 |
|---|---|---|
| **TC-1a** | 设置面板 → LLM Providers → 手填新增一条 provider（填 id/name/base_url/model/api_key）→ 列表出现该行 | ★ 手填新增不破（WI-1 字段未破 add） |
| **TC-2** | 该手填 provider 的 `config.toml [[llm.endpoints]]` 那条**不写 `source` / `account_ref` 两行**（user 行字节 = 改动前） | ★ 不污染 user 行（WI-1 writer 默认抑制） |
| **TC-1e** | 该手填 provider 能删除，列表与 toml 同步移除 | ★ 删除不破 |

> TC-1b（启停）/ TC-1c（编辑）/ TC-1d（拖拽排序）为同链路 BC 验证，非一票否决但必跑（任一异常即 WI-1 引入回归）。
> 任一 ★ FAIL → Phase B WI-1 引入了 BC 回归，未完成。

---

## 1. 真实 DOM / 文案锚点速查（执行时判定用，已核实源码 2026-06-26）

| 元素 | 锚点 | 代码位置 | 判定用途 |
|---|---|---|---|
| 设置齿轮（Toolbar） | `data-testid="settings-toggle"`，icon=settings，title=「设置」 | Toolbar.tsx:130 | 打开设置面板 |
| 设置面板 modal | `aria-label="设置"`，标题文本「设置」 | SettingsPanel.tsx:146 | 面板出现的硬锚点 |
| LLM Providers 区标题 | `<h3>LLM Providers</h3>` | SettingsPanel.tsx:219 | 定位 provider 区 |
| Providers 容器 | `data-testid="settings-providers"` | SettingsProviders.tsx:627 | provider 列表根 |
| 「+ 添加」按钮 | `data-testid="provider-add-button"`，文案「+ 添加」 | SettingsProviders.tsx:646 | TC-1a 打开新增 modal |
| 新增/编辑 modal | `role="dialog" aria-modal="true" aria-label="添加 provider"`（编辑时 `编辑 provider`） | AddProviderModal.tsx:280 | modal 出现锚点 |
| id 输入 | `data-testid="provider-id-input"`（编辑时 `disabled`） | AddProviderModal.tsx:309 | TC-1a 填 id |
| name 输入 | `data-testid="provider-name-input"` | AddProviderModal.tsx:324 | TC-1a 填 name |
| base_url 输入 | `data-testid="provider-base-url-input"` | AddProviderModal.tsx:338 | TC-1a 填 base_url |
| 新 model 输入 | `data-testid="provider-new-model-input"` | AddProviderModal.tsx:459 | TC-1a 填 model |
| 添加 model 按钮 | `data-testid="provider-add-model-button"`，文案「+ 添加」 | AddProviderModal.tsx:466 | TC-1a 提交 model |
| api_key 输入 | `data-testid="provider-api-key-input"`（type=password） | AddProviderModal.tsx:484 | TC-1a 填 key |
| 保存/添加 按钮 | `data-testid="provider-save-button"`，文案「添加」/「保存」 | AddProviderModal.tsx:503 | TC-1a 提交 |
| provider 行 | `data-testid="provider-row-<id>"`，`aria-label="provider <name>"` | SettingsProviders.tsx:339 | 列表行定位 |
| 启用 checkbox | `aria-label="启用 <name>"` | SettingsProviders.tsx:402 | TC-1b 启停 |
| 编辑按钮 | 行内「编辑」按钮 | SettingsProviders.tsx:408 | TC-1c 编辑 |
| 删除按钮 | 行内「删除」按钮（红字），点击弹 `window.confirm` | SettingsProviders.tsx:413 | TC-1e 删除 |
| 拖拽手柄 | `aria-label="拖拽 <name>"` 的 `⠿` span | SettingsProviders.tsx:346 | TC-1d 拖拽 |

**取 DOM 文本辅助手段**：用 windows-mcp `Scrape`（抓前台 webview 文本）或对桌宠 webview eval JS：
```js
// 列出当前所有 provider 行的 name + 顺序
[...document.querySelectorAll('[data-testid^="provider-row-"]')]
  .map(el => el.getAttribute('aria-label'))
```
> ⚠️ DOM eval **只是辅助**；每个 ★ case 仍须存一张真截图作物证。
> ⚠️ 删除走 `window.confirm("确认删除 provider \"<id>\"？")`（SettingsProviders.tsx:547）—— windows-mcp 真测时确认弹框需真点「确定」。

---

## 2. backend 日志锚点速查（toml 写入 / 持久化判定用）

| 事件 | 日志锚点 | 代码位置 | 判定用途 |
|---|---|---|---|
| 手填新增成功 | `provider_registry: added <id> (priority=N)` | provider_registry.py:497 | TC-1a 旁证 |
| 启停 | `provider_registry: set_enabled <id>=<bool>` | provider_registry.py:515 | TC-1b 旁证 |
| 拖拽重排 | `provider_registry: reordered [...]` | provider_registry.py:538 | TC-1d 旁证 |
| 删除 | `provider_registry: removed <id>` | provider_registry.py:507 | TC-1e 旁证 |
| relay 收编（本 phase **不应出现**） | `relay_provider_ensured id=relay-cloud ... key_fp=...` | relay_provider_ops.py:63 | 反证：手填路径不该触发 |

> ⚠️ 手填 provider 走 `settings_providers_add` → `add_provider`，**不经过** `ensure_relay_provider`。若本 phase 手测时日志出现 `relay_provider_ensured`，说明误触了 relay 收编路径（不该发生，本 phase 无 WI-3 推送源）。

---

## 3. 测试用例（§可现在真测 — BC 手机点击回归）

> 每条：前置 → 操作步骤（declare 坐标|动作|期望）→ 判定标准（截图/DOM/日志/toml）→ 能逼出的 bug。
> 取坐标：桌宠主窗 + 设置面板 + modal 每次重启位置会漂，**每个 case / 每次重启前**先 `Screenshot`/`Snapshot` 取真坐标。

---

### TC-1a ★ 手填新增一条 provider（WI-1 字段未破坏 add）

**前置**：§0.1 环境全绿，桌宠已启动，已备份 `config.toml.before`。

**步骤**
1. 截图主界面 `TC-1a-00-main.png`。
2. `坐标=(gear_x,gear_y) | 动作=click | 期望=点 Toolbar 设置齿轮(settings-toggle)，弹出 aria-label="设置" 面板`。截图 `TC-1a-01-settings.png`，确认面板出现、滚到「LLM Providers」区（`<h3>LLM Providers`）。
3. `坐标=(add_x,add_y) | 动作=click | 期望=点「+ 添加」(provider-add-button)，弹出 aria-label="添加 provider" modal`。截图 `TC-1a-02-modal.png`。
4. `坐标=(id_x,id_y) | 动作=click+type | 期望=id 框填入 "test-manual-bc"`（剪贴板或 Type）。
5. `坐标=(name_x,name_y) | 动作=click+type | 期望=name 框填入 "手填回归测试"`（中文剪贴板 workaround）。
6. `坐标=(url_x,url_y) | 动作=click+type | 期望=base_url 框填入 "https://api.example.com/v1"`。
7. `坐标=(model_x,model_y) | 动作=click+type | 期望=新 model 框填入 "test-model-1"`，再 `坐标=(addmodel_x,addmodel_y) | 动作=click | 期望=点「+ 添加」(provider-add-model-button)，model 进列表并自动设为默认 ◉`。
8. `坐标=(key_x,key_y) | 动作=click+type | 期望=api_key 框填入 "sk-test-bc-12345"`。
9. `坐标=(save_x,save_y) | 动作=click | 期望=点「添加」(provider-save-button)，modal 关闭，列表出现新行`。截图 `TC-1a-03-row-added.png`。

**判定标准**
- ✅ PASS：列表出现 `data-testid="provider-row-test-manual-bc"` 行，name 显示「手填回归测试」、副行显示 `test-model-1` + base_url + `API Key: ********`；DOM `document.querySelector('[data-testid="provider-row-test-manual-bc"]')` 存在；backend 日志出现 `provider_registry: added test-manual-bc (priority=...)`。
- ❌ FAIL：modal 报错 / 列表无新行 / 行渲染崩 / 日志出现 traceback（八成 WI-1 加字段后 `add_provider` 构造或 `to_public_dict` 出错）。

**能逼出的 bug**：WI-1 给 `ProviderEntry` 加 `source`/`account_ref` 后 `add_provider` 透传逻辑（`:482-483`）写错、`to_public_dict()`(`:175`) `asdict` 含新字段导致前端渲染异常、loader 回读崩。

---

### TC-2 ★ 手填 provider 的 toml 不写 `source` / `account_ref`（user 行字节不变）

**前置**：TC-1a 已成功新增 `test-manual-bc`。

**步骤**
1. `坐标=N/A | 动作=open file | 期望=打开 backend/userdata/config.toml`，定位 `id = "test-manual-bc"` 所在的 `[[llm.endpoints]]` 块。截图该块 `TC-2-01-toml-block.png`。
2. 与 `config.toml.before` diff（`git diff --no-index config.toml.before userdata/config.toml` 或人工逐行）：确认**仅新增**了一个 `[[llm.endpoints]]` 块，该块字段为 `id` / `name` / `base_url` / `models` / `default_model` / `api_key_ref` / `priority` / `enabled`，**没有** `source = "..."` 行、**没有** `account_ref = "..."` 行。

**判定标准**
- ✅ PASS：新增的 `test-manual-bc` 块**不含** `source` 与 `account_ref` 两行（因 `source=="user"`/`account_ref==""` 是默认值，writer `:242-245` 抑制输出）；其余既有 user provider 行字节与 before 完全一致。
- ❌ FAIL：
  - 出现 `source = "user"` 行 → writer 没做「仅非默认才 emit」（`_format_providers_section` `:242` 的 `if e.source != "user"` 失效）。
  - 出现 `account_ref = ""` 行 → 同理 `:244` 的 `if e.account_ref` 失效。
  - 既有 user 行被改写 / 重排字节 → 持久化污染。

**能逼出的 bug**：WI-1 writer 把默认值也写进 toml（user 配置无故膨胀两行 + 跨版本 diff 噪音）；这正是「BC 字节不变」的核心 —— 旧用户的手填 provider toml 不该因为 Phase B 加字段而变化。

> 旁证（非 UI 证据，仅交叉确认）：pytest `test_user_provider_omits_source_account_lines`（test_p5s2_provider_registry.py:432）钉死同一不变式。但本 ★ case 的 PASS 必须基于**真机点击新增后**的真实 `config.toml` 字节，pytest 只作旁证。

---

### TC-1b 手填 provider 启停（toggle enabled）

**前置**：TC-1a 行存在。

**步骤**
1. `坐标=(toggle_x,toggle_y) | 动作=click | 期望=点该行「启用」checkbox(aria-label="启用 手填回归测试")，取消勾选`。截图 `TC-1b-01-disabled.png`。
2. 再 `坐标=(toggle_x,toggle_y) | 动作=click | 期望=重新勾选启用`。

**判定标准**
- ✅ PASS：取消勾选后行背景变灰（`background:#f3f4f6`，SettingsProviders.tsx:317）；日志 `provider_registry: set_enabled test-manual-bc=False` 然后 `=True`；config.toml 该块 `enabled` 随之 false/true。
- ❌ FAIL：勾选状态不变 / 行不变灰 / 日志无 set_enabled / 报错。

**能逼出的 bug**：`set_enabled` 受 WI-1 字段影响（不应，但回归全链路）。

---

### TC-1c 手填 provider 编辑（改 name / base_url / model + 留空 key 保留）

**前置**：TC-1a 行存在。

**步骤**
1. `坐标=(edit_x,edit_y) | 动作=click | 期望=点该行「编辑」按钮，弹 aria-label="编辑 provider" modal，id 框 disabled 显示 "test-manual-bc"`。截图 `TC-1c-01-edit-modal.png`。
2. `坐标=(name_x,name_y) | 动作=click+type | 期望=name 改成 "手填回归测试-改"`。
3. api_key 框**留空**（编辑时留空 = 保留已存 key，AddProviderModal.tsx:482 文案「(留空保留已存的 key)」）。
4. `坐标=(save_x,save_y) | 动作=click | 期望=点「保存」(provider-save-button)，modal 关，行 name 更新`。截图 `TC-1c-02-edited.png`。

**判定标准**
- ✅ PASS：行 name 变为「手填回归测试-改」；config.toml 该块 name 更新、`api_key_ref` 不变、**仍无** `source`/`account_ref` 行；keychain key 未被改（留空不重写）。
- ❌ FAIL：name 没改 / id 可编辑（应 disabled）/ 编辑后 toml 莫名多出 source/account_ref 两行（WI-1 `update_provider` 的 `source`/`account_ref` 分支 `:584-587` 误把默认值塞进去）。

**能逼出的 bug**：WI-1 `update_provider` 加了 `source`/`account_ref` 分支后，编辑普通 provider 时若前端 patch 误带这两个 key 会写进 toml —— 但前端 `handleSaveDraft`(SettingsProviders.tsx:553) 的 patch 只含 name/base_url/models/default_model/api_key，不带 source/account_ref，故 user 行应保持干净。本 case 验证这条。

---

### TC-1d 手填 provider 拖拽排序（需 ≥2 条 provider）

**前置**：列表至少 2 条可拖拽 user provider。若只有 1 条，先用 TC-1a 流程再加一条 `test-manual-bc2`（id/model/key 换名）。

**步骤**
1. 截图当前顺序 `TC-1d-01-before.png`，DOM eval 记录 `provider-row-*` 顺序。
2. `坐标=(handle_x,handle_y) | 动作=drag | 期望=按住第一条的拖拽手柄 ⠿(aria-label="拖拽 ...")，移到第二条下方（≥4px 激活 + 中间点）再松手`。用 `Move`→`left_mouse_down`→`Move`(中间)→`Move`(目标)→`left_mouse_up`。截图 `TC-1d-02-after.png`。

**判定标准**
- ✅ PASS：两行顺序互换；DOM 顺序变化与截图一致；日志 `provider_registry: reordered [...]` 且 ids 顺序为新序；config.toml 各块 `priority` 重排 1..N。
- ❌ FAIL：顺序没变（拖拽未激活，retry 调大中间位移 / 确认按住的是 ⠿ 手柄而非整行）/ 日志无 reordered。

**能逼出的 bug**：`reorder` 受 WI-1 字段影响（不应）；拖拽全链路回归。
> 诚实标注：windows-mcp 模拟 dnd-kit 拖拽偶发不激活，**retry ≥3 次不同位移/速度**仍不动再标 env-limited（dnd-kit 真测对模拟拖拽敏感），并以日志 `reordered` 作主判据 + 截图佐证。

---

### TC-1e ★ 手填 provider 删除

**前置**：TC-1a 行 `test-manual-bc` 存在。

**步骤**
1. `坐标=(del_x,del_y) | 动作=click | 期望=点该行「删除」按钮，弹 window.confirm("确认删除 provider \"test-manual-bc\"？")`。截图 confirm `TC-1e-01-confirm.png`。
2. `坐标=(confirm_ok_x,confirm_ok_y) | 动作=click | 期望=点确认框「确定」，行从列表消失`。截图 `TC-1e-02-removed.png`。

**判定标准**
- ✅ PASS：列表不再有 `provider-row-test-manual-bc`；DOM 查询返回 null；日志 `provider_registry: removed test-manual-bc`；config.toml 该 `[[llm.endpoints]]` 块被移除（其余块保留）。
- ❌ FAIL：行还在 / confirm 没弹 / 日志无 removed / toml 块残留。

**能逼出的 bug**：`remove_provider` 受 WI-1 字段影响（不应）；删除全链路回归。

---

## 4. 后端行为验证（pytest 已覆盖 — 旁证，非 UI 证据）

> 以下为 WI-1 / WI-2 的核心后端行为，**已 39 passed**（`backend/tests/test_p5s2_provider_registry.py`，2026-06-26 复跑确认）。这些是 **pytest 才能稳定验证**的逻辑分支（keychain 脱敏、`KeyMissingError` 抛出、source 抑制不变式、relay-only 拒绝等），真机 UI 无独立入口可触发（依赖 WI-3 推送），故列清单作旁证，**不在真测里假装点过**。

### WI-1 — `ProviderEntry` 字段 + `ensure_provider` 幂等 upsert（9 测，test_p5s2_provider_registry.py:404-575）

| 测试 | 行 | 验什么 | 真机可点? |
|---|---|---|---|
| `test_source_and_account_ref_roundtrip_toml` | :404 | `source`/`account_ref` 写入→回读一致（非默认值 roundtrip） | 否（需带 source 写入，仅 relay 路径） |
| `test_user_provider_omits_source_account_lines` | :432 | user provider（默认 source/account_ref）toml **不写**两行 | **TC-2 真机覆盖**（pytest 作旁证） |
| `test_ensure_provider_idempotent` | :445 | `ensure_provider` 重复 upsert 同 id 幂等（不重复加行） | 否（依赖 WI-3 推送源） |
| `test_ensure_preserves_user_reorder` | :476 | ensure 更新时保留用户既有 reorder（priority 不乱） | 否 |
| `test_ensure_updates_key` | :498 | ensure 带新 key → 重写 keychain（换 key 生效） | 否 |
| `test_ensure_first_login_steals_default_priority` | :524 | 首次 ensure（registry 非空）priority 严格小于现存 → 排最前 | 否 |
| `test_ensure_raises_key_missing_when_keychain_empty` | :548 | 存在但 key 丢失且未带 key → 抛 `KeyMissingError` | 否（异常路径，仅 pytest） |
| `test_ensure_updates_base_url_models` | :575 | ensure 更新 base_url + models（meta 刷新） | 否 |
| （`source`/`account_ref` add 透传，含在 :404/:432 + add_provider 测） | — | add_provider 透传两字段 | 否 |

### WI-2 — WS `settings_providers_ensure` + `settings_providers_relay_logout`（6 测，test_p5s2_provider_registry.py:627-737）

| 测试 | 行 | 验什么 | 真机可点? |
|---|---|---|---|
| `test_ws_settings_provider_relay_messages_are_wired` | :627 | 两 msg_type 接进 main.py:5803 元组（薄壳已接电） | 否（接线断言） |
| `test_ws_ensure_rejects_non_relay_source` | :663 | `ensure_relay_provider` 拒绝 `source != relay`（返 `ensure_only_managed`） | 否 |
| `test_ws_ensure_relay_provider_succeeds_and_lists` | :674 | ensure 成功后出现在 list（source=relay/account_ref/key redacted） | **延后** WI-3/4 集成 E2E |
| `test_ws_ensure_key_missing_returns_error` | :690 | key 缺失 → 回 `{reason:key_missing}` 错误 payload | 否（异常路径） |
| `test_ws_ensure_never_logs_plaintext_key` | :707 | handler 永不打明文（caplog 断言只见 `key_fp` sha256[:8]） | 否（日志脱敏，仅 pytest） |
| `test_ws_relay_logout_disables_and_deletes_key` | :722 | relay_logout disable + 清 account_ref + 删 keychain key | **延后** WI-6/集成 |
| `test_ws_relay_logout_noop_when_absent` | :737 | relay row 不存在时 logout 幂等 no-op | 否 |

> **结论**：WI-1 + WI-2 后端行为已被 15 个针对性 pytest（含本文件其余 P5-S2 registry 测共 **39 passed**）钉死。本 phase 真机 UI 唯一能稳定覆盖的是 §3 的「手填 provider CRUD BC」+ TC-2「user 行不污染」；其余收编/脱敏/异常路径属 pytest 领域或延后到 WI-3/4 集成。

---

## 5. 前向引用（延后到集成真测 — WI-3/WI-4 落地后覆盖）

> Phase B 是地基。「收编行为」的真机 UI E2E **必须等 WI-3（registration 把稳定 device key 镜像进 registry）+ WI-4（设置面板 relay 受限三态 + 重置 key UI）落地后**才能整链路真测。本节给出**将来 TC 占位**，标注「Phase B 的收编行为 E2E 在 WI-3/WI-4 集成真测覆盖」，对齐 plan §WI-7 E2E-1~7（:265-272）。

| 将来 TC（占位） | 验收点 | 依赖 | 对齐 plan |
|---|---|---|---|
| **TC-FWD-1** | relay edition 登录后，设置面板「LLM Providers」**自动出现** `relay-cloud` 行（id=relay-cloud，name「中转站 · chinzy」，key readonly + relay 徽章） | WI-3 推送 + WI-4 UI | E2E-1（:265） |
| **TC-FWD-2** | 聊天 → backend 日志走 `provider_chain(relay-cloud)` 非旁路 + `key_fp=` + 真回复 | WI-3 镜像 + chat resolve | E2E-2（:266） |
| **TC-FWD-3** | 重启 app → relay-cloud 行在、device key 复用不变（同 key_fp，relay key 表不增） | WI-B 复用 + WI-3 | E2E-3（:267） |
| **TC-FWD-4** | relay 受限三态：可启停 / 可拖拽排序 / 选 default_model，但 key 不可编辑（readonly） | WI-4 UI | §目标 1（:52） |
| **TC-FWD-5** | 「🔄 重置 key」按钮 → `recover`（`?rotate=force` 重签）→ key_fp 变 → 聊天用新 key | WI-4 重置按钮 + WI-B force | E2E-6（:271） |
| **TC-FWD-6** | 登出 → relay row disable + account_ref 清空 + keychain key 删（`relay_logout` 经 WS 触发）→ 列表 relay 行消失或转登出态 | WI-3 onLogout + WI-2 relay_logout + WI-6 | E2E-4/E2E-7（:268/272） |
| **TC-FWD-7** | 多账号：登出 A → 登录 B → `account_ref` 变 B + chat `key_fp` == B ≠ A（不用 A 额度） | WI-3 account 切换 + WI-2 | E2E-4（:268） |

> **本 phase 对这些的贡献**：WI-2 已把 `ensure_relay_provider` / `relay_logout` 的决策逻辑就位并 pytest 钉死（§4 列），WI-1 已把 `source`/`account_ref` 字段 + `ensure_provider` 幂等地基就位。**收编一旦由 WI-3 推送 + WI-4 渲染，TC-FWD-1~7 即可整链路真机点击。** Phase B 自身在 live UI 上的真机证据 = §3 的 BC 回归（手填 provider 不受新字段影响）。

---

## 6. 边界 / 诚实口径（哪些只能 pytest、哪些能真机点、哪些延后）

| ID | 场景 | 期望 | 处置 |
|---|---|---|---|
| **E-1** | keychain 脱敏（list_providers api_key=`********`） | 列表行始终显 `********` | 真机可见（TC-1a 行 `API Key: ********`）+ pytest 兜底 |
| **E-2** | `KeyMissingError` 抛出（relay row 存在但 key 丢失且未带 key） | ensure 抛错 / WS 回 `key_missing` | **仅 pytest**（异常路径无 UI 入口，test:548/690） |
| **E-3** | `source`/`account_ref` 非默认值 roundtrip | 写入→回读一致 | **仅 pytest**（写入需走 relay 路径，本 phase 无 WI-3 源，test:404） |
| **E-4** | handler 永不打明文 key（只 `key_fp`） | caplog 无明文、只见 sha256[:8] | **仅 pytest**（日志断言，test:707）；真机旁证 = 抓 tauri-dev.log 全程无 `tsk_`/`sk-` 明文 |
| **E-5** | relay-only 拒绝（`settings_providers_ensure` 收 `source!=relay`） | 返 `ensure_only_managed` | **仅 pytest**（无 UI 发 ensure 入口，test:663）；将来 WI-3 才有真发送方 |
| **E-6** | ensure 幂等 / priority 抢默认 | 重复 upsert 不重复 + 排最前 | **仅 pytest**（依赖 WI-3 推送，test:445/524）；将来 TC-FWD-1/3 间接覆盖 |
| **E-7** | 手填 provider CRUD 不污染 user 行 | toml 无 source/account_ref 行 | **真机（TC-2 ★）** + pytest 旁证（test:432） |

> **诚实口径**：Phase B 大量行为属「relay 收编路径」，本 phase **没有 WI-3 推送源 + WI-4 UI**，故这些只能 pytest 或延后集成，**一律标 env-limited（依赖 WI-3/4）并指向对应 pytest / TC-FWD 占位，不在真测里假装点过**。真机能稳定覆盖的核心 = §3 手填 provider CRUD BC（TC-1a~1e）+ TC-2 user 行字节不污染。

---

## 7. 真测结果（待执行填写）

> 执行时填。每行附截图相对路径（`plans/manual-results-2026-06-26-relay-provider-phaseB/screenshots/<file>.png`）+ DOM/日志/toml 证据摘要。

| Case | ★ | 输入 / 操作 | 期望 | 实测结果 | 截图 | PASS/FAIL/env-limited |
|---|---|---|---|---|---|---|
| TC-1a | ★ | 真坐标点击新增手填 provider | 列表出现新行 + 日志 added | | | |
| TC-2 | ★ | 看 config.toml 该块 | 无 source/account_ref 行 | | | |
| TC-1b | | 启停 checkbox | 行变灰 + set_enabled 日志 | | | |
| TC-1c | | 编辑改 name（留空 key） | name 更新 + 仍无 source/account_ref | | | |
| TC-1d | | 拖拽排序 | 顺序互换 + reordered 日志 | | | |
| TC-1e | ★ | 删除（confirm） | 行消失 + removed 日志 + toml 块移除 | | | |
| E-1 | | 看行 API Key 列 | `********` | | | |
| E-4 | | 抓 tauri-dev.log 全程 | 无 tsk_/sk- 明文 | | | |
| TC-FWD-1~7 | | relay 收编整链路 | 见 §5 | — | — | **延后**（WI-3/4 集成 E2E） |
| E-2/E-3/E-5/E-6 | | 异常/收编路径 | 见 §6 | — | — | env-limited（仅 pytest，39 passed 旁证） |

### 执行环境记录（待填）
- 日期 / 执行人：
- backend_launch log：`[backend_launch] Dev python=...` ✅/❌（源码非 Bundled frozen）
- `VITE_AUTH_EDITION`：（建议 manual，隔离手填路径）
- config.toml.before 备份：✅/❌
- pytest 旁证：`test_p5s2_provider_registry.py` 39 passed（2026-06-26 复跑 ✅）
- 执行方式：windows-mcp OS 级 SendInput 真坐标点击 + 真截图 + config.toml 真字节 diff（非脚本回放、非 ws 直注、非 import ensure_provider）

### ★ 一票否决汇总（待填）
- TC-1a / TC-2 / TC-1e 全 PASS = WI-1 加 source/account_ref 字段**未破坏手填 provider 管理 + 未污染 user 行 toml** → Phase B BC 真机确认。
- relay 收编行为（TC-FWD-1~7）延后 WI-3/WI-4 集成真测；WI-1/WI-2 后端行为由 39 passed pytest 钉死作旁证。
