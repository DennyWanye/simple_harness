# 子代理并发驱动 — windows-mcp 真机 E2E 结果 (V1–V5)

> **日期**: 2026-06-21 | **分支**: master | **测试方式**: windows-mcp 真坐标点击 + 真中文输入(剪贴板粘贴) + 截图 + backend.log/metrics.jsonl 铁证
> **配套**: [`04-manual-test-cases.md`](../2026-06-21-subagent-concurrency-driver/04-manual-test-cases.md)
> **环境**: dev 模式 (`scripts/dev-start.ps1` 同款启动，Tauri spawn 单 backend via `backend/.venv`)；dev 自动登录 (`tauri-app/.env.local`)；relay `chinzy.com` 200 OK，gpt-5.5；三 flag 全开 (`[features] subagent_driver/agent_team/subagent_nonblocking = true`)；BGE-M3 真 worker (非 mock)。
> **证据源**: backend structlog → `G:\projects\deskpet\logs\backend.log`（FileHandler，main.py:42）；进度事件 → `backend/userdata/metrics.jsonl`（双发：scheduler run_id-based + agent_parallel tool task_id-based）。

## 总览

| 用例 | 结论 | 关键证据 |
|---|---|---|
| **V1** ★ 多事务异构并发 | ✅ PASS | agent_parallel 调用；3 子任务 kind=research/web/general 三不同 lane；250ms 内全 running 时间重叠；聚合回一条；进度卡片 Code 模式可见 |
| **V2** ★ 同构任务池 spawn_team | ✅ PASS | `spawn_team team=… n=4`；team_task_claim ×8(4真+4空=池清零)；team_task_update ×4 status=done；4 译文聚合 |
| **V3** 背压 (global cap) | ✅ PASS | 6 子任务全入队；峰值并发=4(global cap)；2 个排队等待→随完成晋升；6 全 completed 不丢；卡片"运行中 4/6"实拍 |
| **V4** ★ flag OFF 字节级 BC | ✅ PASS | 158 用例全绿(0 失败)；`test_scheduler_none_is_bc`(★BC) + `test_byte_level_consistency` + `test_subagent_config` 覆盖 flag-off 扁平 gather/无 scheduler |
| **V5** ★ 取消级联 | ✅ PASS | spawn_subagents(background) run_id=`spawn-*` 立即返回；停止按钮→`subagent_cancel_all n=3`；2 running→failed(取消)即时；取消后 0 LLM 出站(不烧 token) |

**裁定: 5/5 全 PASS（含 4 个一票否决 ★ V1/V2/V4/V5）。**

---

## V1 ★ 多事务异构并发 — PASS

- **flag**: subagent_driver=true
- **坐标/动作**: 桌宠消息面板输入框 (2818,1274) → 剪贴板粘贴中文 → Enter
- **输入**: 「同时帮我做三件事，请并行处理：①深度调研2025年钠离子电池产业现状 ②查一下小米SU7的最新售价 ③把"season复盘"的要点列成一份提纲」
- **截图**: `screenshots/V1-aggregated-reply-idle.png`（聚合长回复尾部=season提纲，状态✓空闲）、`screenshots/V1-progress-card-codemode.png`（Code 模式进度卡片 "🤖 子代理并发·全部完成(3)" 含3行 kind 徽章+终态）
- **log 证据** (`V1-evidence.txt`):
  - `name='agent_parallel' … parse_ok=True`
  - `subagent_scheduled kind=research run_id=code-xbhyzzo0.par-sodium_ion_2025_research`
  - `subagent_scheduled kind=web run_id=…xiaomi_su7_latest_price`
  - `subagent_scheduled kind=general run_id=…season_review_outline`
  - metrics: 3 子代理 queued→running 全在 +0.00~0.25s（**时间重叠=真并发**）；season +42s completed、sodium +129s completed、su7 +105s failed
- **裁定**: 3 不同 kind + 并发重叠 + 聚合单消息 + 进度卡片可见 → **PASS**
- **附带验证 (E-2 错误隔离)**: web 子代理(SU7 查价)因外部搜索接口当日不稳而 failed，主代理优雅兜底(主循环自行补 web_search/web_fetch 取价)并把 2 成功+1 兜底聚合进同一条回复；桌宠自述"外部搜索接口今天有点抽风"。错误隔离 + graceful recovery 真机生效。

## V2 ★ 同构任务池 spawn_team — PASS

- **flag**: agent_team=true
- **坐标/动作**: 桌宠主输入框 (3420,1383) → 剪贴板粘贴 → Enter
- **输入**: 「用一个并行团队(spawn_team)把下面这4句话分别翻译成英文…①春天来了…②学习是一辈子…③喝水八杯…④代码要优雅」
- **截图**: `screenshots/V2-spawn-team-result.png`
- **log 证据** (`V2-evidence.txt`):
  - `name='spawn_team' … {"kind":"general","num_teammates":4,…} parse_ok=True`
  - `spawn_team team=team-1782008966-b40a70 n=4`
  - `team_task_claim` ×8（4 真领取 + 4 空领取=**池清零**）、`team_task_update` ×4 全 `status="done"`
  - 4 译文: "Spring has arrived, and all things come back to life." / "Learning is a lifelong endeavor." / "Drink enough water—eight glasses a day." / "Code should be written elegantly."
  - 末轮聚合 end_turn + billing_record
- **裁定**: team 真起 + claim 池清零 + 4 结果聚合 → **PASS**

## V3 背压 (global cap=4) — PASS

