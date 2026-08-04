# 方案 — 把 superpowers 结构化工作流集成进 Code 模式

> **状态**: 调研 + 方案(不改代码,等评审)
> **日期**: 2026-06-02
> **触发**: 用户反馈 code 模式 auto "奇奇怪怪"——问个问题它就埋头改代码、不先澄清、
> 不按计划、做完不验证就说完成、该并行的串行。
> **取向**: 用户明确"先调研+出方案,别急着改"。本文只给诊断 + 方案 + 决策点。

---

## 1. 用户痛点(原话)

> "我让他跟我说他的模型是什么,结果他就开始做直接事情了,埋头乱改、不先问需求,
> 不按计划/跑偏,做完不验证就说完成,该并行的串行、慢。"

拆成 5 条:
1. **意图误判** — 提问/闲聊被当成任务直接执行(问模型 → 开始改代码)
2. **埋头乱改** — 不先澄清需求和边界就动手
3. **不按计划/跑偏** — 没有"先定计划、人确认"这一步
4. **不停 / 做过头** — 没有清晰的"够了"信号
5. **不验证就说完成** — 声称做好了但没跑测试/自检,结果可能是错的;且做完不汇报"做了什么/还剩什么"

---

## 2. 现状诊断 — 这不是 LLM 笨,是执行架构缺了"工作流骨架"

调研(Explore agent + 核验关键文件)的结论:**code 模式是纯 ReAct 循环,没有任何
"先想清楚→定计划→执行→验证"的阶段**,而且讽刺的是——**superpowers 那套需要的零件,
deskpet 大半已经写好了,但全是孤儿代码、从没接进主流程**。

### 2.1 每个痛点的技术根因(已核验)

| 痛点 | 技术根因 | 落点(核验过的) |
|---|---|---|
| **#1 意图误判** | ① 任务分类器没有"低置信度时澄清/拒绝"策略,不确定就 fallback 到 chat;② 但 chat policy 仍开放全工具集;③ Code persona 通篇"**优先使用工具完成任务**",没有"先判断用户是提问还是派活" | `assembler/classifier.py`(rule+embed+llm 级联, 无 clarify 门)<br>`assembler/policies/default.yaml`(chat 也开全工具)<br>`assembler/components/persona.py:36-48`(已核验:强驱动行动,无意图判断) |
| **#2 埋头乱改** | persona 无"首轮先澄清需求/边界"指令;ReAct 循环立即启动,无"计划审批"关卡 | `persona.py:36-48`(核验:无澄清指令)<br>`agent_loop.py`(纯 ReAct 主循环) |
| **#3 不按计划** | `todo_write` 只是状态容器,不是流程引擎;`goal_store.py`/`goal_checker.py` **已存在但从未被 agent_loop/assembler 调用**(孤儿) | `goal_store.py` / `goal_checker.py`(孤立)<br>persona 只说"用 todo 拆步骤",无"先提交计划等确认" |
| **#4 做过头不停** | 只有 iter=10/20/30 的被动 selfcheck 提醒;persona 说"够用即停"但无客观停止判据;max 50 轮硬编码 | `agent_loop.py`(被动 selfcheck)<br>`persona.py:46`(核验:"够用即停"= 模糊) |
| **#5 不验证就说完成** | `outcome_verifier.py`(FileExists/GitDiff/Build/Test 验证器)、`verify_gate.py`(声明验证)**都已存在,但只在工具级 receipt 校验里用,没接进"任务完成"全流程**;persona 只要求"给简短总结",不要求"跑验证" | `outcome_verifier.py` / `verify_gate.py`(工具级,非任务级)<br>`persona.py:47-48`(核验:只要总结,不要验证) |

### 2.2 关键洞察:**一半是配置/persona,一半是"接孤儿代码"**

- 痛点 #1/#2/#4 主要是 **persona 措辞 + 缺意图门** → 改 prompt + 加一个轻量意图判断就能大幅缓解
- 痛点 #3/#5 的"零件已存在"(goal_checker / verify_gate / outcome_verifier)→ **不用重写,接电即可**

这意味着集成的性价比很高——不是从零造工作流引擎,是"补 persona + 接已有孤儿件"。

### 2.3 ⚠️ 重要纠正(2026-06-02 实现期核验后)

**我在 §2.1 把 verify_gate / goal_checker 称作"孤儿、从未被调用"——这是错的,该纠正。**
逐文件核验后真相是:

