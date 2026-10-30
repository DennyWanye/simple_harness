# 任务书 B（给 gpt-6.1-sol / Codex）：全宿主统一的模型并发门

日期：2026-10-30。发包人：Claude 会话（SimplaHarness）。审核人：同一 Claude 会话。
你只看这一份任务书和它指向的文件就够了。本任务书与任务书 A 文件不重叠，可同时做。

## 1. 背景（一段话）

宿主 `OrchestrationHost` 里有多个执行池：任务池（跑 HTN/TaskGraph 的步骤）和每个会话各自的聊天池。每个池在组装时各自建一个模型并发门（`asyncio.Semaphore`），`HostSettings.max_concurrent_model_calls` 这个数是**每池**上限，不是全宿主上限。会话一多，同时在线路上的模型调用就按会话数翻倍。手机上扛不住。本任务书把门做成宿主一个，注入所有池。**不改任何默认数值。**

## 2. 仓库与基线

- 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（私有，不推公开远端）。基线 `47fd34f0da65667817d30f1994e73523b5aaf8ba`。
- 工作树：`git worktree add ../simple_harness-sol-B -b sol/B-shared-admission main`。提交到分支，不推送、不合并。
- 测试在 `sdk/simple-harness-sdk/` 下用 `uv run --frozen python -m pytest <文件> -q`。只跑改到的文件对应的测试，不跑全量。
- 先读：
  - `src/simple_harness/execution/provider_admission.py`（`LocalProviderAdmission`、`ProviderAdmissionPort`、`ProviderHandoffFence`；指纹常量 `base-agent-local-admission-v1`，**槽位数不在指纹里**）
  - `src/agent_orchestrator/runtime/assembly.py`（`assemble_orchestrator_runtime` 的 `provider_admission` / `provider_admissions` / `local_provider_admissions` / `provider_handoff_fence` 参数；`admission_accepts`；`_check_intent_contexts` 里对持久意图指纹的核对）
  - `src/simple_harness/agents/runtime.py` 第 160～200 行附近（没有注入门时每条 wire 自建信号量）
  - `src/agent_orchestrator/host/host.py` 的 `_assemble_runtime`、`_build_native`、`settings` 相关
  - `src/agent_orchestrator/host/chat.py` 的 `open()`（第 116～160 行附近，组装聊天池的地方）
  - `src/agent_orchestrator/host/settings.py`（`max_concurrency`、`max_concurrent_model_calls`）
  - 测试：`tests/orchestrator/host/test_chat.py`（`ScriptedProvider` 的 `held / entered / release` 用法）、`tests/orchestrator/host/test_host_sessions.py`

## 3. 车道（只能改这些文件）

- `src/agent_orchestrator/host/host.py`：只改组装处（建一个宿主级门并传下去）
- `src/agent_orchestrator/host/chat.py`：只改 `open()` 里 `assemble_orchestrator_runtime(...)` 那一处调用，把宿主的门传进去
- `src/agent_orchestrator/runtime/assembly.py`：只在必要时改参数透传，不改身份核对逻辑
- `src/simple_harness/execution/provider_admission.py`：只在必要时加"可被多个 runtime 共享"的保证（例如同一事件循环内可重复 `recover`），不改指纹
- 新建 `tests/orchestrator/host/test_shared_admission.py`
- `docs/orchestration-host.md` 在 §2b 或 §3 加三行说明：这个数是全宿主上限

别的不碰。

## 4. 要做的事

1. 宿主打开时建**一个** `LocalProviderAdmission(max_concurrent=settings.max_concurrent_model_calls, fence=...)`（或现有等价对象），任务池组装和每个聊天池组装都传同一个对象。
2. 聊天池组装处（`chat.py`）把它传给 `assemble_orchestrator_runtime(..., provider_admission=host_admission)`。
3. 门的生命周期跟宿主：宿主关闭释放；会话关闭不释放。
4. 恢复：宿主重开时门从零开始，不继承上次的占用（进程没了，占用自然没了）；在途取消/失败要释放许可。
5. **不改指纹、不改默认数值。** 指纹是常量、槽位数不在里面，所以注入不动池身份。但要知道一件事：现在的聊天池持久意图里 `provider_admission_fingerprint` 是空（因为之前没注入），注入后 `_check_intent_contexts` 会拒开旧聊天库。这是预期（开发期不兼容旧数据）。在记录文件里写明，并在文档里写"需要新数据根"。
6. 公平：门是先到先得即可，不做优先级（前台优先放后面任务书）。

## 5. 不变量与测试

| # | 不变量 | 测试做法 |
|---|---|---|
| I10 | 宿主模型在途数 ≤ `max_concurrent_model_calls`，与会话数无关 | 门设 1：开两个会话各 `send` 一次，同时用 `host.chat()` 发一次；`ScriptedProvider.held` 挡住线路；`entered` 计数在任何时刻 ≤1；放行后三个都完成 |
| 同上 | 门设 2 时峰值恰为 2 | 同上，断言峰值 == 2（要观察到 2，不只是 ≤2） |
| 释放 | 取消一个在途回合后许可释放 | 门设 1：挡住第一个，`chat_cancel`，放行；第二个能进 |
| 恢复 | 关宿主再开，门不被上次占用卡住 | 挡住一个，不放行直接 `host.close()`（会按现有逻辑结清），重开后新回合能进 |
| 身份 | 注入前后池编号不变 | 断言聊天池 `profile_id` 与注入前相同（从基线提交取字符串比对） |

在途计数怎么拿：`ScriptedProvider` 的 `entered` 事件只有一个；你可以在测试里包一层 provider，在 `chat` 调用进入/退出时维护计数并记录峰值。不要改 `offline.py`。

先写测试看它红（门设 1 时现在峰值会是 3），再写代码。

## 6. 完成标准

- `test_shared_admission.py` 全绿；`test_chat.py`、`test_host_sessions.py`、`test_session.py` 全绿。
- `uv run --frozen ruff check src tests`、`ruff format --check` 绿。
- 记录 `plans/2026-10-30-agentos-main-session-contract/记录-任务书B-Sol.md`：改了什么、测试命令与通过数、旧库拒开的说明、拿不准的、分支与提交号。中文。

## 7. 禁区

- 不改 `max_concurrency`、`max_concurrent_model_calls` 的默认值与校验范围。
- 不改指纹字符串。不改 `admission_accepts` 的语义。
- 不改任何工具说明、提示词。
- 不加兼容分支。注释中文写"为什么"，不写代号。
- 不推送、不合并、不跑全量回归。不碰密钥文件。

## 8. 有问题怎么办

写进记录文件"拿不准的"，按最合理的做法先做完。
