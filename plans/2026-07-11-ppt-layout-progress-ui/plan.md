# Plan：PPT 多构图与 Session 单卡动态进度

## 主要矛盾

- PPT 的主要问题不是图片质量本身，而是 durable full-page 路径把所有页的 Prompt 和文字面板几何写死为左文右图，导致整套 deck 没有节奏。
- Session 的主要问题不是 progress 事件过多，而是前端丢掉 `run_id/seq/event_type` 后把每条事件追加成普通消息；正确边界是 reducer，不是后端减少节点可观测性。

## 关联验收标准

- AC-22：确定性六类构图、整套视觉节奏、中文安全、一图一页结构不变。
- AC-23：同 run 单组件原位更新、状态/并发/乱序/重连/历史恢复边界完整。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/workflows/definitions/ppt_pro_nodes.py` | slide record/effect identity | deck-level layout planner、variant prompt、hash、视觉修订切换 |
| `backend/deskpet/tools/ppt_tools.py` | Pillow 页面合成 | 六类 layout spec 与 variant compositor、文字 fit/safe box |
| `backend/deskpet/workflows/definitions/v1/ppt_pro.py` | PPT graph routing | typed preflight failure 到安全 terminal；旧 record read-normalization |
| `backend/deskpet/workflows/progress.py` | public progress identity | transition event key 纳入 attempt |
| `backend/deskpet/workflows/runner.py` | run terminal convergence | allowlisted domain terminal_status 映射真实 failed/cancelled run |
| `backend/deskpet/workflows/service.py` | Durable history API | session-scoped event hydration 与 delivery fence 校验 |
| `backend/main.py` | Session history adapter | 用 `workflow_event_id` 补回结构化 durable envelope |
| `tauri-app/src/stores/sessionsStore.ts` | Session reducer | `workflow_progress` 类型与按 run/seq 归并 |
| `tauri-app/src/code-panel/ws.ts` | WS/history event adapter | live/history 统一调用 reducer，final 终态 |
| `tauri-app/src/message-panel/MessagePanelRoot.tsx` | 消息投影 | 保留 progress 结构与真实时间戳 |
| `tauri-app/src/components/MessageStreamPanel.tsx` | Session UI | 单卡进度条、状态视觉与无障碍属性 |

## Task 1：确定性 PPT layout planner [AC-22]

- `SlideOutline` 新增独立字段 `full_page_layout`，不复用 legacy `image_variant`；合法值为 `cover_band/text_left/text_right/visual_top/floating_card/quote_center`。旧 checkpoint 缺字段时由 planner 补齐，legacy renderer 继续只读 `image_variant`。
- 新增 `FullPageLayoutSpec`、`FULL_PAGE_LAYOUT_SPECS`、`FULL_PAGE_LAYOUT_SPEC_VERSION` 与 `plan_full_page_layouts(slides)`。spec 是 prompt/compositor 唯一数据源，至少包含安全框、mask、alignment、capacity/min font、negative-space instruction。
- `stable_slide_records()` 先对原始 normalized payload 计算 stable slide id，再给 payload 写自动 layout；因此自动布局/spec 升级和视觉修订不改变 slide id。slide id 排除 `notes/image_path/full_page_layout`，但内容文案变化仍改变 id。
- 第一阶段按角色与容量选候选：首张 `cover_band`；quote 优先 `quote_center`；双栏/高密度优先 `visual_top`；一般内容可用左右/card/top。第二阶段 coverage repair 用稳定顺序补足种类并检查 text preflight；tie-break 使用页 index + 内容 hash，不使用随机数。
- 约束：相邻不重复，`text_left/text_right` 不连续同侧；6 页以上至少 5 类，1–5 页最多不重复地覆盖可容纳布局。若任何候选都无法容纳，稳定失败 `text_overflow`；不以不兼容布局硬凑 coverage。
- 合法显式 `full_page_layout` 是 planner preference，不是不可变锁：若容量/邻接不兼容则选择下一兼容布局；非法值在 normalize 时清空。视觉评审的 `layout_misfit` 可覆盖该 preference，保证修订能收敛。
- 新增 `FULL_PAGE_COMPOSITOR_VERSION` 和 `FULL_PAGE_FONT_POLICY_VERSION`。pre-hash 包含 layout、layout spec、prompt、normalizer、compositor、font policy 和 page revision；notes 不进入，layout revision 保持 slide id 只改变 pre-hash。
- `layout_misfit` 按 `text_left→visual_top→text_right→floating_card→cover_band→quote_center` 的兼容循环选择下一布局，并再次检查邻页冲突；同页一轮多个 issue 只增一次 revision，只清空问题页 effect。清理不可达 `low_contrast` prompt 分支。

## Task 2：六类 Pillow compositor [AC-22]

- 用声明式 spec 定义每类文字安全框、遮罩、对齐、prompt 负空间和容量。
- 保留图片模型无字、Pillow 精确中文、一页一张最终 PNG 的边界。
- 生图前由 `stable_slide_records()` 调用与 compositor 相同的无副作用 `preflight_full_page_copy(slide, layout)`；planner 可在生成 effect 前切到高容量候选。全部候选仍放不下则失败，不生成图片、不留下部分输出。
- `_full_page_font()` 对含 CJK 的文案禁止 `ImageFont.load_default()`；按 Windows CJK fallback chain 打开字体，并通过字体 cmap 验证本页所有非空实际 code point（含生僻字与中文标点）均有 glyph；缺字则尝试下一字体，全部失败抛稳定 `cjk_font_unavailable`。
- `_fit_full_page_copy()` 先测量标题/正文/来源所有 block，再原子绘制；换行处理中文禁则标点、混合中英和长 token。所有 wrapped line 拼接去除布局换行后必须还原原文，不使用省略号、切片或丢 entry。
- 使用测量式字号 fit；在最小字号仍放不下时显式报 `text_overflow`，禁止静默截断。
- 保留 speaker notes、1792x1008、PPTX 单 picture invariant。

## Task 3：结构化 workflow history [AC-23]

- `WorkflowService.hydrate_session_history_event_ids(session_id, event_ids)` 按 event id 读取 Outbox、分组各自 run、hydrate envelope，并校验事件存在 `session_message/websocket` delivery 且 target_id 等于请求 session；main 不直接查 workflow DB。
- service 返回匹配的 delivery 元数据；`main.py` 再镜像 live fence：run 的 `delivery` session ref 必须匹配目标 session，ref epoch 必须等于当前 SessionDB epoch，且 session 未 deleted。epoch mismatch/deleted 的旧事件只保留 legacy 文本，不重建当前 run 卡。
- `session_messages_load` 批量调用该 API，对含 `workflow_event_id` 的历史行附 `workflow_event`；event 被 retention 清理、fence 不匹配或缺失时保持 legacy 文本。
- 不改变 SessionDB schema，不把结构化 JSON 喂回 LLM 历史。200 条截断仅还原返回窗口内事件，各 run 独立归并。

## Task 4：单 run progress reducer [AC-23]

- Message 增加 `workflow_progress` role 和 run/event/status/stage/ordinal/display_ordinal/total/seq/terminal/error_code/error_text/recovery_action 字段。
- `reduce_workflow_event()` 以 `workflow-run:{run_id}` 固定 key 原位更新并保留首次 `ts`/数组位置；仅 accepted/progress/final 这三类卡事件推进 run-level waterline，相同/更小 seq 忽略；history 先按 `(run_id,seq)` 排序再 reduce。terminal final 锁定后任何 progress 不得回退；并发 run 独立。
- 状态机：accepted→running；progress started→running、waiting→waiting、failed→failed（非终态）、cancelled→cancelled（非终态）；更高 seq progress 可恢复 running；final 映射 completed/failed/cancelled 且 `terminal=true`；final_assistant 不进入 reducer。
- `workflow.final` 即使没有 final_assistant，也必须把安全 error code、用户可理解错误文案和 recovery action 投影到卡片；禁止展示 traceback/原始参数。`text_overflow/cjk_font_unavailable` 有专门可理解文案。
- 合法 graph 回环不让进度条倒退：`display_ordinal=max(previous.display_ordinal,current.ordinal)`，阶段文案仍显示当前“修订问题页面”；最终完成固定 100%。测试覆盖 PPT 11/12→8/12 回环。
- live WS 与 history hydration 都走同一 reducer；无 run_id 降级为普通 assistant 文本。
- `workflow.final_assistant` 保留最终回答气泡，`workflow.final` 只更新进度组件。
- 新增 `merge_history_messages()`：普通持久消息按稳定 id 合并，不用随机 id；workflow envelope 按 `(run_id,seq)` reduce；late history 不能覆盖先到的 live progress，不丢 awaiting cards、live assistant/tool 消息。组件保留首次出现时间和数组位置。
- progress 的 failed/cancelled 只表示节点 transition，可被更高 `seq` 恢复事件覆盖；只有 `workflow.final` 锁定 run 终态。为避免同节点重试期间卡在旧状态，`WorkflowProgressReporter` 的所有 transition event key 都纳入 `identity.attempt`，同 attempt 重放仍幂等，新 attempt 的 started/waiting/failed/cancelled 都产生更高 seq；waiting 若原 attempt resume 不变，则在下一 public stage 或 final 前保持，不伪造信号。

## Task 5：进度条 UI 与交互边界 [AC-23]

- 新增 `WorkflowProgressRow`：任务名、当前阶段、`ordinal/total`、百分比进度条和状态标签。
- running 动画；waiting 黄、completed 绿、failed 红、cancelled 灰并停止动画。
- 组件具备 `role=progressbar`、`aria-valuemin/max/now`；同 run 更新不增加 rows length，不强制打断用户向上阅读。
- 卡片使用稳定高度约束；阶段单行省略，错误说明最多两行并通过 `title` 提供完整文本，避免原位更新改变消息流高度。DOM 测试锁定同 run 更新前后组件数量和高度样式；浏览器点击测试记录用户停在历史中部时 `scrollTop` 不跳。

## Task 5A：PPT typed preflight 与旧 checkpoint [AC-22]

- `ppt_tools.FullPageLayoutError(code, user_message, recovery_action)` 仅允许 `text_overflow/cjk_font_unavailable/layout_unavailable`；未知异常使用安全 `ppt_layout_failed`，不携带 raw exception。
- `prepare_slides_handler()` 捕获 typed error，写 `terminal_status=error` 与结构化 `terminal_error`；新增 `prepare_slides_route` 把失败直接送 terminal，最终 envelope/进度卡显示安全文案和恢复动作。
- `normalize_full_page_records(records)` 在 image_map/render 读取 checkpoint 时兼容缺 `full_page_layout` 的旧记录：ready 且文件存在的页面固定为 `text_left` 并保留旧 path/post_hash/pre_hash，不重复副作用；所有 pending/失效页面作为一个 deck 子集重新执行 planner，并把 ready 页视为已占用邻接约束，升级 spec/compositor/font-policy 元数据并重算 pre-hash。新旧记录均保持原 slide id。由于 ready legacy 页面不可重做，恢复 deck 的 5 类 coverage 为 best-effort 兼容豁免；新 run 仍严格满足 AC-22。
- `WorkflowRunner` 在正常 graph 输出后读取 allowlisted `values.terminal_status`：`error→FAILED`、`cancelled→CANCELLED`、`success/None→COMPLETED`，并把结构化安全 terminal_error/recovery_action 写入 run result；未知 domain status fail closed。这样 launcher 的 `workflow.final.status` 与业务结果一致，不把 preflight 失败标 completed。

## Task 6：验证与真机 [AC-22/23]

- 后端 planner：1–5 页、恰好 6 页、长 deck、同类内容、quote/two-column、显式 layout、coverage repair、恢复确定性、无法满足时 fail closed。
- 后端 hash/revision：layout/spec/compositor/font policy 改变均换 hash；notes 不换；revision 不换 slide id；仅问题页 pending，邻页 path/hash 不变。
- compositor：六布局 bbox 与 prompt/geometry 同源、原文守恒、输出尺寸、失败无部分文件；字体缺失/缺 glyph、中文标点、中英混排、长 token、最小字号溢出。
- history：多 run、target/epoch/deleted fence、event 缺失/retention、200 条窗口、history/live 竞态。
- Graph/preflight：三种 typed error 安全 terminal、未知异常安全兜底、run store/`workflow.final.status=failed`、final 无 traceback；旧 checkpoint 的 ready page 复用、pending deck 子集重规划与相同 slide id。
- Progress identity：同 attempt 的 started/waiting/failed/cancelled 各自幂等；新 attempt 的四种 transition 均产生新 event/更高 seq，但前端仍只更新同一张卡。
- 前端 reducer：重复、乱序、final-before-progress、terminal lock、节点失败后新 attempt 恢复、11→8 合法回环进度不倒退、并发、legacy、final assistant；UI 五状态、终态错误/恢复动作、ARIA、同 run 更新不增 rows length/高度且不触发滚动位置重置。
- 视觉：生成六版式本地样张与 montage，逐页检查布局差异、文字安全和底部内容。
- Windows Computer Use：真实 Session 启动 PPT，确认进度只占一条且动态变化；确认大纲后观察 waiting→running→completed；打开最终 PPT 抽查多种构图。

## 可追溯矩阵

| AC | Tasks | 自动化 | 真机 |
|---|---|---|---|
| AC-22 | 1,2,6 | planner/compositor/PPTX tests | 成品多构图抽查 |
| AC-23 | 3,4,5,6 | history/store/ws/component tests | 单卡动态更新与终态 |

## 执行结果（2026-07-11）

- Task 1/2：六类 planner/compositor 已落地；最终真实 run `371e34dd90c044c0963c748f7a3ce6c9` 交付 6 页 `full_page_images` deck，每页恰好 1 个全幅 picture，视觉审查 6/6 `ok`、无质量警告。
- Task 3/4/5：live/history 统一 reducer 已落地；Windows Computer Use 观察到同一卡片从 2/12、3/12、4/12、waiting 6/12、running 8/12、revision 11/12 到 completed 12/12，应用重启后仍只恢复一张 100% 卡。
- 大纲修改语义：真实点击“修改”后显示“正在修改 PPT 大纲”，新版第 2 页标题正确落地，再次确认后继续生成。
- 真测补洞：视觉修订期 provider 暂时不可用曾把 full-page deck 静默降级为模板；现已规定显式 `full_page_images` 不允许模板回退，连接错误跨 checkpoint 重试未完成页，视觉审查预算耗尽则保留整页图并显式标注质量警告。
- 自动化：focused backend 73 passed；workflow/PPT 宽回归 450 passed；前端 tsc PASS，focused 34 passed。最终 PPT、montage、结构报告与真实 UI 截图见 `plans/manual-results-2026-07-11-ppt-layout-progress-ui/`。