- **flag**: subagent_driver=true，`global_concurrency=4`(默认)，lane: web=3/general=2…
- **坐标/动作**: 桌宠主输入框 (3420,1383) → 粘贴 → Enter
- **输入**: 「请用子代理并行处理这6个相互独立的小任务…①②③查北京/上海/广州天气 ④⑤⑥把'勇气'/'坚持'/'梦想'写成五言诗」(3 web + 3 general)
- **截图**: `screenshots/V3-card-running-4of6.png`（卡片头 "🤖 子代理并发 · 运行中 4/6"，3 weather running + 2 poem completed + 1 poem running）
- **log/metrics 证据** (`V3-evidence.txt`，run_id-based 时间轴):
  - +0.00~0.45s：6 个全部 `queued`（无丢）
  - 前 4 个立即 `running`（3 weather + poem_courage）；poem_persistence、poem_dream **滞留 queued**（被 global cap=4 挡住——注意 general lane(cap2) 尚有空位，证明是**全局闸**在背压）
  - +5.71s poem_courage 完成→poem_persistence 晋升 running；+11.31s→poem_dream 晋升
  - **peak concurrent running = 4**；queued:6 / running:6 / completed:6（**6 全完成不丢**）
- **裁定**: 4 跑 2 排队 + 排队晋升 + 全完成不丢 + 峰值并发=4(全局闸) → **PASS**
- **备注**: Code 模式进度面板订阅同一 control WS，对桌宠主聊触发的并发也实时渲染（4/6 卡片实拍来自此）。

## V4 ★ flag OFF 字节级 BC — PASS

- **方法**: 该用例为 BC/回归测试，测试用例 §V4 动作① 即"跑 pytest"。pytest fixture 三 flag 默认 False，天然走 flag-off 路径。
- **命令**: `backend/.venv/Scripts/python.exe -m pytest`（17 个 subagent/agent_parallel/team 套：test_agent_parallel*, test_byte_level_consistency, test_spawn_team*, test_subagent_*, test_task_kinds, test_team_store, test_teammate_tools* …）
- **结果**: **158 passed in 5.63s（0 失败，diff=0）**
- **关键 BC 断言** (`V4-evidence.txt`):
  - `test_subagent_config.py`: `assert f.subagent_driver is False`（出厂默认全 OFF）
  - `test_agent_parallel_kinds.py::test_scheduler_none_is_bc`(★BC): scheduler=None → 原扁平 gather，**无 subagent_scheduled**
  - `test_byte_level_consistency.py`: flag-off 下 agent_parallel 信封 `{ok,result,error}` 字节一致
- **活体反证**: V1/V3/V5 的 scheduler 之所以触发，正因 flag=ON；flag OFF 时 main.py 传 scheduler=None（即 test_scheduler_none_is_bc 验证的 BC 路径）。
- **裁定**: 测试全绿无新失败 + flag-off 扁平 gather 无调度 + 信封字节一致 → **PASS**

## V5 ★ 取消级联 — PASS

- **flag**: subagent_nonblocking=true
- **坐标/动作**: ①桌宠主输入框 (3420,1383) 粘贴+Enter 派后台子代理；②Code 模式会话停止按钮 (3735,1983) 真点击
- **输入**: 「请在后台用子代理并行帮我深度调研3个竞品(特斯拉Model 3/比亚迪汉EV/蔚来ET5)…spawn之后立刻返回别等」
- **截图**: `screenshots/V5-cancelled-card.png`（tesla/byd ❌ failed）
- **log 证据** (`V5-evidence.txt`):
  - `name='spawn_subagents' … parse_ok=True`；run_id 前缀 `code-xbhyzzo0.spawn-*`（区别于阻塞 agent_parallel 的 `par-*`）；桌宠"已在后台并行启动3个深度调研子代理，没有等待它们完成"=**立即返回**
  - 停止按钮 → `chat_v2_interrupt` → **`subagent_cancel_all n=3`** (10:26:22.048)
  - tesla/byd(running) 于 10:26:22.05 即时转 failed(CancelledError 取消)；nio(queued)亦在 n=3 内取消
  - **取消后 0 条 LLM 出站调用**(grep openai_compat_outbound=0)→ 不再烧 token
- **裁定**: cancel_all n=3 + 活子代理停 + 无后续 LLM → **PASS**
- **次要瑕疵(非阻断)**: 在信号量排队中(general/research lane 已满)被取消的子代理(nio)不经过 scheduler 的 running/failed emit 分支，故不发终态进度事件 → 卡片该行停留 "queued" 不更新为 ❌（功能上已取消，n=3 + 无 token 烧覆盖）。建议后续给"排队中被取消"补一条终态进度事件以让卡片归位。

---

## 架构观察（供后续）

1. **进度卡片(SubagentProgressPanel)只挂载在 Code 模式**(`code-panel/MessageStream.tsx`)，桌宠主消息面板用 `components/MessageStreamPanel.tsx` 不渲染该卡片。subagentStore 经同一 control WS(`code-panel/ws.ts`) 喂数据，故桌宠主聊触发的并发也能在 Code 模式卡片实时显示(V3 实证)。若产品上希望桌宠消息面板也显示并发卡片，需在 MessageStreamPanel 也挂载 SubagentProgressPanel。
2. **metrics 进度事件双发**：scheduler 发 run_id-based(queued/running/completed/failed)，agent_parallel 工具另发 task_id-only(starting/completed/failed)——统计终态时会"翻倍"，分析须按 run_id 去重(本报告 analyze.py 已只取 run_id-based)。
3. **kind→lane 路由**由 LLM 在 agent_parallel/spawn_subagents 的 `kind` 字段显式给定(实测 research/web/general 准确)。
