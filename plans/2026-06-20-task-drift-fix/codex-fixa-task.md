# CODEX 作业指令 — 任务漂移修复 Fix A（组装层话题门控，Tier 1 默认开 + Tier 2 kill-switch 默认关）

你是 DeskPet 后端 Expert。在**当前 worktree**（`G:/projects/deskpet/.claude/worktrees/task-drift-fixa`，分支 `task-drift-fixa`）实现 **Fix A**。Lead（Claude）会审查 + 集成 + 真机验收。

## 权威规格（必读，自包含）
先读本 worktree 的 `plans/2026-06-20-task-drift-fix/00-fix-plan.md`，**重点 §9.1（Fix A 确切改点表 A1-A6）+ §9.1 的 🔴 阻碍 + §8.1 风险分层 + §3 机制**。再读 `plans/2026-06-20-task-drift-fix-HANDOFF.md` 了解根因。§9 的 file:line 是评估时核实的，可能漂移，**按语义定位**。

## 背景一句话
桌宠聊天单一 `session="default"`，最近 5 条原始历史（CATL 占压倒性）被**零门控**地提升成"正在进行的对话线"贴在当前 user 前面（`memory.py::provide` → `bundle.build_messages` 顺序 `[system][skill][memory_block=L3][history=L2轮][当前user]`）→ 新请求被旧主题压垮。Fix A 在组装层治。

## 风险分层（核心，决定默认值）
- **Tier 1（零风险，默认开，永不删上下文）**：① 当前请求优先锚定（紧邻 user 的 system nudge）② L2 历史重定性标签。
- **Tier 2（高风险，kill-switch 默认关）**：话题跳变语义截断。**Tier 2 才碰 embedder**（Tier 1 零 embedder = 零超时风险）。

## 你的实现范围（严格只改这些，别碰 Fix B 的 agent_loop.py/research_tools.py；main.py 只改 build_messages 调用处那一行透传）

### A1 — build_messages 加 late-system 槽位（锚定"紧邻 user"）
- `backend/deskpet/agent/assembler/bundle.py`：`build_messages` 加 keyword-only 参数 `late_system_nudge: Optional[str] = None`；在 `history` extend **之后**、`user_message` append **之前**插入 `{"role":"system","content":late_system_nudge}`（仅当非空）。
- **理由**：memory_block 在 history **之前**，recency 压不住 5 轮 CATL；拼进 user 文本会污染 Fix B 的原话源。所以**必须**独立 late system message。对 prompt cache frozen 前缀零影响。

### A2 — Bundle 透出 nudge + assembler 提升（锚定逻辑收敛组装层）
- `bundle.py`：`Bundle`（或等价 dataclass）加字段 `late_system_nudge: str = ""`。
- `backend/deskpet/agent/assembler/assembler.py`：`_stitch`（约 :365，处理 `meta["l2_history"]` 的地方）旁边，把 MemoryComponent 经 meta 透出的 nudge 提升到 `bundle.late_system_nudge`（仿 l2_history 同套机制）。
- `backend/main.py`：build_messages 调用处（约 :5472）把 `late_system_nudge=_bundle.late_system_nudge` 透传进去（main.py **仅此 1 行**，None/空时不插，字节级向后兼容）。

### A3 — L2 重定性标签（memory.py）
- `backend/deskpet/agent/assembler/components/memory.py::provide`：构建 `l2_history` 时，若 `policy.relabel_l2` 且有 l2_rows，在列表**头部**插一条 system：`{"role":"system","content":"以下为较早的对话记录，可能涉及其他话题，仅供背景参考。"}`。
- 同时按 `policy.anchor_current` 把锚定文本（如「当前请求是本轮唯一任务；先前对话仅为背景，若与当前请求冲突，以当前请求为准。」）经 Slice `meta` 透出（key 如 `meta["late_system_nudge"]`），供 A2 提升。
- L2 仍是真 turn，**不回退 P4-S21 #16**（标签只是 history 首条 system，不把 L2 挪回 system block）。

### A4 — 配置双改（隐藏必改点！否则 YAML 新 key 被静默丢弃）
- `bundle.py` 的 `MemoryPolicy` dataclass 加字段：`relabel_l2: bool=True`、`anchor_current: bool=True`、`topic_shift_gate: bool=False`、`topic_shift_threshold: float=0.35`、`l2_keep_on_shift: int=1`。
- `backend/deskpet/agent/assembler/policy.py` 的 `_to_policy`（约 :201，构造 MemoryPolicy 处）同步从 `memory_raw.get(...)` 解析这 5 个新字段（带同样默认值）。
- `backend/deskpet/agent/assembler/policies/default.yaml` 的 `memory:` 块补对应 key（值同默认）。

