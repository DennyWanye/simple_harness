# PPT Pro — windows-mcp 真机手工测试用例

> **被测功能**：PPT Pro（F1 deepresearch 调研 → F2 拟大纲 → F3 大纲确认卡 → F4 gpt-image-2 惊艳生图 / 模板兜底）。
> **对应 plan**：[plans/2026-06-21-ppt-deepresearch-pro/00-PLAN.md](../../plans/2026-06-21-ppt-deepresearch-pro/00-PLAN.md)（v1.3 LOCKED，F1-F4 + WI-0~10）。
> **实现锚点**：`backend/deskpet/tools/ppt_tools.py`（`_handle_ppt_pro`@4509 / `_ppt_pro_orchestrate`@4375 / `_render_pro`@2334 / `_autofill_with_connectivity_gate`@2290 / `_degrade_to_template`@2265 / `_ppt_pro_cancel`@4329）、`backend/deskpet/tools/image_tools.py`（`probe_image_reachable`@154 / `_classify_image_error`@269）、`backend/main.py`（`_ppt_outline_propose`@3136 / `_broadcast_control`@3127 / `_handle_control_ws_message`@3191 / `_ppt_artifact_push`@3233 / `_expire_ppt_outline_dangling_for_startup`@3297）、`tauri-app/src/code-panel/PPTOutlineCard.tsx`。
> **最后更新**：2026-06-22

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 进程清场（防孤儿进程占端口）

1. 关闭桌宠，然后 `taskkill /F /IM deskpet.exe`（项目坑 #1：TaskStop 不清 deskpet.exe + Vite）。
2. 杀掉残留 Vite：`Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*tauri-app*' } | Stop-Process -Force`（或确认 5173/5373 端口空闲）。
3. **不要手动起 backend**（项目坑 #7：手动 `python main.py` 占 8100 → Tauri spawn backend 报 `os error 10048`）。
4. **不要手动起 vite**（项目坑 #9：`tauri dev` 的 `beforeDevCommand` 已自管唯一 vite，双 vite 抢 strictPort → 白屏）。

### 0.2 跑「当前 worktree 代码」而非 frozen exe（HARD GATE）

只给 **Tauri 进程**注入 env（不要手动起 backend）：

```
DESKPET_BACKEND_DIR = G:\projects\deskpet\backend
DESKPET_PYTHON      = G:\projects\deskpet\backend\.venv\Scripts\python.exe
DESKPET_DEV_MODE    = 1
```