- `agent_loop.py` 的 FinalEvent 守门处(955-1141 行)**早已接好三道关卡**:
  `verify_gate`(end_turn 守门+rebound+ephemeral 救援+metrics)、`goal_checker`+
  `session_goal_store`(/goal rebound)、`completion_probe`(todo 守门)。
- `main.py` 的 `build_agent` 工厂(620-737)**已构造并注入** verify_gate / receipt_store /
  goal_checker;chat_v2 handler(4880-4897)**已调用**。
- **真正的卡点是配置 flag 默认关**:`cfg.tools.verifier.verify_gate_mode = "off"`(默认)
  → verify_gate=None → 假完成抓获率 **0%**;`features.goal_mode = False`(默认)→ goal_checker 不构造。
  **这正是本项目反复出现的"功能写了但 flag 默认 off / 构造传 None"模式**(§7 已记),我先前误判成"没接电"。
- **唯一真没接进 end_turn 的**是 `outcome_verifier`(真跑 build/test/git_diff)——它只在工具级被引用,
  没进 agent_loop 的完成守门。这才是真正需要写代码接电的一块。
- ⚠️ `verify_gate` 的 `claim_patterns.yaml` 只匹配**产物生成 claim**(已生成 X.pptx / 已保存 path /
  I have created X.ext),**不覆盖** edit_file 改代码、"测试通过了"等 → 对 code 模式是**部分覆盖**,
  不是完整"代码做完跑测试"硬卡。

**结论修正**:"硬卡验证"大半是**翻配置开关**(verify_gate_mode → shadow/strict、goal_mode → true),
不是接电写代码;真正要写代码的是 outcome_verifier 接进 end_turn(若要"代码做完真跑测试"级硬卡)。

---

## 3. superpowers 工作流要做到什么

superpowers 的核心是 4 个阶段 + 一条纪律:

1. **Brainstorm(澄清)** — 先把需求、边界、成功标准问清楚,**不急着写代码**
2. **Write Plan(计划)** — 落一份 todo/spec,**人确认后**才执行
3. **Execute(执行)** — 按计划做;**独立子任务用多子代理并行**(deskpet 已有 agent_parallel)
4. **Verify(验证)** — 做完**跑测试/自检**,报告"做了什么、验证结果、还剩什么",不"看起来对就交"

外加一条贯穿纪律:**意图门**——先分清用户是"提问/闲聊"还是"派活",提问就老实回答、别动手。

---

## 4. 集成方案 — 渐进三层(强烈建议按层推进,先验证再加深)

### Layer 1 — Persona 注入工作流纪律 + 意图门(最轻、最快见效)

**改什么**:
- 重写 `_CODE_MODE_PERSONA_TEMPLATE`(persona.py:36-48),把"优先使用工具"换成**工作流纪律**:
  ```
  收到消息先判断意图:
    · 如果是提问/闲聊(问你用什么模型、解释概念...)→ 直接回答,不要调工具改东西
    · 如果是派活 → 进入工作流:
        1. 需求不清先问澄清(目标/边界/成功标准),别埋头猜
        2. 先 todo_write 列出计划,简述后再执行
        3. 执行中保持 todo 最新
        4. 完成后必须自检(跑测试/检查产物),报告"做了什么+验证结果+还剩什么"
  ```
- 在 classifier 加一个"低置信度 → clarify"倾向(或在 chat policy 收窄默认工具,不确定时不给写工具)

**成本**: 小(改 persona 模板 + 可能微调 policy)。**可逆**。
**收益**: 直接缓解 #1/#2/#5 大部分(意图门 + 先澄清 + 完成后自检都是 prompt 能约束的)。
**局限**: persona 是"建议",LLM 可能忽视——所以这是"先验证方向"的一层,不是终态。

### Layer 2 — 接电已有孤儿件(goal_checker + verify_gate)