### A5 — Tier 2 语义截断 + embedder 降级骨架（默认关，topic_shift_gate=False）
仅当 `policy.topic_shift_gate` 为 True 时执行。在 memory.py provide 里：
```python
def _get_embedder(mm):
    r = getattr(mm, "_retriever", None)
    return getattr(r, "_embedder", None) if r is not None else None

async def _topic_similarity(emb, current, l2_concat):
    if emb is None or not emb.is_ready() or emb.is_mock():
        return None                      # 降级：不门控
    try:
        vecs = await asyncio.wait_for(emb.encode([current, l2_concat]), timeout=0.3)
    except Exception:
        return None                      # fail-open
    if getattr(vecs, "shape", [0])[0] < 2:
        return None
    return float(vecs[0] @ vecs[1])      # 归一化向量 → cosine=点积
```
🔴 **关键阻碍**：embedder 的 `await encode` 落在已有 ~1500ms 硬超时的组件 fan-out 内（`registry.py` 约 :172 的 `asyncio.wait_for(per_component_timeout_s)`），`mm.recall` 已占一部分。冷模型首调（加载 286MB BGE-M3）会超时 → memory slice 整个丢（L2+L3 全没），比漂移更糟。所以**必须**：`is_ready()/is_mock()` 预检（不触发 warmup）+ 内层 `wait_for(0.3)` + 一切异常 fail-open（保留全部 L2）。

### A6 — 合取判据（Tier 2）
```python
sim = await _topic_similarity(emb, current, l2_concat)
is_shift = (sim is not None and sim < policy.topic_shift_threshold
            and len(current.strip()) > 50 and not _starts_with_anaphora(current))
if is_shift:
    l2_rows = l2_rows[-policy.l2_keep_on_shift:]   # 截断，保留最低衔接
```
- `_starts_with_anaphora`：复用 `backend/deskpet/memory/entity_extractor.py`（约 :39-55）的 `_STOPWORDS`（含中文"这个/那个/什么/为什么"+ 英文疑问词），判断句首是否代词/疑问词起手。短消息（<10 字）/ 代词起手 → 直接判延续保留。
- 任一条件不满足 → 全量保留 L2（fail-safe 偏向追问连续性）。

## 单元测试（必写，放 backend/tests/）
新建 `backend/tests/test_task_drift_fixa.py`，至少覆盖：
1. **Tier 1 锚定注入**：`build_messages(..., late_system_nudge="X")` 的输出在 history 之后、user 之前有一条 `{"role":"system","content":"X"}`；`late_system_nudge=None` 时无此条（字节级 BC）。
2. **Tier 1 重定性**：`relabel_l2=True` 且有 l2_rows → l2_history 首条是重定性 system 标签；`relabel_l2=False` → 无标签。
3. **配置解析**：`_to_policy` 能读出 5 个新字段（造一个含新 key 的 raw dict，断言 MemoryPolicy 字段值）；缺省时用默认值。
4. **Tier 2 降级**：embedder=None / is_mock()=True → `_topic_similarity` 返回 None → **不截断**（保留全部 L2）。
5. **Tier 2 截断**：mock 一个 ready embedder 让 `encode` 返回"低相似"向量 + 长消息 + 无指代 → 截断到 l2_keep_on_shift；高相似 → 保留。
6. **Tier 2 默认关**：`topic_shift_gate=False` 时即便低相似也不截断（不调用 embedder）。
7. **追问豁免**：current="它的竞品呢" 这类短/代词起手 → 即便低相似也保留。

## 跑测试（用主 checkout 的 venv 解释器，从本 worktree 的 backend 跑）
```bash
cd /g/projects/deskpet/.claude/worktrees/task-drift-fixa/backend
/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/test_task_drift_fixa.py -v
# 回归：确认没碰坏 assembler/memory 现有测试（尤其 P4-S21 context bundle history）
/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/ -k "assembler or bundle or memory or p4s21 or policy" -q
```
`python -m pytest` 从本 worktree backend/ 跑 → sys.path[0]=CWD → import 本 worktree 代码（隔离正确）。

## 交付要求
- 只改 A1-A6 列出的文件 + 新增测试文件。**不要** `git commit`（Lead 审查后集成）。**不要**起 backend/Tauri/前端。
- Tier 1 默认开、Tier 2 默认关（kill-switch off）。降级路径绝不报错。
- 改完跑通单测，最后输出：①改了哪些文件每个文件改了什么 ②单测结果（贴 pytest 末尾 PASSED 行）③遇到的偏差/阻碍。