启动后**必须**在 tauri dev log 里确认出现：
`[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`
如果看到 `[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**测了等于白测，立即停下重配 env**。

> backend structlog 全走 stderr → `Stdio::inherit()` → 落进 tauri dev 重定向 log。本文档所有「backend log 证据」都 grep 这份 tauri dev log。建议启动命令把输出重定向到 `plans/manual-results-2026-06-22-ppt-pro/tauri-dev.log`。

### 0.3 登录 / key / flag

- relay 已登录（dev 自动登录或手动 onboarding；测试凭据见 gitignored `LOCAL-DEV-CREDENTIALS.md`，token 常过期需重登）。
- gpt-image-2 key 已在 OS keychain（惊艳路径 TC-1/TC-9 必需）。
- dev config 默认即开 `[ppt].pro_enabled=true`（实现默认 True，见 `_ppt_pro_cfg`@128）。可用 `[ppt]` 段确认或显式写 `pro_enabled = true`。
- 默认档位 `pro_default_depth = "deep"`（6 子问题/2 轮反思，~5min）；`pro_research_timeout_s = 360`；`pro_confirm_timeout_s = 1800`（等用户看大纲）；`pro_outline_history = true`；`pro_save_research = true`。

### 0.4 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

1. **真模拟人**：每个 case 必须 Screenshot/Snapshot 抓状态 → **真坐标点击 / 真键盘输入** → 截图验证 → 肉眼或日志判 PASS/FAIL。
2. **禁绕过**：不允许用 WebSocket 直连 backend、pytest/acceptance 脚本、`import` 查内部状态、keychain/文件存在/boot log grep 当 UI 测试证据替代真点击（见根 CLAUDE.md「🔒 手工测试纪律」）。
3. **每个动作前 declare**：`坐标=(x,y) | 动作=click/type/drag | 期望=...`。
4. **截图存盘**：`testcase/2026-06-22-ppt-pro/screenshots/<case-id>.png`（或同步到 `plans/manual-results-2026-06-22-ppt-pro/screenshots/`）。
5. **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」。已知陷阱与圣杯 workaround（SendInput / Clipboard 中文粘贴 / DPI）见根 CLAUDE.md。
6. **跳过任何 case** 必须显式声明 + 给具体环境受限理由 + 等用户确认。

### 0.5 ★ 必过项（一票否决）

| Case | 验收点 | 为什么是一票否决 |
|---|---|---|
| **TC-1** | 全链路惊艳出图 | F1+F2+F3+F4 主路径，招牌链 |
| **TC-4** | 网络不可达回退模板（无占位残页 + 不二次烧图） | F4 核心，用户原诉求「连不上就用模板」 |
| **TC-5** | 4xx 模型不可用回退模板 | F4 第 2 层 gate（probe 200 但模型不可用） |
| **TC-9** | preempt 不杀确认链路 | R-4 BLOCKING 真机验证，独立 task 存活 |

任一 ★ FAIL → 不算完成，回 plan 修。

---

## 1. 真实锚点速查（执行时 grep / 肉眼比对用）

### 1.1 桌宠气泡文案（notifier，经 `_ppt_notify_chat_bubble`→`chat_response` WS）

| 阶段 | 真实文案（逐字） | 代码位置 |
|---|---|---|
| handler 秒回 | `收到，我先围绕这个主题做调研，拟好大纲会给你确认。`（status=`researching`） | ppt_tools:4566 |
| 同主题重复调 | `这份 PPT 已经在做了，等我把大纲或成品推回来。`（status=`already_running`） | ppt_tools:4532 |
| 换主题替换 | `好，换个主题，我重新来过。` | ppt_tools:4535 |
| 调研开始 | `🔍 正在围绕主题做深度调研…` | ppt_tools:4392 |
| 调研完成 | `📚 调研完成（N 个来源），正在拟大纲…` | ppt_tools:4401 |
| 调研无来源 | `📝 调研未取得来源，按通用知识拟大纲…` | ppt_tools:4403 |
| 大纲确认后 | `✅ 大纲已确认，开始生成…` | ppt_tools:4455 |
| probe 不可达回退 | `AI 配图暂时连不上，已切换模板生成。` | ppt_tools:2350 |
| 首图 gate 回退 | `gpt-image-2 暂时用不了，已切换模板生成。` | ppt_tools:2355 |
| 取消 | `好的，已取消，没有生成 PPT。` | ppt_tools:4439 |
| 改太多轮没定 | `大纲改了好几轮还没定，先暂停，需要再叫我。` | ppt_tools:4452 |
| 停止/取消 | `已停止当前 PPT 任务。` / `PPT 任务已停止。` | ppt_tools:4547/4476 |
| 成功自动打开 | `✨ PPT 做好啦，已自动打开：<path>` | ppt_tools:4359 |
| 失败 | `PPT 没做成：<error>` | ppt_tools:4370 |

> ⚠️ 注意：实现文案与 plan §8 / 旧种子 02-manual-test.md 措辞**不完全一致**（如回退文案没有「⚠️」前缀、没有「精美模板」字样）。判定以**本表逐字文案**为准（已 grep 源码）。

### 1.2 backend log 锚点（grep tauri dev log）

| 事件 | 锚点字符串 | 代码位置 | 含义 |
|---|---|---|---|
| 大纲卡决议被 resolve | `ppt_outline_decision_resolved outline_id=<oid>` | main.py:3207 | 用户点了卡的某动作且命中等待中的 Future |
| 决议无对应等待 | `ppt_outline_decision_no_pending outline_id=<oid>` | main.py:3209 | 卡已超时/已 resolve（死卡或重复点） |
| 启动清残留卡 | `ppt_outline_expired_dangling count=N` | main.py:3301 | 跨进程重启把 proposed→expired |
| image probe 不可达 | `image probe unreachable: <reason>` | image_tools:158/173 | F4 第 1 层探测判定不可达 |
| 首图生成失败 | `ppt_pro first image generation failed: <e>` | ppt_tools:2306 | 首图异常（connectivity） |
| 编排失败 | `ppt_pro orchestrate failed`（带 traceback） | ppt_tools:4479 | 编排 task 异常 |
| artifact 推送失败 | `ppt_artifact_push_send_failed` / `ppt_artifact_push_persist_failed` | main.py:3248/3262 | 成品卡推送/落库异常 |

### 1.3 WS 信封（control 通道，广播到所有 control peer）

```jsonc
// backend → 前端（弹卡）
{"type":"ppt_outline_proposed","payload":{"outline_id","topic","outline_md","session_id","sources_count","no_research","history":[...]}}
// 前端 → backend（用户决定）
{"type":"ppt_outline_decision","payload":{"outline_id","action":"accept|modify|cancel|reuse","feedback?","reuse_id?"}}
// backend → 前端（resolve 后清所有面板 stale 卡）
{"type":"ppt_outline_resolved","payload":{"outline_id"}}
// backend → 前端（成品卡，复用 tool_result 信封）
{"type":"tool_result","payload":{"tool":"ppt_pro","artifacts":[{kind:"file",...},{kind:"image",...}],"session_id"}}
```

### 1.4 大纲卡 UI 按钮（PPTOutlineCard.tsx，逐字）

- 卡标题：`PPT 大纲确认 · <topic>`
- 来源数：`📚 N 个调研来源`
- 无来源提示：`⚠️ 本次未取得调研来源，基于通用知识`
- 按钮：`✅ 确认生成` / `✏️ 修改`（展开 textarea「说说想改哪里」+ `提交修改`）/ `✖ 取消`
- 历史区：`📜 历史大纲`（折叠，列 topic+时间+来源数，点条目=reuse）
- 决议后：`已提交决定`
- ⚠️ **不显示生图费用**（用户决策 4）。

### 1.5 落盘位置

- PPT 成品：安装目录 `OutPut/PPT/*.pptx`（dev = repo 根）；预览 PNG 同侧。
- 调研报告（`pro_save_research=true`）：`DeepResearch/<slug>.md` + `DeepResearch/index.md` 倒序索引。

---

## 2. 测试用例

> 每条格式：case-id / 前置 / 坐标&动作（声明式）/ 期望 / backend log 证据 / 判定标准。
> 坐标占位 `(x,y)` 执行时按真机 Snapshot 实测填，报告里写真实物理像素。

---

### TC-1 ★ 惊艳全链路主路径（F1+F2+F3+F4）

**前置**：§0 全部满足；gpt-image-2 key 在 keychain；网络可达 relay。

**步骤**
1. `坐标=(桌宠输入框 x,y) | 动作=click | 期望=输入框聚焦`
2. `动作=Clipboard SetText "帮我做一份『钠离子电池 2025 产业现状』的惊艳 PPT，8 页" → Ctrl+V → Enter | 期望=消息发出`
3. 截图 `TC-1-01-sent.png`
4. 等待 ~3-5min（deep 档调研）；其间观察气泡进度。
5. 大纲确认卡弹出后截图 `TC-1-02-outline-card.png`
6. `坐标=(卡内「✅ 确认生成」按钮 x,y) | 动作=click | 期望=按钮消失、出现「已提交决定」`，截图 `TC-1-03-accepted.png`
7. 等待生图+渲染；成品卡出现后截图 `TC-1-04-artifact.png`；WPS 自动打开后截图 `TC-1-05-pptx.png`

**期望**
1. 桌宠**秒回**（<3s）`收到，我先围绕这个主题做调研，拟好大纲会给你确认。`
2. 依次出现气泡 `🔍 正在围绕主题做深度调研…` → `📚 调研完成（N 个来源），正在拟大纲…`（N>0）。
3. 弹出**大纲确认卡**（标题 `PPT 大纲确认 · 钠离子电池 2025 产业现状`），含**多行大纲**（页码+标题+bullets，内容含调研里的真实数据/事实，非空泛模板话），`📚 N 个调研来源`。
4. 点确认后气泡 `✅ 大纲已确认，开始生成…`。
5. 出现成品「打开/在文件夹中显示」卡（来自 `tool_result` 信封），气泡 `✨ PPT 做好啦，已自动打开：...OutPut\PPT\*.pptx`。
6. WPS 打开的 deck 是 AI **整页配图**惊艳风（首页/内容页满版图，无「image placeholder」深色占位）。

**backend log 证据**（grep tauri dev log）
- deepresearch 真调用：搜索 query 拆解 + `coverage` 含 `n_sources` > 0（`research_tools` coverage:1816）；`pro_save_research` 开 → `DeepResearch/<slug>.md` 新文件落盘。
- 多次 `POST .../images/generations` 真 200（gpt-image-2 出图）。
- `ppt_outline_decision_resolved outline_id=<oid>`（点确认命中 Future）。
- **无** `image probe unreachable` / **无** 回退文案。

**判定标准**：PASS = 秒回 + 调研真发生(n_sources>0) + 大纲有据(含真实数据) + 卡可见可点 + 点确认后真出图惊艳 deck 落盘 + 自动打开 + 成品卡出现。任一缺失 = FAIL。

---

### TC-2 修改环 · 增量改不整盘重拟（F3「可改」防整盘重拟）

**前置**：TC-1 弹出大纲确认卡后**不点确认**。先肉眼记录当前卡里**第 1/2/4...页**的标题（用于对比「其余页未变」）。

**步骤**
1. `坐标=(卡内「✏️ 修改」按钮 x,y) | 动作=click | 期望=展开 textarea「说说想改哪里」`，截图 `TC-2-01-edit-open.png`
2. `坐标=(textarea x,y) | 动作=click 聚焦`
3. `动作=Clipboard SetText "把第 3 页换成与磷酸铁锂的竞品对比，其余页不要动" → Ctrl+V | 期望=文本落入 textarea`，截图 `TC-2-02-feedback.png`
4. `坐标=(「提交修改」按钮 x,y) | 动作=click | 期望=卡进入「已提交决定」，编排 task 重拟`
5. 等待重拟，新卡弹出后截图 `TC-2-03-new-card.png`
6. `坐标=(新卡「✅ 确认生成」x,y) | 动作=click`

**期望**
1. backend log：`ppt_outline_decision_resolved`（action=modify）；编排 task 调 `_draft_outline_from_research(feedback=..., prev_slides=...)`（ppt_tools:4441）重拟。
2. **新确认卡**：第 3 页变为「与磷酸铁锂的竞品对比」；**第 1/2/4... 页标题与上一版基本一致**（验证增量修订非整盘推翻）。
3. 点确认后正常进入生成（气泡 `✅ 大纲已确认，开始生成…`）。

**backend log 证据**：`ppt_outline_decision_resolved outline_id=<oid1>`（首卡 modify）→ 重拟 → 第二张 `ppt_outline_proposed`（新 oid）→ `ppt_outline_decision_resolved outline_id=<oid2>`（accept）。

**判定标准**：PASS = 第 3 页按要求改 + 其余页未被推翻（逐页标题比对一致）+ 再确认后生成。若其余页大面积变动 = FAIL（整盘重拟回归）。

---

### TC-3 取消（F3 取消路径）

**前置**：弹出大纲确认卡。

**步骤**
1. `坐标=(卡内「✖ 取消」按钮 x,y) | 动作=click | 期望=卡进入「已提交决定」`，截图 `TC-3-01-cancel.png`

**期望**
1. 桌宠气泡 `好的，已取消，没有生成 PPT。`
2. `OutPut/PPT/` 无新 .pptx 文件（取消前后 `ls` 对比）。
3. 编排 task 干净结束，log **无** `ppt_pro orchestrate failed` / 无 traceback。

**backend log 证据**：`ppt_outline_decision_resolved outline_id=<oid>`（action=cancel）；之后无 images/generations 调用。

**判定标准**：PASS = 不生成 + 友好提示 + 无报错。

---

### TC-4 ★★ 网络不可达回退模板（F4 一票否决）

**前置**：制造 relay images **整体不可达**（三选一，按可行性，记录用了哪种）：
- (a) 改 dev config `[image].base_url` 指向错地址（如 `http://127.0.0.1:1/v1`）后重启桌宠；
- (b) 关代理 + 断网使 relay 不可达（注意 `_trust_env_proxy` 默认 False 直连，需真断到 relay）；
- (c) 临时把 `[image].base_url` 指向一个会拒连的端口。

> 目的是让 `probe_image_reachable`（GET `<base>/v1/models`）返回 False（第 1 层 gate）。

**步骤**
1. 输「做一份『量子计算入门』惊艳 PPT，6 页」→ Enter，截图 `TC-4-01-sent.png`
2. 等大纲卡弹出 → 点「✅ 确认生成」，截图 `TC-4-02-accepted.png`
3. 等渲染完成，成品卡 + WPS 打开后截图 `TC-4-03-template-deck.png`（逐页翻，确认无占位残页）

**期望**
1. backend log：`image probe unreachable: <reason>` → `_render_pro` 切 `use_template=True`。
2. 桌宠气泡 `AI 配图暂时连不上，已切换模板生成。`
3. 走模板路径出**完整美观 deck**：每页有充实 bullets（WI-2 双模式产出），**无占位残页**、**无「image placeholder」深色 IMG 徽章页**。
4. **不二次烧图**：`_degrade_to_template` 清了 image_prompt + `skip_image_gen=True` → log **无任何** `images/generations` 调用（全程 0 次，连首图都不发，因为 probe 先挡住）。

**backend log 证据**：`image probe unreachable` 出现 1 次；**0 次** `images/generations`；最终成品卡/`✨ PPT 做好啦`。

**判定标准**：PASS = 检测到不可达 + 回退模板 + 出完整无占位 deck + **0 次生图调用**。任一失败（出占位残页 / 仍发生图请求 / 没回退报错）= FAIL。**这是 F4 一票否决项。**

> 测后**务必还原** `[image].base_url` / 网络，重启桌宠确认恢复正常（影响后续 case）。

---

### TC-5 ★ 4xx 模型不可用回退模板（F4 第 2 层 gate）

**前置**：relay 在（probe 会 200 通过第 1 层），但 gpt-image-2 模型不可用 —— 改 dev config `[image].model` 为一个不存在的模型名（如 `gpt-image-does-not-exist`）后重启桌宠。

> 目的是让第 1 层 probe `/v1/models` 仍 <500（relay 活着），但**首图实测**返回 4xx → `_classify_image_error` 归为 `model_unavailable`（image_tools:298-312）→ `_should_fallback` 触发回退。

**步骤**
1. 输「做一份『城市绿色出行』惊艳 PPT，5 页」（普通主题即可）→ Enter
2. 等大纲卡 → 点「✅ 确认生成」，截图 `TC-5-01-accepted.png`
3. 等渲染，成品卡 + WPS 打开后逐页翻，截图 `TC-5-02-template-deck.png`

**期望**
1. backend log：**无** `image probe unreachable`（probe 通过）；**有** 1 次首图 `images/generations` 返回 4xx；分类为 `model_unavailable`（可在 `_autofill_with_connectivity_gate` 路径见首图失败）。
2. 桌宠气泡 `gpt-image-2 暂时用不了，已切换模板生成。`（注意是这条，区别于 TC-4 的 probe 文案）。
3. 出完整模板 deck，**无占位残页**。
4. **首图后不再继续烧其余图**（首图 gate 判定不可达即停）→ images/generations 只 1 次（首图），其余页走模板。

**backend log 证据**：probe 通过（无 unreachable）；`images/generations` 恰 1 次（首图）且 4xx；气泡 `gpt-image-2 暂时用不了…`。

**判定标准**：PASS = probe 过但首图 4xx 判 model_unavailable → 回退模板 + 仅 1 次生图尝试 + 完整无占位 deck。**一票否决项。**

> 测后还原 `[image].model` 重启。

---

### TC-6 内容/safety 失败不误伤（F4 不整副回退）

**前置**：relay + gpt-image-2 均正常。让**个别页**的 image_prompt 触发 content/safety 4xx（难稳定构造，可 best-effort：主题里塞一页明显敏感描述；或临时在 image_tools 注入「特定 prompt 命中即返回 content 4xx」的 dev 钩子）。

> 实现：`_classify_image_error` 把 content_policy/safety 归 `content`（image_tools:303），`_should_fallback` 只对 connectivity/model_unavailable 回退 → content 失败该页占位降级、**不整副回退**。

**步骤**
1. 触发后输惊艳 PPT 主题 → 确认大纲 → 生成。

**期望**
1. backend log：个别页生图返回 `content` 类失败，其余页正常 200 出图。
2. **不回退模板**（仍走惊艳路径）；失败那页占位降级，其余页正常 AI 配图。
3. 无 `gpt-image-2 暂时用不了` / `AI 配图暂时连不上` 文案。

**判定标准**：PASS = 个别 content 失败 → 该页占位、整副仍惊艳（不回退）。若整副回退模板 = FAIL（误伤）。
**若无法稳定构造 content 4xx**：声明环境受限 + 等用户确认（按 §0.4.6）。

---

### TC-7 调研降级知情确认（F1 全 0 源/超时）

**前置**：让 deepresearch 取不到来源（断网搜索，或临时把 `pro_research_timeout_s` 改很小如 `5` 逼超时）后重启桌宠。

> 实现：research 网络类失败 → `_research_topic_for_ppt` 返回 None（不上抛）；编排走「无来源」分支，确认卡 `no_research=true`。

**步骤**
1. 输「做一份『城市夜间经济』PPT，6 页」→ Enter
2. 等大纲卡弹出后截图 `TC-7-01-no-research-card.png`
3. 点「✅ 确认生成」继续。

**期望**
1. 气泡出现 `📝 调研未取得来源，按通用知识拟大纲…`（不是「📚 调研完成」）。
2. 大纲卡显示 `📚 0 个调研来源` + **黄字提示** `⚠️ 本次未取得调研来源，基于通用知识`。
3. 用户点确认后**仍能出 PPT**（功能不缺，只是无来源）。

**backend log 证据**：research 超时/0 源（`research timeout` 类 warning 或 coverage n_sources=0）；编排走 `report is None` 分支。

**判定标准**：PASS = 用户被明确告知无来源（卡上黄字 + 气泡）+ 知情后可继续生成。若静默当「正式调研完成」= FAIL。

> 测后还原 `pro_research_timeout_s` / 网络。

---

### TC-8 配置/认证错误上抛不静默编（F1）

**前置**：临时清掉 LLM key / 让 `_resolve_default_llm_call` 拿不到 key（如临时改坏 llm_runtime.json 或清 keychain LLM 项）后重启桌宠。

> 实现：`_is_config_or_auth_error`（ppt_tools:466）命中 401/403/key 缺失 → research 上抛 → 编排 `except` 走 `_ppt_pro_report_done(ok=False)`。

**步骤**
1. 输「做一份『AI 教育应用』PPT，6 页」→ Enter，截图 `TC-8-01-config-err.png`

**期望**
1. 桌宠气泡 `PPT 没做成：<错误>`（明确报错），**不**弹出一张凭空编的大纲卡假装调研完成。
2. backend log：`ppt_pro orchestrate failed`（带配置/认证异常 traceback）。

**判定标准**：PASS = 明确报配置/认证问题，不假装调研、不静默出大纲。若仍弹出大纲卡正常往下走 = FAIL（静默编）。

> 测后还原 LLM key / llm_runtime.json，重启确认恢复。

---

### TC-9 ★ preempt 不杀确认链路（R-4 BLOCKING 真机验证）

**前置**：TC-1 弹出大纲确认卡后**先不点**。

**步骤**
1. `坐标=(桌宠输入框 x,y) | 动作=click 聚焦`
2. `动作=Clipboard SetText "现在几点" → Ctrl+V → Enter | 期望=触发 same-sid 新 chat task`，截图 `TC-9-01-interrupt-msg.png`
3. 等桌宠回答「现在几点」后截图 `TC-9-02-answered.png`
4. 回到**仍在的大纲卡**，截图 `TC-9-03-card-alive.png`
5. `坐标=(卡内「✅ 确认生成」x,y) | 动作=click | 期望=编排 task 仍存活、继续出图`
6. 等成品出来，截图 `TC-9-04-still-generates.png`

**期望**
1. 新消息「现在几点」被正常回答（chat task 正常）。
2. **大纲确认卡仍在、按钮仍可点**（未变灰、未消失）。
3. 点确认 → 编排 task 仍存活，继续走生成（气泡 `✅ 大纲已确认，开始生成…` → 出图 → 成品卡）。

**backend log 证据**：新 chat task 起来/结束的常规 log；之后点确认仍 `ppt_outline_decision_resolved outline_id=<oid>`（**命中 Future**，证明编排 task + waiter 未被 preempt cancel）；**不是** `ppt_outline_decision_no_pending`（那说明卡变死卡 = FAIL）。

**判定标准**：PASS = 新消息不杀确认链路 + 卡可点 + 点后 resolve 命中 + 继续出图。若点确认后 log 出 `ppt_outline_decision_no_pending` 或编排静默死亡 = FAIL。**这是 R-4 BLOCKING 真机验证，一票否决。**

---

### TC-10 去重 / 替换（same-sid 并发控制）

**前置**：弹出某主题（设为「主题 A」）的大纲卡且**未决**（编排 task running 中）。

**步骤（10a 同主题 → already_running）**
1. `动作=type 完全相同的请求文案再发一次（同主题 A 同措辞）→ Enter`，截图 `TC-10a-01.png`

**期望 10a**：桌宠回 `这份 PPT 已经在做了，等我把大纲或成品推回来。`（status=already_running）；**不**起第二个编排 task、**不**弹第二张卡。

**步骤（10b 不同主题 → 取消旧换新）**
2. 在 10a 之后（旧 A 仍未决），`动作=type 一个不同主题 B 的请求 → Enter`，截图 `TC-10b-01.png`

**期望 10b**：桌宠先回 `好，换个主题，我重新来过。` → 旧 A 编排 task 被 cancel（旧 A 卡变 stale/`ppt_outline_resolved` 或超时）→ 起新 B 编排（`🔍 正在围绕主题做深度调研…` for B）→ 弹 B 的大纲卡。

**backend log 证据**：10a 无新 `ppt_outline_proposed`；10b 旧 A 被 `_ppt_pro_cancel`（CancelledError 穿透），新 B `ppt_outline_proposed`（新 oid，topic=B）。

**判定标准**：PASS = 同主题挡住(already_running) + 异主题取消旧换新（只剩 B 在跑）。

> 注：去重按 `_session_id` + topic 字符串精确比对（ppt_tools:4528）。同主题需**措辞完全一致**才命中 already_running；措辞不同会被当异主题替换 —— 这是已知边界，报告里如实记。

---

### TC-11 停止取消后台任务（/停止）

**前置**：弹卡前的**调研阶段**（气泡停在 `🔍 正在围绕主题做深度调研…`），或确认后的**渲染阶段**。

**步骤**
1. 找到桌宠的「停止」按钮 / 或发 `/stop`（按现有 chat preempt/停止 UI），`坐标=(停止按钮 x,y) | 动作=click`，截图 `TC-11-01-stop.png`

**期望**
1. 后台 ppt_pro 编排 task 被取消（`_ppt_pro_cancel` → CancelledError）。
2. 桌宠气泡 `已停止当前 PPT 任务。` 或 `PPT 任务已停止。`
3. 不再继续调研/出图；`OutPut/PPT/` 无半成品。

**backend log 证据**：编排 task CancelledError 路径（ppt_tools:4475/4546）；之后无新 images/generations、无新 ppt_outline_proposed。

**判定标准**：PASS = 停止后后台真停 + 友好提示 + 无残留产物。
**若桌宠当前 UI 无法对 ppt_pro 后台 task 触发停止**（停止只作用于 chat task）：声明环境受限 + 等用户确认，并记录实测行为。

---

### TC-12 历史复用 + reload 持久化（F3 决策 2）

**前置**：已完成 TC-1（一次成功的 PPT，历史里有「钠离子电池 2025 产业现状」条目，`pro_outline_history=true`）。

**步骤（12a 历史复用）**
1. 发一个**新主题** PPT 请求 → 等新大纲卡弹出
2. `坐标=(卡内「📜 历史大纲」x,y) | 动作=click 展开 | 期望=列出上次「钠离子电池…」条目`，截图 `TC-12a-01-history.png`
3. `坐标=(历史条目「钠离子电池 2025 产业现状」x,y) | 动作=click | 期望=action=reuse 直接用历史大纲进入生成`，截图 `TC-12a-02-reuse.png`

**期望 12a**：点历史条目发 `ppt_outline_decision{action:reuse, reuse_id}` → 编排取回历史 slides（ppt_tools:4433-4437）→ 直接进 `✅ 大纲已确认，开始生成…`（不再重新调研拟纲）。

**步骤（12b 同进程 reload 持久化）**
4. 弹出某主题大纲卡（未决）后，在桌宠/code-panel **reload 页面**（不重启 backend 进程，仅前端刷新），截图 `TC-12b-01-after-reload.png`

**期望 12b**：reload 后**大纲卡仍在且仍可点**（前端 `set_messages` 保留内存 awaiting 卡）；点确认仍 `ppt_outline_decision_resolved`（命中 Future）。

**backend log 证据**：12a `ppt_outline_decision_resolved`（reuse）+ 取 `ppt_outline_history` 记录；12b reload 后点确认仍命中 Future（非 no_pending）。

**判定标准**：PASS = 历史可见可复用（reuse 直接生成）+ 同进程 reload 卡不丢可点。

**12c 诚实边界（跨进程重启，非死卡）**
5. 弹卡未决 → **完全重启 backend 进程**（关桌宠 taskkill + 重开）→ reload。

**期望 12c**：重启后**不再有可点的 awaiting 死卡**；启动 log 出 `ppt_outline_expired_dangling count=N`（proposed→expired）；该大纲仍能在新会话的「📜 历史大纲」里**作为可 reuse 历史**出现（不是冒充等待中的卡）。

**判定 12c**：PASS = 重启后无死卡 + 有 expired log + 大纲仍可作历史 reuse。这是诚实标注的边界（awaiting 卡只在同进程存活）。

---

### TC-13 BC · 旧 ppt_create 不受影响

**前置**：§0 满足。

**步骤**
1. 输「快速做个 3 页朴素 PPT，关于咖啡冲煮入门，不用调研也不用配图」→ Enter（引导 LLM 走 `ppt_create` 而非 `ppt_pro`），截图 `TC-13-01.png`
2. 另测「直接用这个大纲做 PPT：[贴一份简单 outline]」走 `ppt_create` outline 路径。

**期望**
1. 走旧 `ppt_create` 路径（log 见调 `ppt_create`，**不是** `ppt_pro`）；行为与改造前一致（同步/异步、模板/生图照旧，`skip_image_gen` 默认 False → 字节 BC）。
2. 不弹大纲确认卡（ppt_create 无确认环）。

**backend log 证据**：tool call = `ppt_create`；无 `ppt_outline_proposed`。

**判定标准**：PASS = 旧路径行为不变（不被 ppt_pro 改造污染）。

---

### TC-14 边界与健壮性

逐子项执行，各自截图 `TC-14-<子项>.png`。

| 子项 | 输入 | 期望 |
|---|---|---|
| 14a pages 极小 | 「做一份『光伏发电』PPT，3 页」 | 正常出 3 页（pages 下限 3，`_coerce_ppt_pro_args` clamp 3-20） |
| 14b pages 极大 | 「做一份『中国高铁发展史』PPT，20 页」 | 出 20 页（上限 20）；render 超时按 `max(600, pages*120)` 给足预算不被杀 |
| 14c pages 越界 | 「做一份 PPT，100 页」 | clamp 到 20，不崩 |
| 14d 超长中文主题 | 一段 200+ 字的超长中文主题（描述性长句） | 不崩；调研/大纲/slug 截断合理，正常弹卡 |
| 14e 乱码主题 | 「做一份 PPT：������\x00乱码￥%……」 | 不崩；要么正常降级出大纲，要么友好报错，**无 traceback 致编排死亡** |
| 14f image_mode=false | 「做一份『茶文化』PPT，6 页，**用模板不要 AI 配图**」（引导 image_mode=false） | 直接走模板路径（`_render_pro` use_template=True from start），**不调** probe、**不调** images/generations，出完整模板 deck |

**backend log 证据**：14f log **无** `image probe`、**无** images/generations；14b 渲染未被 60s/registry 超时杀（编排自管 render_timeout）。

**判定标准**：PASS = 各边界不崩 + 行为符合预期（clamp / 截断 / 模板直出）。任一崩溃或 traceback 致编排静默死亡 = FAIL。

---

## 3. 结果汇总表（执行后填）

| case | 维度 | 坐标 | 动作摘要 | 截图 | log 证据 | 判定 |
|---|---|---|---|---|---|---|
| TC-1 ★ | 惊艳全链路 | | | | | |
| TC-2 | 修改环防整盘重拟 | | | | | |
| TC-3 | 取消 | | | | | |
| TC-4 ★ | 网络不可达回退 | | | | | |
| TC-5 ★ | 4xx 模型不可用回退 | | | | | |
| TC-6 | content 不误伤 | | | | | |
| TC-7 | 调研降级知情 | | | | | |
| TC-8 | 配置错上抛 | | | | | |
| TC-9 ★ | preempt 不杀链路 | | | | | |
| TC-10 | 去重/替换 | | | | | |
| TC-11 | 停止取消后台 | | | | | |
| TC-12 | 历史复用+持久化 | | | | | |
| TC-13 | BC ppt_create | | | | | |
| TC-14 | 边界(a-f) | | | | | |

---

## 4. 通过线

- **★ 必过（一票否决）**：TC-1 / TC-4 / TC-5 / TC-9。任一 FAIL → 不算完成，回 plan 修。
- 非 ★ 用例 FAIL 记录为 bug，按严重度决定是否阻断。
- 全 PASS（或 ★ 全 PASS + 非 ★ 受控）后更新 `STATUS/PPT.md` + `STATUS/status.md`（项目硬纪律）。
- 截图/日志证据存 `plans/manual-results-2026-06-22-ppt-pro/`，本目录只放用例定义。
