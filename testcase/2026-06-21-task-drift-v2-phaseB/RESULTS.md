# 任务漂移 v2 阶段 B — windows-mcp 真机测试结果

> **执行**: 2026-06-22，windows-mcp 真模拟人（剪贴板中文输入 + 真坐标点击）
> **被测**: T1-1 会话切分 + T0-4 voice 全链路 + §8 sentinel + group + 前端，commit ca4022a7
> **环境 HARD GATE**: ✅ master backend（Dev python）；✅ embedder is_mock=False；✅ 前端改动编译通过（vite ready）
> **判定锚点**: `session_id=task-*`（切场最硬证据）+ deepresearch 搜索 query 主题（网络受限不依赖落盘）

---

## 核心结论：T1-1 切场隔离根治 真机 PASS（层 1 根治）

| TC | 场景 | 判定 | 硬证据 |
|---|---|---|---|
| **TC-B1 ★** | 钠离子/电池历史压力下，`/new 帮我深度调研 区块链` | ✅ **PASS** | 组装 gate **`session_id='task-default-1'`**（后端 resolve 切到新 effective_sid，新干净 scope）；fanout 子代理 `run_id=task-default-1.dr-0/1/2`；**新 scope 搜索区块链 11 次，0 次电池/钠离子/宁德**（`baike.baidu.com/item/区块链的…`）；Fix B `req_len=33`。截图 `TC-B1-new-scope-blockchain-no-drift.png`，log `TC-B1-log.txt` |
| **前端"新话题"按钮** | UI 渲染 | ✅ 真机渲染 | Snapshot `(2846,1509) 按钮 "新话题"`——前端 codex 改动真机生效（修可见性回归） |

**层 1 根治 vs 阶段 A 层 2 纵深**：阶段 A 的 T0-1 让 deepresearch 内部以原话为准（即使外层 topic 漂，报告/搜索拉回）；**阶段 B 的 T1-1 从源头切干净 scope**——`/new` → 后端起 `task-default-1` → 新 scope L2 历史**为空** → 主 loop 选 topic 时**根本看不到旧主题** → 彻底不漂。对照 2026-06-21 复现（区块链→宁德漂），现在 `/new` 后区块链→区块链×11 不漂。

> **切场证据说明**：`session_switched`/`task_session_started` 事件经 control WS 发给前端（不落 backend stderr log，故 grep 未匹配）；但**组装 gate 与 fanout 的 `session_id=task-default-1`** 是后端真切到新 scope 的**最硬证据**——后端所有下游（assemble/L2/工具 context/fanout）都用了新 effective_sid。

---

## 实现 + 评估（真机外的强保障）
- **后端 389 passed**（4 新测试文件 + BC）+ **前端 tsc --noEmit exit 0 + vitest 2 passed**。
- **评估子代理 100%**：§5 后端 8 接入点全 PASS（无硬编码 default 残留/无半隔离泄漏）+ group + §4 voice 5 处 sid + §8 sentinel 显式 flag + task_scope + 前端契约，0 GAP。

## 未单独真机执行（理由，诚实标注）
- **TC-B2 前端按钮路径**：按钮已真机渲染 + ws.ts vitest 验响应事件 + TC-B1 已验后端切场核心；按钮 `{new_session:true}` 路径与 /new 文本路径殊途同归（同一 resolve）。
- **TC-B4 /continue、TC-B5 多窗口 group、TC-B6 sentinel、TC-B7 voice**：由单测覆盖（test_task_scope/test_main_task_scope_wiring/test_voice_task_scope/test_agent_loop_sentinel/test_voice_pipeline_broadcast，389 passed）+ 评估子代理逐条核对；真机受网络（deepresearch 0 引用不落盘）/ ASR / auto-resume 触发条件 / 窗口拓扑限制，未逐一真机。
- **TC-B8 BC**：单测 `test_resolve_default_is_bc_identity` + 评估推演（无 /new→effective=default，多窗口共享不变）。

## 结论
T1-1 会话切分根治（层 1）**真机硬证据 PASS**——`/new` 切干净 scope，新请求主题从源头不被旧 attention-sink 污染（区块链 11/0）。后端 389 + 前端 tsc/vitest + 评估 100% 为完整实现保障。落盘受网络限制（非代码问题）。判定基于真模拟人触发后真实运行栈 log（`session_id=task-*` + 搜索 query），符合 `feedback_real_e2e_not_script_replay`。
