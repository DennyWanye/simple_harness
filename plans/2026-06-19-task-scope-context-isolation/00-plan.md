# 任务作用域上下文隔离修复 — plan v1

> 2026-06-19 · 方向 D（改 compaction/召回的任务作用域）· spec-first

## 1. 现象

桌宠 companion 侧：用户让做「小学教育 PPT（深调研→大纲→AI 全屏 PPT）」，
agent 做着做着漂回到上一轮的「宁德时代 / CATL 年报」任务。

## 2. 根因（已用真实 state.db 实锤）

真实运行库：`backend/userdata/data/state.db`（dev `DESKPET_USER_DATA_DIR`）。

- companion 所有任务共用 **单一 `session_id="default"`，且从不重置**：
  872 条消息、5/16→6/16 整整一个月堆在一个会话里。
- 该会话被 CATL 主题压倒性主导：宁德时代×65、CATL×35、年报×31
  vs 小学×4、教育×33、PPT×19。
- `session_goals` 表为空 → **goal-anchor 漂移已排除**（不是它）。

漂移由两个"搬运工"把旧任务 framing 反复送回上下文，长链任务尤甚：

### Carrier 1 — compaction 滚动摘要的"任务棘轮"
`deskpet/agent/context_compressor.py`
- `_SUMMARY_SYSTEM`（:102-116）强制保留【意图/目标】【进行中/当前任务】，
  压缩窗跨 CATL+教育时会把 CATL（重头）记成"当前任务"。
- WI-3 增量摘要（:278、:319-324 `_extract_prior_summary` + prior 基底）
  指令"在此基础上增量更新，**不要丢已记录的任务/决策**" = **棘轮**：
  CATL 一旦进滚动摘要就**永不脱落**。这是结构性泄漏根源。
- 实锤：6/16 14:45 那条 assistant 摘要开头即「### 当前任务 用户让我针对宁德时代…」。

### Carrier 2 — L3 召回对同 session 零时近门控
`deskpet/memory/retriever.py`
- `_session_affinity`（:729-730）**同 session 一律 1.0**。整套跨 session 降权
  对本问题无效（CATL 与教育是同一 session）。
- 无时近 / 无当前主题门控 → 一个月前 CATL 记忆对"生成PPT"新查询满权召回。
- `messages` 表已有 `decay_last_touch` / `salience` 列，时近信号现成未用。

## 3. 修复方案（方向 D，最小侵入 + 安全默认回退）

### WI-1：compaction 摘要任务作用域化（context_compressor.py）
- 摘要分段改造：把【进行中/当前任务】严格绑定到 **最近一条用户请求** 的主题；
  与当前主题不一致的旧任务**降级**到【历史已结束任务】（一行带过，可丢），
  不得继续占【进行中/当前任务】。
- 打破棘轮：增量更新指令改为「**当最新用户请求切换了主题，旧的"进行中任务"
  转入"历史已结束"**，不再当作当前任务保活」。保留"不要丢关键事实/决策"，
  但任务连续性只跟随最新主题。
- BC：无 prior、单任务连续场景输出语义不变（仍保任务连续性）。

### WI-2：L3 同 session 时近性降权（retriever.py + config.py）
- `_session_affinity` 同 session 分支加 **时近性乘性衰减**：用 `decay_last_touch`
  / `created_at` 算 age，越旧权重越低（仍是降权非过滤 —— 保"桌宠记得你"）。
- 新 config：`[companion].memory_intra_session_recency_half_life_days`（或等价），
  **缺省 = 关闭（半衰期=∞ → 全 1.0，回退旧行为，Strangler-Fig）**。
- 与现有 `cross_session_decay` 正交叠乘。

### WI-3（可选叠加，先不做）
goal_mode ON + always-on 锚定当前任务 —— 留作后续，本次不引入。

## 4. 测试（TDD）

- 单测：
  - `test_context_compressor`：构造「CATL 旧任务 + 教育新请求」混合中段，
    断言摘要【进行中/当前任务】= 教育、CATL 落【历史已结束】；主题未切换时不降级。
  - `test_retriever`：同 session 两条记忆（旧 CATL / 新教育），断言旧的被时近降权后
    排序靠后；half_life=∞ 时回退全 1.0（BC）。
