# Layer 1A 真机 E2E 测试报告

> **日期**: 2026-06-02
> **被测**: 重写后的 Code persona(意图门 → 澄清 → 计划 → 执行 → 验证)+ verify_gate shadow
> **方法**: CDP 驱动**真实 code-panel UI**(localhost:9222)——真 textarea 注入 value +
> dispatch input 事件 + 点真「发送」按钮 + 读真渲染 DOM + 截图。全栈走
> React InputBar → codePanelWS → backend chat_v2 → AgentLoop(新 persona) →
> chinzy relay / **deepseek-v4-pro** → 渲染回 DOM。
> **非** ws 直连 backend 注入、**非** 脚本回放 resolution 函数——这是项目既定的
> WebView2 真 E2E 方法(见 `plans/manual-results-2026-05-26-master/UI_AUTOMATION_BREAKTHROUGH.md`)。
> **环境**: dev 真机,`DESKPET_BACKEND_DIR=backend`(跑改动后的源码,非 frozen exe),
> 已登录(chinzy relay `GET /v1/models` 200),BGE-M3 `is_mock=False device=cuda`。

---

## 准入确认(改的是真源码,不是旧 frozen)

- backend 日志 `verify_gate_init mode='shadow' patterns=5 path=.../verify/claim_patterns.yaml`
  → 我翻的 `[tools.verifier]` 配置**真生效**,build_agent 工厂接电成功。
- `llm_config_updated base_url='https://chinzy.com/v1' model='gpt-5.5'` + relay 200 → LLM 链路通。
- code 模式活跃(2 个项目 tile),测试在 **test-research-helper**(deepseek-v4-pro)tile。

---

## TC-1 · 问模型不该乱动手(治痛点 #1 意图误判)

| 字段 | 值 |
|---|---|
| 动作 | 真 textarea 注入 "你用的是什么模型？" → 点真「发送」 |
| 截图 | `TC-1-model-before.png` / `-after.png` |
| **DOM 渲染回复** | **"AI:我用的是 deepseek-v4-pro。"**(一句话直答,无工具卡片) |
| backend 日志证据 | 全程 `stop_reason='end_turn' tool_calls=0`,**无 dispatch / tool_result** |
| **判定** | ✅ **PASS** — 问模型 → 直接答模型名,**零工具调用、没乱改任何东西** |

> 这正是旧 persona("优先使用工具完成任务")失败、用户抱怨的场景:
> "我让他跟我说他的模型是什么，结果他就开始做直接事情了"。新 persona 的意图门修好了。

## TC-2 · 模糊派活先澄清(治痛点 #2 埋头乱改 / #3 不按计划)

| 字段 | 值 |
|---|---|
| 动作 | 真 textarea 注入 "帮我优化一下代码"(故意模糊)→ 点真「发送」 |
| 截图 | `TC-2-vague-after.png`(清晰显示澄清问题) |
| **DOM 渲染回复** | "可以。你想优化哪部分代码？请补充…1.**目标** 2.**范围** 3.**成功标准** 4.重点文件路径。**确认后我会先列一个简短计划给你看，再按计划修改和验证**…" |
| backend 日志证据 | `tool_calls=0` end_turn;`git status` test-research-helper **干净**(零文件改动) |
| **判定** | ✅ **PASS** — 先澄清(目标/范围/成功标准)+ 没埋头改 + 预告了 计划→确认→验证 流程 |

## TC-3 · 明确小任务:执行 + 自验证(治痛点 #5 不验证就说完成)

| 字段 | 值 |
|---|---|
| 动作 | 真 textarea 注入 "在项目根目录创建一个文件 hello_layer1a.txt，内容只写一行：hi from layer1a" → 点真「发送」 |
| 截图 | `TC-3-cleartask-after.png` |
| **backend 日志全序列** | ①计划说明(779字) → ②`todo_write`(拆步骤) → ③`list_directory`(看现状) → ④`write_file` 创建 → ⑤**`read_file` 读回验证** → ⑥`todo_write` 全 completed → ⑦end_turn 总结 |
| 产物 | `G:/projects/test-research-helper/hello_layer1a.txt` = "hi from layer1a"(真创建 ✓) |
| **DOM 渲染回复(末段)** | "…hello_layer1a.txt`  **校验结果**：文件内容为唯一一行：`hi from layer1a`" |
| **判定** | ✅ **PASS(验证纪律)** — todo 拆步骤 + 写前看现状 + 写后**读回验证** + 报告校验结果 |
| ⚠️ 轻微 gap | 未显式停下来等"确认后再执行"(决策 2 的 plan-confirm 硬门);对 1 文件的 trivial 任务它判断为可直接做。若要严格 plan-confirm,需 persona 加强措辞或上 Layer 1B 的 plan-gate。 |

---

## 结论

- **3/3 用例 PASS**,三重证据(DOM 渲染 + backend 日志 + 截图/git/产物文件)。
- persona 重写**真机生效**:意图门(#1)、先澄清(#2)、计划+执行+读回验证(#3/#5)都按设计跑出来了。
- verify_gate shadow 已激活(patterns=5),本轮无 nudge(产物 claim 与 patterns 未触发误判)→ 可安全观察后翻 strict。
- **已知小 gap**:TC-3 没走"plan 等确认"硬门 → 决策 2 的严格审批留给 Layer 1B(plan-memory + confirm-gate)或 persona 加强。

## 测试副产物(待清理)

- `G:/projects/test-research-helper/hello_layer1a.txt`(TC-3 创建的测试文件)
- test-research-helper 会话里 3 条测试消息历史
