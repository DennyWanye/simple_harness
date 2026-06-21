# CODEX 实现作业 — 阶段 C：T1-2 L2 降级 external memory（page-in）+ /continue 透传

你是 DeskPet（G:/projects/deskpet，master @ 8810211a）后端 Expert。实现 v2 plan 阶段 C。Lead 审查+集成+真机验收。

## 权威规格
读 `plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md` **§6（T1-2）**。**只做 §6**（§3 关 Tier2 是阶段 D，不做）。

## 背景
关 Tier2 后普通回复漂移只剩 Tier1 软锚定（§3 空窗）。T1-2 兜底：L2 旧历史**不默认直灌** prompt，按需 page-in，从根上削弱 attention-sink。`/continue` 给用户显式"保留上下文"退路。注：阶段 B 已在 `TaskScopeDecision` 加了 `force_l2_page_in` 字段 + `/continue` 在 main.py resolve 处解析（grep 确认现状）。

## 实现范围（只改这些）

### 1. `backend/deskpet/agent/assembler/bundle.py` — MemoryPolicy 加字段
`MemoryPolicy` dataclass 加 `l2_page_in: Literal["always","followup","off"] = "always"`（import Literal）。

### 2. `backend/deskpet/agent/assembler/policy.py` — `_to_policy` 解析（隐藏必改点）
`_to_policy` 构造 MemoryPolicy 处加 `l2_page_in=str(memory_raw.get("l2_page_in","always"))`；`load_policies` 的 fallback clone 处也带上（grep 现有 5 字段 clone 处照加，否则 YAML 新 key 被吞）。

### 3. `backend/deskpet/agent/assembler/components/memory.py` — provide 按 page-in 调 l2
在 `mm.recall(...)` 调用**之前**（构造 `call_policy` 处，grep `l2_top_k`）：
```python
if policy_memory.l2_page_in == "off":
    call_policy["l2_top_k"] = 0
elif policy_memory.l2_page_in == "followup" and not _starts_with_anaphora(ctx.user_message):
    call_policy["l2_top_k"] = 0
```
（`_starts_with_anaphora` 已在 memory.py，复用。）⚠️ `l2_top_k=0` 会跳过 L2 history/reasoning_content 回填——确认 manager 支持 0（阶段评估已确认 `manager.py:147` 支持），但要测连续 thinking 场景不炸。

### 4. `backend/deskpet/agent/assembler/policies/default.yaml` — 默认 profile
每个 task_type 的 memory 块加 `l2_page_in`：`task`/`web_search`/`command` 设 `followup`；`recall`/`chat`/`emotion`/`plan`/`code` 设 `always`（保连续性）。

### 5. `/continue` 强制 always 透传链路（§6 R3 接口）
- `ContextAssembler.assemble()`（`assembler.py`）现仅 `task_type_override`，**新增 `memory_policy_override: Optional[dict]=None`**（或 `assembly_options`）参数；取到 policy 后若 override 含 `l2_page_in`：`policy = dataclasses.replace(policy, memory=dataclasses.replace(policy.memory, l2_page_in=override["l2_page_in"]))`，再 fanout 给组件。
- `main.py`：resolve 得 `_scope_decision.force_l2_page_in`（阶段 B 已有）后，assemble 调用处把它传成 `memory_policy_override={"l2_page_in":"always"}`（当 force_l2_page_in=="always"）。grep main.py assemble 调用点（约 :5742）。

## 单测（必写）
- `test_l2_page_in.py`：3 档（always 取 L2 / off 跳 L2 top_k=0 / followup 非 anaphora 跳、anaphora 保）+ 默认 profile（task=followup 等）+ `_to_policy` 解析新字段 + BC（无字段→always 等同现状）。
- `/continue` 透传：`memory_policy_override={"l2_page_in":"always"}` → assemble 后该轮 L2 强制取（即使 policy profile 是 followup/off）。
- **reasoning_content 场景**：l2_top_k=0 不炸（构造含 reasoning_content 的 L2 fixture，验跳过不抛）。

## 跑测试
```bash
cd /g/projects/deskpet/backend
/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/ -k "memory or assembler or policy or bundle or page_in or scope or task_drift" -q
```
不得回归。

## 交付
- 只改上述文件 + 新测试。**不 commit**。**不起服务**。**不碰 Tier2**（阶段 D）。
- 输出：①各改点 file:line ②默认 profile 设置 ③/continue 透传链路 ④测试结果 + reasoning_content/BC ⑤偏差。
