# WI-5 知识片段 trigger 运行时不命中 — 修复 + 真机复验结果

> 日期：2026-06-20。承接 [`plans/2026-06-20-agent-loop-optimization/HANDOFF.md`](../2026-06-20-agent-loop-optimization/HANDOFF.md) §3 遗留 bug。

## 根因（一句话）

`main.py` 构造 `SkillLoader` 时**漏传 `knowledge_enabled`**，loader 恒用默认 `False` →
`reload()`（`loader.py:308`）把所有 `user-invocable: false` 的知识片段挡在快照外 →
`loader.all()` 永远只有 12 个常规技能，3 个知识片段从未进候选 → matcher 无从匹配。

此前的 `d7da6e5` / `011aab5` 修的是 assembler/SkillComponent 的 per-turn config 流通，
但那是在「过滤一个本就为空的子集」——源头 loader 没放行，下游再对也没用。

## 诊断证据（loader 层直证）

`diag_wi5.py` 用 main.py 同款方式构造 loader，分别跑两个 flag：

| knowledge_enabled | loader.all() total | 含知识片段？ |
|---|---|---|
| False | **12** | 否（ppt-tips / source-check / windows-path-debug 全缺） |
| True | **15** | 是，且 triggers 正确（windows-path-debug = 路径/windows/反斜杠/backslash/path） |

→ 真机 HANDOFF 症状 `total=12` 即 loader 跑在 False 上的铁证。

## 修复

`backend/main.py` `_SkillLoader(...)` 增 `knowledge_enabled=bool(config.skills.knowledge_enabled)`
（默认 False 保 BC）。

## 真机复验（windows-mcp，DESKPET_DEV_MODE=1，LLM key 从 keychain 读）

启动日志 HARD GATE 通过：`[backend_launch] Dev python=G:/projects/deskpet/backend/...`（跑 worktree 源码非 frozen exe）。

### case WI5-R1：boot 时 loader 含知识片段

- 期望：loader count 从 12 → 15
- 证据（`codex/tauri-dev8.log`）：
  ```
  event='skill.reload_ok' count=15
  event='p4_skill_loader_ready' count=15
  ```
- 判定：**PASS**

### case WI5-R2：code 消息触发知识片段注入（HANDOFF 原症状用例）

- 坐标：输入框 (3444,1509)，发送 (3714,1509)
- 动作：Click 输入框 → Clipboard「我在windows下用反斜杠路径老是报错，怎么办」→ Ctrl+V → Click 发送
- 截图：`screenshots/wi5-trigger-windows-path-debug.png`（桌宠「努力工作中」+ web_fetch 卡，端到端响应）
- backend log 证据（`codex/tauri-dev8.log:178-180`）：
  ```
  assembler_task_classified task_type='code' prefer=[..., 'skill', ...]
  skill_auto_disclosed total=15 strong=1 auto_loaded=1 names=['windows-path-debug'] top_sim=0.950
  ```
- 对照 HANDOFF 修前同句：`total=12 strong=0 auto_loaded=0 names=[] top_sim=0.479`
- 判定：**PASS**（知识片段 windows-path-debug 经 trigger 命中并 body 注入上下文）

## 回归测试

- 新增 `tests/test_wi5_trigger_inject.py::test_real_loader_registry_injects_knowledge_via_trigger`
  （用真 SkillLoader 当 registry，走 `loader.all()`，捕获本 wiring bug）
- 新增 `..::test_real_loader_without_flag_drops_knowledge`（对照：loader 不开 flag → 知识片段不进快照）
- 全套：`test_wi5_trigger_inject.py`(8) + 各 WI + assembler + agent_loop BC 套件全绿（73 + 34）。
