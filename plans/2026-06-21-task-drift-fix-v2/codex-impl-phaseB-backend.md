# CODEX 实现作业 — 阶段 B 后端：T1-1 会话作用域切分 + T0-4 voice 全链路 + sentinel + group

你是 DeskPet（G:/projects/deskpet，master @ 07edc0ee）后端 Expert。实现**经 7 轮对抗硬化定稿**的 v2 plan 的**阶段 B 后端部分**。Lead（Claude）审查+集成+真机验收。

## 权威规格（必读）
读 `plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md`，实现 **§5（T1-1 后端接入点全清单 + group 实现）+ §4（T0-4 voice 全链路）+ §8 的 auto-resume sentinel 处理**。**本次不做**：前端（App.tsx/MessagePanelRoot.tsx，另一个 codex）、§6 T1-2 page-in（阶段 C）、§3 关 Tier2（阶段 D）。

## 背景
桌宠单一永续 `session="default"` 从不切分，旧主题 attention-sink 压新请求。T1-1 治根因：**按任务切分会话作用域**，新无关请求 = 新 effective_sid，旧历史不进上下文。**用结构化信号（显式 /new），不用 embedding 自动检测**（已证伪）。

## 你的实现范围

### 1. 新建 `backend/deskpet/session/task_scope.py`（仿 `code_mode/state.py`）
```python
@dataclass
class TaskScopeDecision:
    effective_sid: str
    created: bool
    reason: str           # "explicit_new" | "continue" | "default"
    stripped_text: str    # 去掉 /new 或 /continue 前缀的正文
    force_l2_page_in: Optional[str] = None   # "always" | None（给阶段 C 用，先留字段）
class TaskSessionManager:
    def resolve(self, base_sid: str, text: str, explicit_new: bool, force_l2: bool=False) -> TaskScopeDecision: ...
```
- `/new ...` 前缀 或 `explicit_new=True`（payload `{new_session:true}`）→ 新 `effective_sid`（如 `task-<单调计数或基于 base_sid 的稳定 key>`，**不要用 Date.now/random**——可用进程内计数器或 base_sid+序号），`created=True`，`reason="explicit_new"`，strip `/new`。
- `/continue ...` 前缀 或 `force_l2=True` → `reason="continue"`, `force_l2_page_in="always"`, strip `/continue`，effective_sid=base_sid（继续当前）。
- 否则 → effective_sid=base_sid, `reason="default"`, stripped_text=text。
- 进程内单例 + 记住"当前活跃 effective_sid"（per base_sid 组），供 group 用。

### 2. `backend/main.py` 接入点（严格按 §5 表，逐一改）
- **插入点**：`:5401`（得 `_msg_sid`）后、`:5419` 前，resolve 并覆盖 `_msg_sid` + `text`（含 `force_l2 = bool(_payload.get("force_l2")) or text.startswith("/continue")`）。
- 后续全用 effective `_msg_sid`：`:5419` cmm.is_enabled、`:5426` code_mode_suggest payload、`:5442` reset_auto_resume_attempts、`:5465` append、`:5667` assemble、`:6002` 工具 session context、`:6767/6770/6771/6786/6837` in-flight/redispatch/callback。
- **group 广播**（§5 group 实现细节）：`_broadcast_default_chat_peers`（约 :3405）改"按 group"——保持 `_control_connections` 按 transport sid；新增 `_chat_peer_groups: dict[str,str]`（transport sid→effective chat sid，默认 `default→default`/`message-panel-main→default`）；`/new` 时同组 peers 重映射到新 effective_sid；广播按 `_chat_peer_groups[peer]==payload.session_id`。**调用侧 `:5478` 去掉 `_sid == "default"` 条件**（改 `if not _is_sentinel:`）。
- 发 `session_switched`/`task_session_started` 事件（payload `{old_sid, new_sid, reason}`）给前端（前端另做）。

### 3. §4 voice 全链路（`backend/pipeline/voice_pipeline.py`）
- `:616` `loop.run(...)` 补 `loop_user_request=text`。
- voice 也 resolve effective sid（仿 main），全链路统一：`:493/494` append、`:512` assembler、`:616` run、`:706` assistant persist 都用同一 effective sid。
- **`_broadcast_chat_v2` 全部调用点**（`:299 chat_v2_user_echo` + `:359 chat_v2_final`）：函数加 `session_id: str | None = None`（None→回退 `self.session_id`，保 BC），payload 用该 sid，去掉 `:174` `self.session_id == "default"` 门，按组过滤；两调用点都传 effective_sid。**grep `_broadcast_chat_v2(` 确认无第三处遗漏**。

### 4. §8 auto-resume sentinel 禁触发 deepresearch
- `AgentLoop.run(...)` 加显式 `is_sentinel_run: bool = False`（约 `agent_loop.py:600`）；`main.py:_run_chat` 用已有 `_is_sentinel` 传入（`:6322` 附近）。
- dispatch 前：`if is_sentinel_run and tc.name == "deepresearch": return <structured refusal 提示需显式发起研究>`。**不用 `loop_user_request is None` 判据**。

## 单测（必写）
- `test_task_scope.py`：resolve 的 /new strip+新sid、/continue+force_l2、default、group 重映射。
- main 接入点：mock 验 /new 后 append/assemble/in-flight 用 effective sid（可抽小函数或集成测）。
- voice：`loop_user_request` 透传 + 五处 sid 一致 + `_broadcast_chat_v2` 两调用点 effective sid。
- sentinel：`is_sentinel_run=True` 拒 deepresearch、普通轮放行。
- **BC**：不发 /new、payload 无 new_session → effective_sid=base_sid，行为等同现状（零回归，多窗口 default 共享不变）。

## 跑测试
```bash
cd /g/projects/deskpet/backend
/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/ -k "session or scope or voice or agent_loop or chat or main or task_drift" -q
```
不得回归。

## 交付
- 只改后端（task_scope.py 新建 + main.py + voice_pipeline.py + agent_loop.py + 测试）。**不碰前端**。**不 commit**。**不起服务**。
- 输出：①各接入点 file:line + 改了什么（对照 §5 表逐项）②group/sentinel/voice 改法 ③测试结果 + BC ④偏差/阻碍。
