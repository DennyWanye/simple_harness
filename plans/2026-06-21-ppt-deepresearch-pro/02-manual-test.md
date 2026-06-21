# PPT Pro — windows-mcp 真机手测用例

> 配套 [00-PLAN.md](./00-PLAN.md)。**项目硬纪律**：改完代码必须 windows-mcp 真模拟人工点击 + 截图 + 抓 backend log，不能用 pytest/WebSocket/import 替代（见根 CLAUDE.md §手工测试纪律）。
> **前置**：干净重启 Dev python backend（注入 `DESKPET_BACKEND_DIR=<repo>/backend` 跑当前码，日志确认 `[backend_launch] Dev python`）；relay 已登录（dev 自动登录或手动）；gpt-image-2 key 在 keychain。
> **flag**：dev config 开 `[ppt].pro_enabled=true`。

每个 case 报告格式：`case / 坐标 / 动作 / 截图 / backend log 证据 / 判定`。截图存 `plans/manual-results-2026-06-21-ppt-pro/screenshots/`。

---

## TC-1 ★惊艳全链路（F1+F2+F3+F4 主路径）
**步骤**：桌宠输入框输「帮我做一份『钠离子电池 2025 产业现状』的惊艳 PPT，8 页」→ Enter。
**期望**：
1. 桌宠秒回「先做调研…拟好大纲弹给你确认」（handler 秒回 `status:researching`）。
2. backend log：`deepresearch` 真调用（子问题拆解 + 多源命中 + `n_sources>0`）+ 进度通知「📚 调研完成（N 个来源）」。
3. 弹出**确认卡**（ClarificationDialog）含**多行大纲**（页码+标题+bullets，内容含调研里的真实数据/事实，非空泛）。
4. 真坐标点击「✅ 确认生成」。
5. backend log：gpt-image-2 `images/generations` 真 200 多次出图；`_render_pro` 惊艳路径。
6. 成品落 `OutPut/PPT/*.pptx`，预览图 artifact 进聊天，自动打开（WPS），首页/内容页是 AI 整页配图惊艳风。
**判定**：PASS = 调研真发生 + 大纲有据 + 确认卡可见 + 点确认后真出图惊艳 deck 落盘。

## TC-2 修改环（F3「可改」+ MAJOR-3 防整盘重拟）
**步骤**：TC-1 弹确认卡后，点「✏️ 让我改改」→ 出现 textarea → Clipboard 输「把第 3 页换成与磷酸铁锂的竞品对比，其余页不要动」→ 点「提交修改」。
**期望**：
1. backend log：编排 task 收到 feedback，`_draft_outline_from_research(feedback=..., prev_slides=...)` 重拟。
2. **新确认卡**：第 3 页变为竞品对比，**其余页与上一版基本一致**（验证增量修订非整盘重拟）。
3. 点「确认生成」→ 正常出图。
**判定**：PASS = 第3页按要求改 + 其余页未被推翻 + 再确认后生成。

## TC-3 取消（F3 取消路径）
**步骤**：弹确认卡 → 点「✖ 取消」。
**期望**：桌宠回「已取消，没有生成 PPT」；`OutPut/PPT/` 无新文件；编排 task 干净结束（log 无 Traceback）。
**判定**：PASS = 不生成 + 友好提示。

## TC-4 ★★连不上 gpt-image-2 回退模板（F4 核心一票否决）
**制造不可达**（三选一，按可行性）：
- (a) 临时把 relay images 端点改成错地址 / 关代理使 gpt-image-2 不可达；
- (b) dev 注入环境让 `probe_image_reachable` 返 False；
- (c) 临时改 image 配置指向不存在的 model（触发 4xx model_unavailable）。
**步骤**：输「做一份『量子计算入门』惊艳 PPT，6 页」→ 确认大纲 → 点确认。
**期望**：
1. backend log：probe 不可达 **或** 首图返回 `error_kind=connectivity/model_unavailable` → `_render_pro` 切 `use_template=True`。
2. 桌宠通知「⚠️ AI 配图暂时连不上/用不了，已切换精美模板生成」。
3. 走模板路径出**完整美观 deck**（每页有充实 bullets，**无占位残页**、无「image placeholder」）。
4. `_degrade_to_template` 清了 image_prompt → log **无第二次** `images/generations` 调用（不二次烧图）。
**判定**：PASS = 检测到不可达 + 回退模板 + 出完整无占位 deck + 不二次烧图。**这是 F4 一票否决项。**

## TC-5 调研降级（F1 失败知情确认）
**制造**：临时让 deepresearch 搜索全 0 源 / 超时（断网搜索或缩短 `pro_research_timeout_s`）。
**期望**：
1. research 返回 None（网络类失败，不上抛）。
2. 确认卡问题文案显式含「⚠️ 未取得调研来源，下面大纲基于通用知识，是否仍要生成？」。
3. 用户确认后仍能出 PPT（功能不缺，只是无来源）。
**判定**：PASS = 用户被告知无来源 + 知情后可继续。

## TC-6 配置/认证错误上抛（F1 不静默编）
**制造**：临时清掉 LLM key（模拟配置错）。
**期望**：research 的 `_is_config_or_auth_error` 命中 → 上抛 → 编排 task 推「调研服务没配好…」给用户，**不**静默退化成凭空编大纲。
**判定**：PASS = 明确报配置问题，不假装调研。

## TC-7 preempt 不杀确认链路（R-4 回归，★关键）
**步骤**：TC-1 弹确认卡后**先不点**，在桌宠输入框另发一句无关消息「现在几点」→ Enter（触发 same-sid 新 chat task）。
**期望**：
1. 新消息正常被回答（「现在几点」）。
2. **确认卡仍在、仍可点**；回到确认卡点「确认生成」→ 编排 task 仍存活、继续出图（独立 task 未被 preempt cancel）。
**判定**：PASS = 新消息不杀确认链路（验证 §2.5 独立 task 修复）。**这是 R-4 BLOCKING 的真机验证。**

## TC-8 BC（旧 ppt_create 不受影响）
**步骤**：输「直接用这个大纲做 PPT：[贴一份 outline]」或「快速做个 3 页朴素 PPT」（引导走 ppt_create 而非 ppt_pro）。
**期望**：走旧 `ppt_create` 路径，行为与改造前一致（同步/异步、模板/生图照旧）。
**判定**：PASS = 旧路径字节级不变。

---

## 通过线
- TC-1/TC-4/TC-7 为 ★ 必过（覆盖 F1-F4 主路径 + 回退一票否决 + preempt 修复）。
- 任一 ★ FAIL → 不算完成，回 plan 修。
- 全 PASS 后更新 `STATUS/PPT.md` + `STATUS/status.md`（项目硬纪律）。
