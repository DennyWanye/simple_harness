# codex 作业 A1：实现 WI-1（tool_choice 硬约束）+ WI-2（结构化 trace）

你是 DeskPet 项目（仓库根 `G:\projects\deskpet`，已在此 cwd）的实现工程师。**严格按权威 plan 实现，不要自由发挥。**

## 第 0 步：先读 plan（权威，含 5 轮挑战修订）
打开 `plans/2026-06-20-agent-loop-optimization/00-PLAN.md`，**通读这些节**并以它们为准（冲突时后出的修订节优先）：
- WI-1：§2、§13.1（B1-B6）、§15.2 附录 A-1/A-3、§15.3、§15.4、§16.4、§17.1、§17.6
- WI-2：§3、§13.2、§17.3
- 全局：§1（约定）、§13.0（A1/A2：新依赖经 build_agent 注入、flag 走 `cfg.raw.get(...)`）

**所有行号是锚点，动手前用 grep 复核**（main.py/agent_loop.py 大、易漂移）。

## 第 1 步：实现 WI-1（tool_choice 协议级硬约束）
1. `backend/providers/openai_compatible.py`：给 `chat_with_tools`(~237)、`chat_stream_with_tools`(~631)、`_legacy_chat_with_tools_nonstream`(~292) 三个方法各加 kw-only 参数 `tool_choice: str | None = None`；把硬编码 `payload["tool_choice"] = "auto"`（~345 与 ~691）改成 `payload["tool_choice"] = tool_choice or "auto"`。加兜底（B6）：若 relay 返回 400 且错误信息含 `tool_choice`，删掉 payload 里的 tool_choice 重试一次，并 `from observability.metrics_sink import record` 记 `record("tool_choice_none_unsupported", {...})`（metrics_sink 有白名单，按其既有约定，若该事件名未在白名单则跳过记录、不报错）。
2. `backend/agent/agent_loop.py` `AgentLoop.__init__`：加参数 `force_finish_via_tool_choice: bool = True`（带默认，放在现有可选参数后），存 `self.force_finish_via_tool_choice`。
3. `agent_loop.py` `run()`：
   - for 循环顶部（~657）加局部 `_force_finish_next = False`（每轮复位）。
   - 在 selfcheck 注入块（~858，`if iteration % _SELFCHECK_EVERY == 0` 那段附近）加：`if self.force_finish_via_tool_choice and iteration >= _SELFCHECK_TIER3_AT: _force_finish_next = True`。**必须在三处 LLM 调用之前**。
   - 三处 LLM 调用都传 tool_choice：chain（~898 `prov.chat_with_tools`）加 `tool_choice=("none" if _force_finish_next else None)`；stream（~979 `chat_with_fallback_stream` 之前）加 `if _force_finish_next: llm_kwargs = {**llm_kwargs, "tool_choice": "none"}`；nonstream fallback（~1050 `chat_with_fallback`）同样在调用前注入 llm_kwargs。
   - tier3+ 禁 nudge（B4）：completion(~1120)/verify(~1179)/goal(~1393) 三处 nudge 最外层 if 各并入前置条件 `iteration < _SELFCHECK_TIER3_AT`（逐处按其真实条件加括号，别套同一模板）。
   - verify_exhausted（§17.6）：verify 多次失败、原本 `yield ErrorEvent(reason="verify_exhausted"); return` 之处，改为：用 run() 局部 latch `_verify_final_done`（初值 False，在 ~604 区定义）；`if self.force_finish_via_tool_choice and not _verify_final_done: _verify_final_done=True; _force_finish_next=True; append 一条 system "本轮必须 end_turn，向用户如实总结…不要再调用工具"; continue`，否则维持原硬退。
4. `backend/main.py` `build_agent`（~820-1005）：读 `_ff = cfg.raw.get("agent", {}).get("force_finish_tool_choice", True)`；构造 AgentLoop 时传 `force_finish_via_tool_choice=_ff`。

## 第 2 步：实现 WI-2（每轮结构化 trace）
1. 新建 `backend/agent/trace.py`：`class IterationTracer`，构造 `__init__(self, *, trace_dir: Path, session_id: str = "", task_id: str = "")`（trace_dir mkdir(parents=True, exist_ok=True)）；方法 `record(self, event: dict) -> None`：用 `time.time()` 打 `ts`，json.dumps(ensure_ascii=False) append 一行到 `<trace_dir>/<task_id>.jsonl`；内部加 `threading.Lock` 兜底并发（§17.3）。失败只 log debug、绝不抛。
2. `agent_loop.py` `__init__` 加 `tracer: Optional[Any] = None`，存 `self._tracer`。
3. `run()` 关键点插 `if self._tracer: self._tracer.record({...})`（None 时零开销）：迭代开始(iter_start, msg_count)、LLM 返回后(llm_out: stop_reason/content 前 200 字/tool_calls 含**完整 args**/usage)、**工具结果（在 gather 之后的顺序 for 循环里，~1521 区，不要放进并发的 _dispatch_one）**(tool_result: name/args/ok/result 前 200 字)、守门结论(gate: which/passed/reason)、终止前(end: reason/gate_summary)。
4. `main.py build_agent`：头部确保 `import uuid`（§17.1，若缺）；读 `_trace = cfg.raw.get("agent", {}).get("trace_enabled", False)`；`from deskpet... import` 路径取 user_data：用项目既有 `paths.user_data_dir()`（grep 确认正确导入路径，如 `from deskpet.paths import ...` 或 main 已有的 `_paths`）；`tracer = IterationTracer(trace_dir=<user_data>/"traces") if _trace else None`，注入 AgentLoop。

## 第 3 步：测试（TDD，必须真跑绿）
新建 `backend/tests/test_wi1_tool_choice.py` 与 `backend/tests/test_wi2_trace.py`，覆盖 plan §2.5/§3.4/§15.5/§17.4 的用例，至少：
- WI-1：provider 传 none→body tool_choice=="none"；默认→"auto"；跑到 tier3 三路径都收到 none；`force_finish_via_tool_choice=False`→永不 none（BC）；tier3+ 不注其它 nudge。
- WI-2：tracer 写 jsonl 且 tool args 完整；tracer=None 无文件无异常（BC）。
- 断言 provider 收到 tool_choice：扩展一个 mock provider 子类 `chat_with_tools(self,*a,tool_choice=None,**kw): self.last_tool_choice=tool_choice`（参考 `backend/tests/test_p5s2_agent_loop_provider_chain.py` 的 `_FakeProvider`）。

跑：
```
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_wi1_tool_choice.py backend/tests/test_wi2_trace.py -v
```
**BC 回归**（不得破）：
```
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_deskpet_agent_loop.py backend/tests/test_p6_agent_loop_gate.py backend/tests/test_p6_agent_loop_ctx.py backend/tests/test_p5s2_agent_loop_provider_chain.py backend/tests/test_p5s2_completion_guard.py backend/tests/test_agent_loop_verify_wiring.py -q
```
若 BC 套件里某些 mock provider 因新增 `tool_choice` kwarg 报 TypeError，按 §17.5 给那些**没有 `**kw` 的** fake 补 `tool_choice=None` 形参。反复改到**新测试全绿 + BC 全绿**。

## 约束
- **不要 `git commit` / `git add`**（Lead 负责集成提交）。只改文件、跑测试。
- 不删既有功能，所有改动 flag/参数默认值保证 BC。
- 完成后输出：改了哪些文件、新测试结果、BC 结果（贴 pytest 末尾汇总）。
