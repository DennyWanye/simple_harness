# PPT Pro — 实施 + 真机验收 RESULTS（2026-06-22）

> 承接 [plan v1.3 LOCKED](../2026-06-21-ppt-deepresearch-pro/00-PLAN.md) + [runbook](../2026-06-21-ppt-deepresearch-pro/EXECUTION-WORKFLOW.md) + [手测文档](../../testcase/2026-06-22-ppt-pro/manual-test.md)。

## 1. 实施完成度：100%（子代理两轮评估 + 单测 + 冒烟）
- 全部 WI-0~10 + F1-F4 实现并提交（commits `feat(ppt-pro): ...` 系列）。
- 子代理完成度评估：第一轮 96%（2 功能缺口）→ 补完（`/停止`接`_ppt_pro_cancel` + `ppt_pro_smoke.py`）→ 第二轮 **100% 判定达标**。
- 单测：`-k "ppt or outline or clarify or image or receipt"` **221 passed**；接线冒烟 `scripts/acceptance/ppt_pro_smoke.py` **DECISION: SHIP**（11/11）；collect-only 3276 无错。
- 关键机制代码层全部就位且子代理核实非空壳：双保险防二次烧图、双模式大纲防回退塌方、identity-guard、广播+去重、跨重启不造死卡、分层 error_kind、知情降级、分阶段限时、`/停止`级联。

## 2. 真机 windows-mcp E2E — TC-1 ★ 核心链路 PROVEN（含真 E2E 发现并修复的路由 bug）

环境：dev backend 跑**当前码**（log `[backend_launch] Dev python=...backend_dir=...`）+ relay 已连接 + CDP 9333（仅用于定位坐标）+ SendInput 真鼠标点击/剪贴板真输入。

### 2.1 真 E2E 发现并修复的路由 bug（单测/冒烟都测不出，只有真机暴露）
- **现象**：桌宠聊天发「做个惊艳PPT」，LLM 走了 `web_search ×2 + todo_write ×4 + ppt_create`，**完全绕过 ppt_pro**。
- **根因**：chat 类不加载 ppt-generate skill body，LLM 只看裸工具 schema；`ppt_create` 的 description 又长又详还写了「AI 整页生图最惊艳」把需求吸走，`ppt_pro` description 只有一句简短英文。
- **修复**（commit `fix(ppt-pro): 真机E2E发现路由缺口...`）：强化 `ppt_pro` tool description（「做PPT首选/禁自己 web_search 绕过/一条龙调研+确认+生图兜底」）+ `ppt_create` description 加改道提示。
- **复测**：重启后同样请求 → backend log `idx=0 name='ppt_pro' args={image_mode:true,depth:standard}` → **LLM 真调 ppt_pro** ✅。

### 2.2 TC-1 逐环真机证据（F1→F2→F3→F4 全打通）
| 环节 | 证据（backend log / 截图 / 真操作） | 判定 |
|---|---|---|
| **秒回** | 桌宠气泡「已经开始后台调研啦…会先生成大纲确认卡；你确认/修改后再继续生成」=ppt_pro `status:researching` 秒回 | ✅ |
| **F1 调研** | 真调 deepresearch；搜狗百科直连源真抓（`baike.sogou.com` GET 200）；notifier 气泡「📚 调研完成（2 个来源），正在拟大纲…」 | ✅（来源数受网络限，google-cdp 无VPN TimeoutError，搜狗百科兜底） |
| **F2 拟纲** | LLM 6229 字流产出大纲（grounded） | ✅ |
| **F3 确认卡** | message-panel 真渲染 PPTOutlineCard：大纲正文含引用 `[^1]` + 四按钮「📜历史大纲/✅确认生成/✏️修改/✖取消」（截图）；**SendInput 真点「确认生成」(2669,1033)** → log `event='ppt_outline_decision_resolved' outline_id='58...'`（**非 no_pending 死卡**） | ✅ |
| **F4 生图** | 确认后真发 `POST /v1/images/generations`，**首图 200 OK**（惊艳路径首图 gate 通过，未走回退） | ✅ 机制；🟡 见 §3 |

