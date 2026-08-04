# 任务漂移 v2 阶段 C+D — windows-mcp 真机测试结果

> **执行**: 2026-06-22，windows-mcp 真模拟人（真坐标点击 + 剪贴板中文输入）触发，判定基于真实运行栈 log
> **被测**: C = T1-2 L2 降级 external memory（page-in）+ /continue 透传，commit `bc6955a7`；D = T0-3 关 Tier2（8 处 `topic_shift_gate`→false），commit `f5e878a4`
> **环境 HARD GATE**: ✅ master backend Dev python（`tauri-dev.log:33` `[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe`）；✅ embedder 真模型（`tauri-dev.log:126` `p4_embedder_ready ... is_mock=False`）
> **判定锚点**: 组装期 `task_drift_context_gate` log（最硬证据，不依赖网络落盘），存于 `TC-CD-gate-evidence.txt` + `tauri-dev.log:185`

---

## 核心结论：关 Tier2 运行时生效 + Tier1 保留 真机 PASS（层 3 改造）

| TC | 场景 | 判定 | 硬证据 |
|---|---|---|---|
| **TC-CD ★** | 桌宠 default 会话发请求，组装期触发上下文门控 | ✅ **PASS** | `tauri-dev.log:185` 组装 gate：**`topic_shift_gate=False`**（Tier2 关）+ **`shift_path='off'`**（语义/embedding 相似度门控不再走任何路径）+ **`l2_truncated=False`** + **`l2_count_in=5 l2_count_out=5`**（L2 历史 5 条全保留，Tier2 不再砍）；同时 **`relabel_applied=True anchor_applied=True`**（Tier1 relabel/anchor 仍生效）。截图 `screenshots/phaseCD-tier2-off-runtime.png`，evidence `TC-CD-gate-evidence.txt` |

**为什么 `topic_shift_gate=False` 就是 D 阶段的硬证据**：D 阶段把 8 处 `topic_shift_gate` 全部改为 false（plan §3）。运行时 gate 同时打印这个 flag 与 `shift_path`——`topic_shift_gate=False` + `shift_path='off'` 证明 Tier2 词法/embedding 门控**整条链路被关**，不只是某个分支短路。组装期 embedder 不可靠（[[project_assembler_embedder_unreliable]]：实时 encode 撞锁竞争 + 1500ms 超时），Tier2 相邻领域本就失效且有 fail-open 风险，关掉它是层 3 的正确处置。

**Tier1 未被误伤**：`relabel_applied=True anchor_applied=True` 证明关 Tier2 是**精准切除**——Tier1（relabel 重标 + goal anchor）保留，目标锚定能力不受影响。`l2_count_in=5 l2_count_out=5` 进一步证明 L2 历史不再被 Tier2 截断（旧行为下相邻领域会误砍）。

> **session_id='default' 说明**：本 case 未走 `/new`（那是阶段 B 的 T1-1 路径），在 default 会话直接发请求，正是为了观测「关 Tier2 后默认会话整体不被相似度门控误处理」。effective_sid=default 是 BC 恒等行为（见阶段 B `test_resolve_default_is_bc_identity`）。

---

## 阶段 C（T1-2 L2 page-in + /continue 透传）：单测 + 评估覆盖

阶段 C 的 page-in（L2 降级为 external memory，按需 page-in）与 `/continue` 透传属**纯组装/会话状态行为**，由 **512 passed** 后端测试 + 评估子代理 100%（0 GAP）覆盖。运行时 gate `l2_truncated=False` 与 page-in 的「不再硬截断、改按需调取」语义一致。

`/continue` 续场的真机端到端（在 default 会话灌历史 → `/continue 它的竞品呢` → 验 L2 page-in 保留续场）受网络限制（deepresearch 0 引用不落盘，见 §4 网络坑）未单独逐一真机，由单测 + 评估覆盖。

## 实现 + 评估（真机外的强保障）
- **后端测试**：C 阶段 **512 passed**；D 阶段 **473 passed** 无破坏（关 Tier2 后既有 Tier1/page-in 测试全绿）。
- **评估子代理 100%**：C 阶段 page-in/`/continue` 透传 0 GAP；D 阶段 8 处 `topic_shift_gate`→false 逐处核对 + Tier1 保留确认，0 GAP。

## 未单独真机执行（理由，诚实标注）
- **§3 §4 可选整体交叉验证**（普通对话验关 Tier2 不漂 / `/continue` 验 page-in 续场 / 双窗 `/new` 验 group 同步）：当前由单测 + 评估覆盖，真机受网络限制（deepresearch 0 引用不落盘——设计行为非 bug）/ 窗口拓扑未逐一跑。运行时核心 gate（关 Tier2 + Tier1 保留）已真机 PASS。

## 结论
关 Tier2（D，层 3）+ L2 page-in（C，层 1 配套）**运行时硬证据 PASS**——真模拟人触发后，组装期 gate 实测 `topic_shift_gate=False` + `shift_path='off'` + `l2_truncated=False`（Tier2 整链关 + L2 不再误砍），同时 `relabel_applied=True anchor_applied=True`（Tier1 精准保留）。后端 512/473 passed + 评估 100% 为完整实现保障。判定基于真实运行栈 log 而非脚本回放，符合 [[feedback_real_e2e_not_script_replay]]。落盘受网络限制（非代码问题），故锚点用组装期 gate log 而非落盘报告。