**改什么**(都是"接电",不是新写):
- agent_loop 在 FinalEvent 前调 `goal_checker` 估算"任务真完成了吗",没完成就继续/提示(治 #4 做过头/没做完)
- agent_loop 在收尾调 `outcome_verifier`(FileExists/Test/Build)对照 todo 做客观验证,把结果合成进总结(治 #5 不验证)
- `goal_store` 存当轮的目标/计划,execute 阶段对照(治 #3 跑偏)

**成本**: 中(接线 + 测试,代码大半已存在)。
**收益**: #3/#5 从"靠 prompt 自觉"升级为"有客观验证关卡"。

### Layer 3 — 工作流编排器(最彻底,可选)

**改什么**:
- 新增 `workflow_orchestrator.py`,在 code 模式 agent 前显式跑 brainstorm→plan(审批)→execute(可 agent_parallel 并行)→verify 四阶段
- 可用 slash 命令手动触发阶段(`/plan` `/execute` `/verify`),或自动串

**成本**: 大(改核心执行流 + 充分测试)。
**收益**: 真正的结构化工作流,不靠 LLM 自觉。
**风险**: 改动大、影响所有 code 会话,需端到端测试(正好本项目缺这套 smoke)。

### 推荐路径
**先 Layer 1(改 persona + 意图门),真机验证效果** → 满意度提升后再上 Layer 2(接孤儿件)→ 确有必要再考虑 Layer 3。
理由:80% 的难受(问问题被当任务、埋头不问、不验证)是 persona + 意图门能解决的,先用最小改动验证,避免一上来动核心 loop。

---

## 5. 决策结论(已拍板 2026-06-02)

| # | 决策点 | 你的选择 | 含义 |
|---|---|---|---|
| 1 | **意图门** | **多问澄清,但记录用户意图,后续相同的直接做** | 不是静态门,是**带学习记忆的门**——第一次问、记下来、相似请求免问直接执行 |
| 2 | **计划审批** | **先人工确认,但记录用户习惯,后续相同的直接做** | 同上,plan 审批也带记忆——第一次等点头、记下来、相似任务免审批直接做 |
| 3 | **验证强度** | **硬卡** | `verify_gate` 不通过 = 任务不算完成,不能直接说"做好了" |
| 4 | **slash 命令** | **暂时手动触发**(不自动串工作流) | 顺带:补 slash 自动补全下拉(见 §5.2) |

### 5.1 决策 1+2 的关键升级:这需要一个"偏好记忆"组件(新增)

你对意图门和计划门都选了**"第一次问/确认,后续相同的直接做"**——这把方案从"静态 persona 注入"升级成**带学习的门控**。需要新增一个轻量持久化组件:

**`preference_memory`(偏好记忆)**——存"用户对某类请求的决定":
- **意图记忆**:记 `{请求特征 → 用户真实意图(提问/派活)}`。例:用户问"你用什么模型"→ 第一次澄清"你是想知道还是想让我改配置?"→ 用户答"只是想知道"→ 记下"这类元信息提问 = 纯提问,别动手"→ 后续同类不再问。
- **计划记忆**:记 `{任务类型 → 用户批准的计划模式}`。例:第一次"加个 API 端点"先给 plan 等确认 → 用户点头 → 记下"这类任务用户认可直接按标准 plan 做"→ 后续同类任务跳过审批门。

**匹配方式**(待定,§6 决策):
- **轻量**:关键词/任务类型精确匹配(实现简单,泛化弱)
- **中等**:embedding 相似度(deskpet 已有 BGE-M3 → 直接复用,泛化好但要调阈值)
- **存储**:可复用现有 `goal_store` 的 session 存储模式,或单独 `~/userdata/preference_memory.json`

> ⚠️ **这是本方案最大的新增工作量**,也是和原始"纯 persona 注入"最大的区别。决策 1/2 让 Layer 1 不再是"改 prompt 就完",而是 "改 prompt + 加偏好记忆组件"。

### 5.2 slash 自动补全(决策 4 顺带,回答你的图片问题)

**现状**:deskpet code 输入框**没有**输入 `/` 弹候选菜单的功能(图片里那个是 Claude Code 自己的)。
- 后端命令清单现成:`/help` 已能返回 builtins + skills 列表([commands/__init__.py:71-87](../../backend/deskpet/commands/__init__.py))
- 前端只在 [InputBar.tsx:62-75](../../tauri-app/src/code-panel/InputBar.tsx) 检测 `/` 开头就发出去,**无下拉 UI**

**方案**:前端给 InputBar 加一个 autocomplete dropdown——
- 用户敲 `/` → 拉一次命令清单(复用 `/help` 数据或新增 `slash_list` WS)→ 渲染下拉
- 输入字符实时过滤、↑↓ 选择、Enter/Tab 补全
- 纯前端组件 + 一个列命令的接口,**不碰 agent loop**,风险低,可独立做

---

## 6. 重新划分后的实施路线(按决策更新)

决策 1/2 的"记忆"需求让 Layer 1 变重,重新分层:

### Layer 1A — Persona 工作流纪律 + 硬卡验证(纯 prompt + 翻 flag)
- ✅ **已做**:重写 Code persona(`persona.py:36-58`)——意图门(先分清提问/派活)+ 先澄清 +
  先 plan 等确认 + 完成前必自验证(治 #1/#2/#3/#5 的"自觉"层)。
- ✅ **已做**:dev config 翻 `verify_gate_mode = "shadow"` + `emit_receipts = true`
  + `goal_mode = true`(原本默认 off → verify_gate/goal_checker 现在真活)。
- 🟡 **shadow→strict**:shadow 观察 patterns 对 code 真实场景不误杀后,翻 strict 真硬卡(决策 3)。
- ⬜ **可选加深**:把 `outcome_verifier`(真跑 build/test)接进 end_turn 守门 —— 唯一需写代码的一块,
  做"代码改完真跑测试"级硬卡。先看 persona 自验证够不够,不够再上。

### Layer 1B — 偏好记忆组件(决策 1/2 的核心,新增)
- 新增 `preference_memory`:意图记忆 + 计划记忆,存盘
- 意图门/计划门第一次问→记→后续相同免问
- 匹配方式先用关键词精确匹配(MVP),效果不够再上 BGE-M3 embedding

### Layer 2 — slash 自动补全 UI(决策 4,纯前端,可并行/独立)
- InputBar 加 `/` 触发的命令补全下拉

### Layer 3 — 工作流编排器(可选,暂不做)
- 决策 4 选"暂时手动触发",所以**先不做**自动四阶段编排器

### 推荐执行顺序
**Layer 1A(persona+硬卡验证)先做、真机验证** → 再 Layer 1B(偏好记忆)→ Layer 2(slash 补全)可任意时候并行插入。
理由:1A 改动相对收敛、立刻能验证"问问题不再乱改 + 做完真验证";1B 是新组件、要单独设计测试;2 是独立前端活,不阻塞。

---

## 7. 风险与待核验(诚实标注)

- ⚠️ Explore agent 给的部分行号(如 `main.py:4246-4445`、`agent_loop.py:558`、`max_iterations=50`)**实现时需逐一核验**,本轮只核验了 persona.py:36-48(准确)。
- ⚠️ **slash 命令的实际实现**没查全(`commands/__init__.py` dispatch_slash 现状),Layer 3 用它前要先摸清。
- ⚠️ classifier 的 rule 正则(可能把"写代码"类词误判)需要看实际命中率,Layer 1 调 persona 时一并看。
- ⚠️ companion v1 的 slash/goal flag 刚修好接电(commit 5cd1195),`goal_mode` 的 goal_checker 路径可能和本方案 Layer 1A 重叠——实现前要确认两者关系,别重复造。
- ⚠️ **决策 3 硬卡的误杀风险**:`verify_gate` 太严会把"其实做完了"误判成未完成,卡死会话。需要"验证失败时给清晰理由 + 允许用户一句话 override"的逃生门。
- ⚠️ **偏好记忆的误记风险**(决策 1/2):一旦记错用户意图(把"提问"记成"派活"),后续会一直自动跑错。需要"记忆可查看/可清除"+ 记忆只在高置信度时写入。
- 本项目反复出现"功能写了但没接电/没端到端测"的模式(本轮已修 5 个同类 bug),**落地时必须配端到端 smoke**,否则又是孤儿件。

---

## 8. 下一步

决策已定(§5)。建议执行顺序:**Layer 1A → 1B → Layer 2 可并行**。落地前还需:
- **若走 spec-first 正式流程**(推荐,因为含新组件 preference_memory + 改核心 loop)→ 把本 proposal 拆成 TDD + 手测用例 + 分阶段 WI
- **偏好记忆的匹配方式**(§5.1):先确认走"关键词精确匹配 MVP"还是直接上"BGE-M3 embedding";建议 MVP 先跑通,不够再升级
- **若先快验证** → 先做 Layer 1A(persona + 硬卡验证),dev 真机测"问模型不再乱改 + 做完真验证不过不算完成",验证方向对了再投入 1B 偏好记忆
