# FP-1 目标持久化地基 — windows-mcp 真机手测结果

> 按 [testcase/goal-completion/FP-1-目标持久化-manual-test.md](../../testcase/goal-completion/FP-1-目标持久化-manual-test.md) 逐 TC 执行。
> 执行日期: 2026-06-10 ｜ 执行方式: windows-mcp 真机(Click/Clipboard/ctrl+v/Scroll 真 OS 事件) + CDP 9333 定位/截图 + DB/log 核对
> 环境: master @ 89932fa+(含本次 ws.ts 修复), DESKPET_BACKEND_DIR 源码 backend, DESKPET_USER_DATA_DIR=.tmp/fp1-userdata-a(主)/-b(隔离), goal_mode=true, compaction_enabled=true
> 截图: ./screenshots/

## 结果汇总

| TC | 范围 | 判定 | 关键证据 |
|---|---|---|---|
| TC-1.1 | 设目标→重启→仍在(招牌, pass^k=3) | **PASS 3/3** | k1`帮我整理本周三个会议纪要`/k2`写一份季度OKR草稿`/k3`收集三家竞品资料做对比表` 每轮: UI`已设置目标:...（上限 10 轮）`→taskkill→重启→log`goal_store_load_persisted restored=1`→UI`当前目标: <原文>（已用 0/10 轮）`; 截图 tc-1.1-1-goal-set-final / tc-1.1-2-after-restart-PASS / tc-1.1-k2-after-restart-PASS / tc-1.1-k3-after-restart-PASS |
| TC-1.2 | iterations 重启恢复(T1) | **PASS** | goal_checker rebound 后 DB `iterations_used=1`(goal_id=236379cd)→重启→同 goal_id 仍 `=1` 未归零; log`goal_checker_nudge_injected iter=1/10` |
| TC-1.3 | clear→abandoned 不删行 | **PASS** | `/goal clear` 后同 goal_id 行仍在、`status='abandoned'`、iterations 保留 |
| TC-1.4 | flag-OFF 字节基线(R-T5) | **PASS**(步骤1) | `PASS: flag-OFF baseline ok; no session_goals table; sha256=e5c004ff85ee8159` exit=0。步骤2/3(真机 flag-off 冷启动)未另跑——脚本+单测已覆盖该断言,诚实标注 |
| TC-1.5 | 多目标 last-write-wins | **PASS** | 连设`目标A:写周报`→`目标B:订机票`→`/goal`返回 B; DB top2=[B,A](旧行不自动 abandoned=当前真实行为); 截图 tc-1.5-latest-active.png |
| TC-1.6 | ToolPath 录制(WI-1.6) | **PASS**(后端核对级) | boot`fp5_codify_wiring_ready tool_path=True candidate_store=True llm=True`; 执行期 29 次工具事件(record_tool 输入); 单测 test_tool_path_recording 4 passed(B 步) |
| TC-1.7 | goal_checker 末轮接电 | **PASS** | log`goal_checker_nudge_injected sid=code-6kbuuzg6 iter=1/10`(未完成 rebound)+DB iterations 同步+1; `wi13_goal_anchor_injected iter=5/10` 同捕获 |
| TC-1.8 | USER_DATA_DIR 隔离(R-T7) | **PASS** | B 首启`restored=0`; A 最新=`目标B:订机票`且无 B 目标(0); B 最新=`B目录目标:测试隔离`且无 A 目标(0); 截图 tc-1.8-userdata-b.png |

**FP-1: 8/8 PASS**(TC-1.4 步骤2/3 可选真机项以脚本+单测覆盖,已诚实标注)。

## 执行中发现并修复的问题

| # | 问题 | 性质 | 处理 |
|---|---|---|---|
| 1 | **ws.ts `slash_command_result` 不清 inflight** → slash 后 UI 永卡"思考中"、发送钮消失 | **产品 bug** | 已修(ws.ts 加 `upsert({status:idle,inflight:false})`),真机复测过 |
| 2 | CDP 9333 未注入(main.rs 默认 9222 被 Clash Verge 占) | 测试环境 | 启动加 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333` |
| 3 | git-bash MSYS 把 `/goal` 转换成 `C:/Program Files/Git/goal` | 测试方法 | `MSYS_NO_PATHCONV=1`;后改用 windows-mcp Clipboard+ctrl+v 全原生链 |
| 4 | code-panel 窗口隐藏态点击穿透到壁纸 | 测试方法 | 必须先真点 toolbar code 图标让窗口可见(物理截图确认) |
| 5 | dashboard tile 裸 textarea 不解析 slash(只有全屏 InputBar 解析) | 产品限制(testcase 已知) | 走 ⤢ 完整 chat 的 InputBar |
| 6 | Virtuoso 虚拟列表:气泡不在视口就不在 DOM,innerText 搜不到 | 测试方法 | 验证前先 Scroll 到底 |
| 7 | SlashDropdown/ArgHint 开着时 Enter 被吞 | 产品 UX(轻) | 测试用"带尾空格+点发送钮";产品侧待评估 |

## 遗留/建议
- bug#7(Enter 被 dropdown 吞)与 tile 不解析 slash(#5):建议产品侧评估是否统一。
- 旧 active 行不自动 abandoned(TC-1.5 注明的当前行为):多行 active 累积,建议 set 时 abandon 旧行(冻结契约允许 last-write-wins,非缺陷)。
