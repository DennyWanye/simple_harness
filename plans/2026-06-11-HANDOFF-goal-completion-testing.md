# HANDOFF — goal-completion 测试线交接(2026-06-11)

> 给压缩后会话 / 新会话 / 子代理。接手前必读本文件 + 引用的 RESULTS。
> 上下文:用户指令是「按 testcase 逐 TC 真机测试(windows-mcp),报错则修复复测,opus 4.8 审计完整性」。已全部完成,剩 4 个待修 bug。

---

## 1. 当前状态(已完成,勿重做)

**40/40 TC 全部执行完毕 + opus 4.8 二轮全范围审计「接受归档」**:

| 范围 | 结果 | 证据文件 |
|---|---|---|
| FP-1(8 TC) | 8/8 PASS(招牌 TC-1.1 pass^k 3/3) | [manual-results-2026-06-09-FP-1/RESULTS.md](./manual-results-2026-06-09-FP-1/RESULTS.md) + AUDIT-opus.md + 9 截图 |
| FP-2(10 TC) | 8 PASS + 2 BLOCKED(预期);TC-2.1 经 4 刀修复 FAIL→PASS(fired reduction=0.976+压缩后零漂移) | [manual-results-2026-06-09-FP-2/RESULTS.md](./manual-results-2026-06-09-FP-2/RESULTS.md) |
| FP-3/4/5(22 TC) | 7+7+8 全判定;2 FAIL(4.5 已修/5.1 待修) | [manual-results-2026-06-10-FP345/RESULTS.md](./manual-results-2026-06-10-FP345/RESULTS.md) + **AUDIT-opus-full.md(总审计)** |

**本轮已修 6 个真 bug(全 TDD+commit)**:
1. `58e82f7` ws.ts slash 后 UI 永卡"思考中"(inflight 不清)
2. `e6ee37f` token 估算 CJK 低估 4 倍 + ASCII markdown 3.5 校准
3. `4f7141d` compaction 判定接 relay 真实 prompt_tokens 反馈回路(第 3 刀)
4. `0800299` compressor 接线传裸 provider → AttributeError(第 4 刀,_CmpShim 修复)
5. `75af4bd` B-10 钩同 key 堆积(15 行全 active)→ `facts.upsert_replacing`
6. (testcase 体系本身: `89932fa` FP-1/FP-2 用例 3 轮迭代 + index 登记)

最近 commit 链: `0d7c2e8`(总审计) ← `c6a2fde`(FP345 结果) ← `75af4bd` ← `dd5242d`(TC-2.1 PASS) ← `0800299` ← `4f7141d` ← …

---

## 2. ⏭ 待办(按优先级,用户已知悉)

### 🔴 待修 bug ×4