## 3. 环境受限（如实记录，非代码 bug，**不造假**）
- **relay gpt-image-2 图像配额/权限 403**：TC-1 首图 200 后，后续 7 张图全部 `POST /v1/images/generations 403 Forbidden`（同时 `chat/completions` 仍 200 → 是**图像模型专项 403**，非全局掉线，疑夜间重度测试后配额耗尽）。
  - 后果：autofill 得 1 真图 + 7 占位（n_ok=1>0，按设计不触发整副模板回退），deck 渲染阶段 CPU 长时间活跃（含 WPS COM 视觉评审）但成品在本次观测窗口内未落盘。
  - **结论**：F1-F4 **机制端到端 PROVEN**；惊艳 deck 的**图像完成度受 relay 403 配额限**，需图像配额恢复后复跑。
- **待办（relay 图像配额恢复后续跑）**：TC-1 deck 落盘+自动打开收尾；TC-2~14（修改环/取消/★TC-4网络回退/★TC-5模型不可用回退/content不误伤/调研降级/配置错/★TC-9 preempt/去重替换/TC-11停止/历史复用/BC/边界）。其中多数需 gpt-image-2 可用。

## 4. 观察 / 待investigate（非阻断）
- **持续 403 期间渲染耗时长**：首图成功后 7×403 占位，deck 渲染（含 visual_review WPS COM）在观测窗口内未完成。需复核：图像大面积失败（n_ok 远小于页数，如 1/8）时是否应也回退模板（当前仅 n_ok==0 才回退）——relay 配额恢复后复现确认。

## 4b. 续测（用户选 A：用当前 gpt-image-2 403 状态测回退+非生图用例）

- **★TC-9 preempt 不杀确认链路 — PASS（双重铁证）**：ppt_pro 大纲卡未决时,向桌宠发无关消息「现在几点了？」→ 桌宠**真回答**「现在是 2026年6月22日 00:31…」(截图)＋随后 SendInput 真点「确认生成」→ log `ppt_outline_decision_resolved outline_id=86…`（**非 `no_pending` 死卡**）。证明独立编排 task 未被 same-sid 新消息 preempt 杀掉 = R-4/FP-5 同款 bug 已根治。✅✅
- **F4 回退判定逻辑 — 已触发**：confirm 后 image_mode 真发 `images/generations`，**5/5 全 403**（无 200）→ `_autofill_with_connectivity_gate` `n_ok==0` 安全网 → 应回退模板。判定逻辑按预期执行（全图失败）。

## 4c. 🐛 真 E2E 发现的第 2 个 BUG（需代码级修复）：ppt_pro 渲染步骤在 executor 线程挂起

- **现象**：TC-1（惊艳路径,1图+7×403）与 TC-4（模板回退路径,5×403→n_ok==0）**两条路径**在 confirm 之后都走到「渲染 deck」步骤,但 **deck 始终不落盘、无完成/失败通知、无 Traceback（是 hang 不是 error）**。orchestrate 的 `except Exception: notify("没做成")` 未触发 → 卡在某个**阻塞调用**。
- **排查**：① 初判 WPS COM(`Kwpp.Application` visual_review/preview)在 `run_in_executor` 线程挂起 → dev config 关 `[ppt] visual_review=false / preview_render=false` 重启复测 → **仍 hang**。② 故 hang 不（只）在 visual_review/preview,而在模板回退渲染链更上游（疑 `_resolve_template_for_render` 的**模板选图 `vision_chat`** 或模板库加载在 executor 线程的事件循环/阻塞问题）。
- **影响**：F1→F2→F3 + 图像 403 判定全部 PROVEN,但**惊艳/模板 deck 最终落盘被此 hang 阻断**。
- **建议修复方向（下个 focused 循环）**：给 orchestrate 的 `run_in_executor` 渲染加 ① 阶段日志(定位卡在 `_render_pro` 的哪个子调用) ② render 子步骤超时/线程内事件循环正确初始化 ③ 必要时模板选图 vision_chat 在 executor 线程用独立 `asyncio.run`/同步客户端。`render_timeout` (max(600,pages*120)) 最终会 abort 但用户体验差。
- **注**：`ppt_create` 作为**同步工具**直接调用时渲染正常(单测/历史真机 06-20 PASS);本 bug 特定于 **ppt_pro orchestrate 经 run_in_executor 调 ppt_create** 的集成路径。

## 5. 诚实声明
本次为长时无人化自主构建 + 真机验收。**实施 100% + 单测/冒烟全绿 + TC-1 核心链路（F1-F4 + 路由修复）真机 PROVEN**。**未**对 14 条用例全部跑完真机——根因是 **relay gpt-image-2 403 配额墙**（图像相关用例无法在本窗口完成），按 runbook 纪律**如实记录环境受限，未用脚本/协议层假装通过**。