- live smoke：`scripts/e2e_*.py` 真实跑一遍压缩 + 召回，验证字段/单位不漂。
- 手工 E2E（项目 HARD 约束）：windows-mcp 真机——新建对话→旧 CATL 历史在场→
  发"做小学教育 PPT"→长链跑完截图，确认不漂回 CATL。**收尾前必须做，不可用脚本替代。**

## 4b. 实现进度（2026-06-19）

- ✅ **WI-1**（compaction 任务作用域化）— `context_compressor.py`
  - `_SUMMARY_SYSTEM` 加"任务边界优先"纪律 + 【早前已结束的任务】分段：
    【进行中/当前任务】只跟随最近一条用户请求，旧任务降级、不再保活。
  - 打破 WI-3 增量棘轮：prior 摘要拼接处把"不要丢已记录的任务/决策"改为
    "增量规则：【进行中/当前任务】以最新对话为准，旧当前任务移到早前已结束"。
  - 测试：`tests/test_deskpet_context_compressor.py::TestSummaryTaskScoping`（3 新）。
- ✅ **WI-2**（L3 同 session 时近降权）— `retriever.py` / `manager.py` / `main.py`
  - 新 `_intra_session_recency_weight`（0.5 ** (age/half_life)，带 floor）；
    `_apply_session_affinity` 对**同 session** 记忆乘时近权重；`recall` 加
    `recency_half_life_days` 参数；`_fetch_session_meta` 补 `created_at`。
  - **默认开启 = 7 天半衰期**（main.py 注入，config 可关）。
  - 测试：`tests/test_retriever_session_affinity.py`（7 新）。
- ✅ 回归：compaction+retriever+manager 全套 **219 passed**（203 + 16），无回归。
- ✅ **WI-2 后记修复**（retriever.py，commit 0d60dfe）：真机 E2E 暴露 recency 重排把
  无元数据(归档/stale 索引)id 顶进 top_k 把真命中饿死→空召回。修法：取全体候选
  元数据→按排名过滤→凑 top_k。真实 default 会话 recency-ON 0→8 hits 且偏好近期。

## 4c. 真机 E2E 结果（2026-06-19，windows-mcp）

环境：`backend/userdata`（含 CATL 污染 default 会话 872 msgs）+ 新代码
（日志确认 `[backend_launch] Dev python=...backend\.venv backend_dir=...\backend`，非 frozen）。
任务：发"中国现阶段小学教育...PPT，先深度调研..."到桌宠 companion（SendInput 圣杯点击
+ clipboard 粘贴，破 WebView2 穿透）。证据图 `screenshots/01,02-*.png`。

**结论：漂移已修，但全长链被 env 配置卡停（非漂移、非本修复）。**
- ✅ **零 CATL 漂移**：UI 面板可见 `todo_write`/`research_run` topic 全是"中国现阶段小学
  教育"，尽管同一可见历史顶部就有旧 `run_shell ...catl2024.pdf...`。日志全程
  `宁德|CATL|年报` **0 命中**。
- ✅ **compaction 触发 2 次**（旧代码漂移窗口），摘要未把 CATL 拽进当前任务。
- ✅ **WI-2 空召回回归** 被真机抓到并修复（单测小 db 漏的，正是真 E2E 价值）。
- ⚠️ **任务停在 iter=4** 因 `model_info: gpt-5.5 window=8000 source=global` →
  `token_budget_block (10370/8000)`。这是 **model 注册表 env 配置 bug**（gpt-5.5 真实窗口
  远大于 8000），会卡死**所有**长任务，与本修复无关 → 已登记跟踪。
- ℹ️ 旧有 `l3_failed TypeError("'>' int vs dict")`（vec 路径、safe-fail、非本次改动行）→ 已登记。

**未标 STATUS 完成**：漂移防护机制已真机验证，但完整长链（→大纲→生成 .pptx）因
env window=8000 未跑完；待修 gpt-5.5 窗口配置后可补一次完整长链 E2E。

## 5. 影响文件
- `backend/deskpet/agent/context_compressor.py`（WI-1）
- `backend/deskpet/memory/retriever.py`（WI-2）
- `backend/config.py`（WI-2 新 flag）
- `backend/tests/test_context_compressor*.py` / `test_retriever*.py`（新增/扩充）
- `STATUS/status.md`（完成后更新）