| # | bug | 根因状态 | 修复入口 |
|---|---|---|---|
| 1 | ~~**TC-5.1 skill 自动披露零匹配**~~ → **✅ 已修复+真机双 PASS (2026-06-11)** | 真因三层:① SkillComponent 走 `loader.select(task_type)`,builtin v1 skill task_types=[] 全被滤(total=1) ② sync build 调 async encode 静默 no-op ③ log top_sim 打 strong[0] 掩盖真实分。另发现 BGE-M3 对短中文 query 区分度不够(8 query 校准 on-target 0.45~0.55 vs off-target 0.53+) | **commit `3526ac1`**(全集 venue+build_async 预热+top_sim 真值)+**`b439bbc`**(混合匹配:SkillMeta.triggers 词法路+when_to_use 进 embedding+12 builtin 补全)。真机:TC-5.1 `total=12 strong=1 auto_loaded=1 names=['deep-research'] top_sim=0.950`;TC-5.7 `skill_remounted names=['recall-yesterday']`(同 turn skill_invoke→compaction→remount 全链)。注:auto-disclosure 集合并入 remount 仍是 Future TODO(agent_loop.py:1895) |
| 2 | ~~**skill 候选卡 pending 吞消息**~~ → **✅ 已修复+真机 PASS (2026-06-11)** | 真因三层(比预估深):① InputBar+SessionGridView tile 两处 `Enter/按钮 inflight→stop()`——打好的字不发送不排队还静默打断 turn(4 次复现的实际入口在 tile) ② 后端 codify 300s Future-await 内联 chat task,同 sid 抢占把候选 Future 连带 cancel ③ **ws.ts send() 在 socket 非 OPEN 时直接丢弃消息**(UI 显示气泡但 backend 收不到=完整吞症状) | commit `c9b31f9`(后端拆后台 task+InputBar)+`fb25ac2`(tile 补刀+harness)+`fc1e37c`(ws outbox 队列+重连 flush)。真机:inflight 中发消息立即入库处理;卡 pending 中 chat 秒答并行;点忽略 confirm_received cid=23;超时路径 cid=21 整 300s 自动 reject(历经多 turn+页面 reload 仍存活) |
| 3 | **ArtifactCard 不渲染**(ppt/excel 真产物只有文本路径无卡片) | 未定位。链路:工具 result(artifacts 字段有,本地直调已证)→ envelope → 前端 `extractArtifactsFromResult` → MessageBubble。另观察:receipt 的 artifacts=[] 空(receipt 记录器不抄工具 artifacts 字段,小问题) | 真机跑一次 ppt_create 抓 WS 消息看 envelope 里 artifacts 丢在哪层 |
| 4 | **登录态强杀后失效**(两次弹 Token Relay 重登,均在多次 `taskkill /F` 后) | 疑 refresh token 落盘在退出钩子,强杀来不及写(真实用户崩溃/断电同样触发) | 查 keychain 写入时机(`backend/llm/keys.py` / relay 登录流);改成 token 刷新即落盘 |

### 🟡 复验/优化
- ~~**TC-4.5 真机复验**~~ → **✅ PASS (2026-06-11)**:复验发现二阶缺陷(只 supersede 最新一条,历史脏堆积不自愈)→ 修复 `ac76d48`(supersede 全部 active 同 key)。真机:`/goal` ×2 → active=1 + 16 superseded,15 条脏行一次自愈。注:契约是 `/goal <text>`,无 set 子命令。
- goal_checker 中文长输出 JSON 解析失败率偏高(降级路径工作,`goal_checker.skipped` 频出)→ prompt 加固。
- 补 2 张留痕截图(TC-1.7 goalcheck / TC-3.3 misalign,审计 L1/L2,功能证据已实锤)。

### ⏸ BLOCKED 功能(非 bug,roadmap 待接线)
- TC-2.2:任务图产品链路(main.py 注册 TaskGraphStore + spawn_team 传参 + 前端 goal_tasks 面板;解锁清单见 FP-2 testcase TC-2.2「解锁前置」)。
- TC-2.9:GD_actions/GD_inaction 漂移指标(完全未实现)。

---

## 3. 🛠 真机测试环境与踩坑大全(本轮血泪,接手必读)

### 启动(标准配方)
```bash
export DESKPET_CONFIG="G:\\projects\\deskpet\\.tmp\\fp1-config.toml"      # 全 flag ON(goal/verify strict/compaction/codify/auto_disclosure/persona/goal_facts_hook)
export DESKPET_BACKEND_DIR="G:\\projects\\deskpet\\backend"               # 源码 backend(坑#8)
export DESKPET_PYTHON="G:\\projects\\deskpet\\backend\\.venv\\Scripts\\python.exe"
export DESKPET_USER_DATA_DIR="G:\\projects\\deskpet\\.tmp\\fp1-userdata-a" # 已登录态(R-T7 隔离)
export WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS="--remote-debugging-port=9333 --remote-allow-origins=*"  # ★必须!main.rs 默认 9222 被 Clash Verge 占
cd /g/projects/deskpet/tauri-app && npx tauri dev > /g/projects/deskpet/.tmp/<name>.log 2>&1   # 后台跑,log 落文件
```
启动前必杀:`deskpet.exe` + 8100 LISTENING pid + 5173 vite pid(坑#1/#7)。boot 锚点:`companion_code_v1_goal_mode_ready` / `goal_store_load_persisted restored=N`。

### 操作链(全部验证过可靠)
- **发送消息**:`MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" backend/.venv/Scripts/python.exe testcase/_send.py code0 "<文本>"`(MSYS 豁免必带,否则 `/goal` 被转成 `C:/Program Files/Git/goal`!)
- 或 windows-mcp 原生链:Click 输入框 → Clipboard set → `ctrl+v` → Click 发送钮(坐标用 CDP fresh 算:`(screenX+rect)*dpr`)
- **开 Code 面板**:CDP 在 companion 主窗 DOM 找 `aria-label*=进入 Code 模式` 按钮取物理坐标再点(别猜 toolbar 第几个图标——会点开商店)
- **slash 命令只在全屏 InputBar 解析**:dashboard tile 裸 textarea 不解析(已知限制);点 tile 的 ⤢(aria-label*=完整 chat)进全屏
- **验证 UI 文本**:Virtuoso 虚拟列表——**气泡不在视口就不在 DOM**!先 `windows-mcp Scroll down ×30` 到底再 innerText 搜
- **SlashDropdown 吃 Enter**:裸 `/goal` 的 Enter 被下拉吞;带尾空格或点发送钮
- **候选卡 pending 时消息会被吞**(bug#2):发消息前先点「忽略/保存」清卡
- **DB 查**:`backend/.venv/Scripts/python.exe -c "import sqlite3..."`(无 sqlite3 CLI;console GBK 乱码加 `PYTHONIOENCODING=utf-8`)
- **物理屏幕是真理来源**:CDP 报 visible≠窗口真显示(隐藏窗点击穿透到壁纸);怀疑就 `windows-mcp Snapshot use_vision=true`
- ppt/excel 产物默认落 `C:\Users\24378\AppData\Local\Temp\deskpet-ppt-*.pptx`(不是 userdata/artifacts!)
- compaction 触发条件:单 turn 内累积(每 turn 起步被 last_n 裁到 ~10k);触发配方=「read_file 连读 5-6 个大文件」;real 反馈下 ~24k 即 fire
- 用户在用电脑(游戏/全屏)时**立即停手** GUI 操作

### 关键 log 锚点速查
`goal_store_load_persisted restored=N` / `goal_checker_nudge_injected iter=M/10` / `wi13_goal_anchor_injected` / `p1_4_compaction_fired` / `skill_candidate_proposed|confirm_received` / `skill_auto_disclosed total= strong= top_sim=` / `p4_facts_daily_decay_startup mutated=` / `b10_goal_facts_hook_bound` / `verify_gate_init mode=strict`

---

## 4. 文件地图

| 类 | 路径 |
|---|---|
| testcase(40 TC) | `testcase/goal-completion/FP-1-*.md`(8) / `FP-2-*.md`(10) / `testcase/goal-completion-manual-test.md`(22) |
| 结果+审计 | `plans/manual-results-2026-06-09-FP-1/`(RESULTS+AUDIT-opus+截图) / `-FP-2/` / `-2026-06-10-FP345/`(RESULTS+**AUDIT-opus-full**+截图) |
| roadmap | `plans/2026-06-04-goal-completion-upgrade/10-EXECUTION-ROADMAP.md`(§3 进度表含 2026-06-10 复测节) |
| harness | `testcase/_send.py`(SendInput 发消息) / `_cdp.py`(pages/shot/text/eval) / `deskpet-input.ps1`(Temp 下) |
| 本轮修复源码 | `backend/agent/token_budget.py` / `backend/agent/agent_loop.py`(_last_real_prompt_tokens) / `backend/main.py`(_CmpShim+upsert_replacing 调用) / `backend/deskpet/memory/facts.py`(upsert_replacing) / `tauri-app/src/code-panel/ws.ts` |

## 5. 接手第一步建议

修 bug#1(TC-5.1):读 `skill_matcher.py:118-128` 注释 → 修 sync/async + total=1 → 单测红绿 → 重启真机「帮我深度调研XX」→ grep `skill_auto_disclosed` strong≥1 → 连带复测 TC-5.7(压缩后重挂)。然后依序 #2 → #3 → #4。每修一个:TDD + commit + 真机复测 + 更新对应 RESULTS。
