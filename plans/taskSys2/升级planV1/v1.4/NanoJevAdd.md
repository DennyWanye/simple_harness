# simpleHarness HTN/DAG NanoJev Shadow → Primary 实施计划

## 0. 本次改造目标

本阶段只解决一个问题：

> 在 simpleHarness 的 HTN / DAG 执行过程中，引入 NanoJev 作为高速局部决策模型；首先以 Shadow Mode 运行，不获得执行权。经过真实数据验证后，直接将 NanoJev 切换为 fast-path Primary Decision Provider。

不修改 simpleHarness 当前核心原则：

```text
LLM
负责：
- Goal 理解
- HTN 分解
- DAG 创建
- DAG 结构修改
- Replan
- 复杂语义推理

Runtime
负责：
- 状态机
- 依赖关系
- Ready 判断
- MaxRuns
- Event
- 生命周期
- 确定性规则

NanoJev
负责：
- DAG 内部局部、明确候选集的运行决策
```

NanoJev **不成为 Agent**。

NanoJev **不参与 HTN 长程规划**。

NanoJev **不能直接修改 DAG**。

---

# 1. 最终目标架构

## 1.1 Shadow 阶段

```text
                  MainWork
                     │
                     ▼
                HTN Planner
                     │
                     ▼
                     DAG
                     │
                     ▼
                 DAG Runtime
                     │
              Decision Required
                     │
          ┌──────────┴──────────┐
          │                     │
          ▼                     ▼
 Existing Decision         NanoJev
 Production Path           Shadow
          │                     │
          │                     ▼
          │               Shadow Result
          │                     │
          └──────────┬──────────┘
                     ▼
                  Event Log
                     │
                     ▼
            Production executes
```

核心原则：

```text
NanoJev output
≠
production behavior
```

Shadow 阶段 NanoJev 永远没有执行权。

---

# 2. NanoJev 验证通过后的架构

```text
                Decision Required
                       │
                       ▼
             deterministic?
                │          │
               YES         NO
                │          │
                ▼          ▼
             Runtime    structural?
                          │       │
                         YES      NO
                          │       │
                          ▼       ▼
                       Main LLM NanoJev
                                  │
                           confidence OK?
                             │          │
                            YES         NO
                             │          │
                             ▼          ▼
                          Execute    Main LLM
```

最终实际上只有三个执行路径：

```text
Rule
NanoJev
Main LLM
```

没有：

```text
Router Agent
Decision Agent
Risk Agent
Selector Agent
```

---

# 3. 本阶段最关键的设计边界

把 HTN/DAG 内部判断分成三种。

## 3.1 Deterministic

代码能够 100% 判断。

永远不要调用模型。

例如：

```text
task dependency 是否完成
Task 是否 READY
MaxRuns 是否超过
Task 是否已经 completed
DAG 是否存在 cycle
某 Worker 是否正在运行
```

代码：

```python
if all(dep.status == COMPLETED for dep in task.dependencies):
    task.status = READY
```

不要：

```python
nanojev.decide("Is this task ready?")
```

---

# 4. Structural Decision

会改变任务结构或任务语义。

全部走 slow-path / MainWork。

第一版禁止 NanoJev 处理。

包括：

```text
GOAL_DECOMPOSITION

TASK_DECOMPOSITION

CREATE_TASK

REMOVE_TASK

CHANGE_DEPENDENCY

REOPEN_UPSTREAM_TASK

DAG_REPLAN

DAG_REPAIR

MISSION_REPLAN

ROOT_CAUSE_REPLAN
```

原则：

> 改 DAG = slow-path。

---

# 5. Operational Decision

不改变 DAG 的语义，只决定如何执行现有 DAG。

这是 NanoJev 的职责。

第一版只支持两个。

```text
READY_TASK_PRIORITY

RETRY_OR_ESCALATE
```

先不要继续加。

尤其不要一开始加入：

```text
worker selection
parallel optimization
model selection
tool routing
risk evaluation
```

等最初两类跑稳后再增加。

---

# 6. 第一批两个 Decision

## 6.1 READY_TASK_PRIORITY

当前：

```text
READY:
T12
T15
T16
```

输入：

```python
DecisionRequest(
    type=DecisionType.READY_TASK_PRIORITY,
    candidates=["T12", "T15", "T16"],
    context=...
)
```

NanoJev：

```text
T12  0.13
T15  0.78
T16  0.09
```

返回：

```python
selected = "T15"
```

---

## 6.2 RETRY_OR_ESCALATE

例如：

```text
Task T15
Run #1
FAILED
```

候选：

```text
RETRY
ESCALATE
```

NanoJev：

```text
RETRY       0.91
ESCALATE    0.09
```

这里注意：

```text
ESCALATE
```

不是 NanoJev 自己 Replan。

它只表示：

> 这个问题不适合 fast-path 继续解决。

然后交回 MainWork。

---

# 7. 新增最小核心抽象

建议在 `agent_orchestrator` 内增加：

```text
decision/
```

逻辑结构：

```text
src/agent_orchestrator/

    decision/
        __init__.py
        types.py
        provider.py
        service.py
        policy.py

        providers/
            existing.py
            nanojev.py

    ...
```

如果现在项目已经有类似模块，不重复创建目录，合并进去。

---

# 8. DecisionType

`decision/types.py`

```python
from enum import Enum


class DecisionType(str, Enum):
    READY_TASK_PRIORITY = "ready_task_priority"
    RETRY_OR_ESCALATE = "retry_or_escalate"
```

第一版就两个。

不要设计成：

```python
str
```

因为我们希望编译期/类型检查阶段就发现错误。

---

# 9. DecisionRequest

```python
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DecisionCandidate:
    id: str
    label: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class DecisionRequest:
    decision_id: str
    decision_type: DecisionType

    mission_id: str
    task_id: str | None

    candidates: Sequence[DecisionCandidate]

    context: Mapping[str, Any]
```

注意：

### 不传整个 Agent Context。

禁止：

```python
context = full_agent_context
```

必须构造成最小决策状态。

---

# 10. READY_TASK_PRIORITY Context

例如：

```python
context = {
    "goal_summary": "...",

    "ready_tasks": [
        {
            "id": "T12",
            "summary": "...",
            "attempt_count": 0,
            "priority": 2,
            "dependency_count": 1,
        }
    ],

    "running_task_count": 2,
}
```

不要传：

```text
完整 conversation
完整 DAG JSON
完整 Worker history
全部 Event Log
全部记忆
```

NanoJev 是 fast-path。

必须保持输入非常小。

---

# 11. RETRY_OR_ESCALATE Context

```python
context = {
    "task_summary": "...",

    "attempt_count": 1,
    "max_runs": 3,

    "failure": {
        "kind": "tool_error",
        "summary": "...",
    },

    "previous_failures": [
        "..."
    ],
}
```

同样不要塞完整 traceback。

可以由 Runtime 先压缩：

```text
raw error
↓
normalized failure
↓
DecisionRequest
```

---

# 12. DecisionResult

```python
@dataclass(frozen=True)
class DecisionScore:
    candidate_id: str
    probability: float


@dataclass(frozen=True)
class DecisionResult:
    decision_id: str

    provider: str

    selected: str

    scores: tuple[DecisionScore, ...]

    top1_probability: float

    margin: float

    latency_ms: float

    model_version: str | None = None
```

`margin`：

```python
margin = top1 - top2
```

例如：

```text
T1 = 0.81
T2 = 0.11

margin = 0.70
```

明显确定。

而：

```text
T1 = 0.51
T2 = 0.48

margin = 0.03
```

明显应该 slow-path。

---

# 13. Provider Interface

`decision/provider.py`

```python
from abc import ABC, abstractmethod


class DecisionProvider(ABC):

    @abstractmethod
    async def decide(
        self,
        request: DecisionRequest,
    ) -> DecisionResult:
        ...
```

NanoJev 实现：

```python
class NanoJevDecisionProvider(DecisionProvider):

    async def decide(
        self,
        request: DecisionRequest,
    ) -> DecisionResult:

        ...
```

Existing 路径：

```python
class ExistingDecisionProvider(DecisionProvider):

    async def decide(
        self,
        request: DecisionRequest,
    ) -> DecisionResult:

        ...
```

---

# 14. 为什么一定要包 ExistingDecisionProvider

这是 Shadow Mode 能安全落地的关键。

不要重写现有 Scheduler。

现有逻辑：

```text
existing scheduler
```

只包起来：

```text
ExistingDecisionProvider
    ↓
调用原来的逻辑
```

所以：

```text
before modification
==
after modification production behavior
```

第一阶段必须满足这个要求。

---

# 15. DecisionPolicy

只需要三个模式。

```python
class DecisionMode(str, Enum):
    EXISTING = "existing"
    SHADOW = "shadow"
    NANOJEV = "nanojev"
```

配置：

```python
@dataclass(frozen=True)
class DecisionPolicy:
    mode: DecisionMode
```

不要第一版搞复杂：

```text
providers[]
weights[]
strategies[]
router[]
profiles[]
```

---

# 16. 配置文件

例如：

```yaml
decision:
  mode: shadow

  nanojev:
    enabled: true
    model_path: ~/.simple-harness/models/nanojev

    min_probability: 0.80
    min_margin: 0.20
```

开发开始时：

```yaml
mode: shadow
```

验证以后：

```yaml
mode: nanojev
```

完成切换。

---

# 17. DecisionService

整个新功能最重要的类。

`decision/service.py`

```python
class DecisionService:

    def __init__(
        self,
        existing_provider: DecisionProvider,
        nanojev_provider: DecisionProvider,
        event_store: EventStore,
        policy: DecisionPolicy,
    ):
        ...
```

公开接口：

```python
async def decide(
    self,
    request: DecisionRequest,
) -> DecisionResult:
    ...
```

---

# 18. Shadow Mode 实现

核心代码应该非常简单。

伪代码：

```python
async def decide(self, request):

    production_result = await self.existing.decide(request)

    if self.policy.mode == DecisionMode.SHADOW:

        asyncio.create_task(
            self._run_shadow(
                request,
                production_result,
            )
        )

        return production_result

    ...
```

关键：

```text
RETURN production_result FIRST
```

NanoJev：

```text
永远不能阻塞 Production。
```

---

# 19. Shadow 失败不能影响主路径

必须：

```python
async def _run_shadow(...):

    try:

        shadow_result = await self.nanojev.decide(request)

        await event_store.append(
            ShadowDecisionProduced(...)
        )

    except Exception as exc:

        await event_store.append(
            ShadowDecisionFailed(...)
        )
```

禁止：

```python
raise
```

Shadow 出现任何问题：

```text
NanoJev crash
model missing
MPS error
timeout
OOM
invalid result
```

都只能：

```text
log
```

绝不能改变生产结果。

---

# 20. 更推荐使用队列，而不是裸 create_task

如果现在 Event Runtime 已经有 Queue/Worker：

复用。

结构：

```text
DecisionService
       │
       ├──── Production
       │
       └──── ShadowDecisionQueue
                    │
                    ▼
             NanoJev Worker
```

好处：

```text
控制并发
避免 task 泄漏
可观察
可关闭
```

如果当前没有这样的基础设施：

第一版可以 `create_task()`。

不要为了 Shadow 新建复杂 worker framework。

---

# 21. Event 定义

现有 Event Sourcing 体系中加入：

```text
DecisionRequested

DecisionProduced

ShadowDecisionProduced

ShadowDecisionFailed

DecisionEscalated

DecisionOutcomeObserved
```

---

# 22. DecisionRequested

```python
@dataclass(frozen=True)
class DecisionRequested:
    decision_id: str

    decision_type: str

    mission_id: str

    task_id: str | None

    candidates: list[str]

    timestamp: datetime
```

不要把完整 prompt 写进 Event。

可以：

```text
context_hash
context_version
```

或者只记录结构化必要字段。

---

# 23. DecisionProduced

```python
@dataclass(frozen=True)
class DecisionProduced:

    decision_id: str

    provider: str

    selected: str

    scores: Mapping[str, float]

    top1_probability: float

    margin: float

    latency_ms: float
```

---

# 24. ShadowDecisionProduced

```python
@dataclass(frozen=True)
class ShadowDecisionProduced:

    decision_id: str

    provider: str

    selected: str

    scores: Mapping[str, float]

    top1_probability: float

    margin: float

    latency_ms: float

    production_selected: str

    agreement: bool
```

于是天然可以统计：

```text
agreement_rate
```

---

# 25. DecisionOutcomeObserved

这是整个 Shadow 验证最重要的 Event。

没有它：

```text
Shadow Mode 几乎没有价值。
```

因为：

```text
agreement
≠
correct
```

Existing Scheduler 也可能选错。

因此一定关联最终 Outcome。

```python
@dataclass(frozen=True)
class DecisionOutcomeObserved:

    decision_id: str

    selected_task_id: str

    task_success: bool

    verifier_pass: bool

    retry_count: int

    execution_latency_ms: float
```

未来可以增加：

```text
token_cost
worker_cost
rollback
```

但第一版不需要。

---

# 26. decision_id 必须贯穿整个链路

例如：

```text
dec_01JXYZ
```

生命周期：

```text
DecisionRequested
       │
       ├──── DecisionProduced
       │
       ├──── ShadowDecisionProduced
       │
       ▼
TaskDispatched
       │
       ▼
TaskCompleted
       │
       ▼
VerificationCompleted
       │
       ▼
DecisionOutcomeObserved
```

全部通过：

```text
decision_id
```

关联。

这会成为未来训练 AgentOS-NanoJev 的核心数据。

---

# 27. 不要修改 Task 主模型

不要在：

```python
Task
```

里面加入：

```python
nanojev_score
nanojev_confidence
shadow_choice
```

这是典型污染。

Task 还是 Task。

这些东西属于：

```text
Decision Event
```

而不是：

```text
Task State
```

---

# 28. NanoJev Provider

第一版 Provider 结构：

```python
class NanoJevDecisionProvider:

    def __init__(
        self,
        runtime: NanoJevRuntime,
    ):
        self.runtime = runtime
```

调用：

```python
async def decide(
    self,
    request,
):

    nano_request = self._translate(request)

    raw_result = await self.runtime.evaluate(
        nano_request
    )

    return self._translate_result(
        request,
        raw_result,
    )
```

---

# 29. 把 NanoJev Runtime 和 Provider 分开

非常重要。

```text
DecisionProvider
    │
    ▼
NanoJevDecisionProvider
    │
    ▼
NanoJevRuntime
```

Provider 负责：

```text
simpleHarness contract
```

Runtime 负责：

```text
model loading
PyTorch/MPS
tokenizer
inference
```

以后迁移：

```text
PyTorch MPS
↓
MLX
```

DecisionProvider 完全不用改。

---

# 30. NanoJev Runtime API

保持极小：

```python
class NanoJevRuntime:

    async def start(self) -> None:
        ...

    async def evaluate(
        self,
        request: NanoJevRequest,
    ) -> NanoJevOutput:
        ...

    async def close(self) -> None:
        ...
```

---

# 31. 模型只加载一次

绝不能：

```text
Decision
↓
load model
↓
infer
↓
destroy
```

必须：

```text
simpleHarness start
        │
        ▼
load NanoJev
        │
        ▼
keep resident
        │
        ├── decision
        ├── decision
        ├── decision
        └── decision
```

Mac 16GB 上尤其如此。

---

# 32. Mac 第一版 Runtime

第一阶段建议：

```text
PyTorch
+
MPS
```

而不是先迁移 MLX。

原因：

```text
Shadow 阶段目标是验证决策质量
不是优化极限性能
```

所以：

```python
if torch.backends.mps.is_available():
    device = torch.device("mps")
else:
    device = torch.device("cpu")
```

先跑起来。

NanoJev 验证通过后，再考虑：

```text
MLX runtime
```

---

# 33. NanoJev 不应作为 simpleHarness 强依赖

`pyproject.toml` 不建议直接把完整 AI 依赖塞到 core。

建议 optional dependency：

```toml
[project.optional-dependencies]

nanojev = [
    "torch>=...",
    "transformers>=...",
    "safetensors>=..."
]
```

安装：

```bash
pip install -e ".[nanojev]"
```

普通用户：

```bash
pip install -e .
```

不会下载模型相关依赖。

符合 simpleHarness。

---

# 34. 模型文件也不要提交 repo

目录：

```text
~/.simple-harness/
    models/
        nanojev/
```

或者：

```text
SIMPLE_HARNESS_MODEL_HOME
```

Repository 中只有：

```text
adapter
runtime
contract
```

没有：

```text
*.safetensors
```

---

# 35. HTN Planner 第一阶段不改

这是非常重要的实施策略。

当前：

```text
Goal
↓
HTN
↓
DAG
```

全部不动。

本次 PR 不优化：

```text
HTN decomposition model
```

只优化：

```text
HTN 输出以后
DAG Runtime 内的 operational decision
```

避免这次改动把 HTN 和 NanoJev 两件事耦合到一起。

---

# 36. Ready Task Priority 接入点

找到当前 DAG Scheduler：

```text
ready_tasks
↓
selection
↓
dispatch
```

原来可能类似：

```python
ready_tasks = scheduler.get_ready_tasks()

selected = scheduler.select_next(ready_tasks)

dispatch(selected)
```

改成：

```python
ready_tasks = scheduler.get_ready_tasks()

request = build_ready_task_decision(
    mission=mission,
    ready_tasks=ready_tasks,
)

result = await decision_service.decide(
    request
)

selected = task_by_id(
    result.selected
)

dispatch(
    selected,
    decision_id=request.decision_id,
)
```

注意：

```text
get_ready_tasks()
```

仍然完全由 Runtime 做。

NanoJev 只做：

```text
select_next()
```

---

# 37. Ready Task 数量为 0 / 1 时不要调用模型

```python
if len(ready_tasks) == 0:
    return None

if len(ready_tasks) == 1:
    return ready_tasks[0]
```

只有：

```text
>= 2
```

才产生 DecisionRequest。

这也是 simpleHarness 的原则：

> 没有选择，就没有 Decision。

---

# 38. Retry 接入点

找到当前类似：

```python
if task.failed:
    if runs < max_runs:
        retry()
    else:
        escalate()
```

第一版不要完全替换 MaxRuns。

MaxRuns 仍然是硬边界。

例如：

```python
if runs >= max_runs:
    return ESCALATE
```

只有：

```text
runs < max_runs
```

时：

```text
RETRY
vs
ESCALATE_EARLY
```

给 NanoJev 判断。

所以：

```python
if runs >= max_runs:
    escalate()

else:

    result = decision_service.decide(
        RETRY_OR_ESCALATE
    )
```

NanoJev 永远不能突破：

```text
MaxRuns
```

---

# 39. fast-path gate

Shadow 时不用执行。

但是代码可以先实现好。

最初：

```python
def can_accept_fast_result(
    result: DecisionResult,
) -> bool:

    return (
        result.top1_probability >= 0.80
        and
        result.margin >= 0.20
    )
```

不要加复杂数学。

---

# 40. Slow-path 条件

第一版只有：

```text
NanoJev inference failed

top1 < threshold

margin < threshold

unsupported decision type
```

即：

```python
if not gate.accept(result):

    return await slow_path.decide(
        request
    )
```

---

# 41. 不要给 NanoJev“解释理由”的能力

不要要求：

```json
{
  "choice": "T2",
  "reason": "..."
}
```

NanoJev 的价值就是：

```text
candidate probabilities
```

只需要：

```text
T1 0.12
T2 0.79
T3 0.09
```

解释属于 slow-path LLM。

---

# 42. Primary 切换

当 Shadow 验证完成后：

唯一核心配置变化：

```yaml
decision:
  mode: nanojev
```

DecisionService：

```python
if mode == SHADOW:

    production = existing.decide()

    shadow(nanojev)

    return production
```

变成：

```python
if mode == NANOJEV:

    nano = nanojev.decide()

    if gate.accept(nano):
        return nano

    return existing.decide()
```

这是理想状态：

> 切换 Primary 不需要重写 DAG Runtime。

---

# 43. 注意 fallback 的意义

虽然你说：

> 确认 NanoJev OK 后直接切 NanoJev。

我同意。

但是仍然建议保留：

```text
NanoJev
↓ failure / uncertain
Existing slow-path
```

不是多 Router。

只是 fail-safe。

例如：

```text
NanoJev service crash
```

不应该导致：

```text
整个 Mission fail
```

而应该：

```text
fallback existing
```

---

# 44. Shadow 数据验证指标

不要单纯看：

```text
agreement rate
```

需要至少以下指标。

## A. Agreement Rate

```text
NanoJev == existing decision
```

---

## B. High Confidence Agreement

例如：

```text
top1 >= 0.80
margin >= 0.20
```

的时候一致率。

这比总 agreement 更重要。

---

## C. Outcome Success Rate

NanoJev 推荐的 Task 最终：

```text
completed?
```

---

## D. Verifier Pass Rate

```text
Verifier first-pass success
```

这是 simpleHarness 最重要的质量信号之一。

---

## E. Retry Rate

NanoJev 推荐路径之后：

```text
平均 retry
```

---

## F. Decision Latency

```text
p50
p95
p99
```

---

# 45. Shadow Promotion 标准

不要凭感觉切。

第一版建议定义一个明确 Promotion Gate。

例如：

```text
sample_count >= 1000
```

并且：

```text
High-confidence decisions:

outcome success >= existing baseline

Verifier pass >= existing baseline - 1%

retry rate <= existing baseline + 2%

NanoJev error rate < 0.5%

p95 latency 符合 fast-path 目标
```

这里不要死卡我上面的百分比。

真正重要的是：

> NanoJev 至少不能显著劣于 Existing。

---

# 46. 更重要的是分 DecisionType 看

禁止统计：

```text
NanoJev overall accuracy = 92%
```

要分别：

```text
READY_TASK_PRIORITY
    samples
    agreement
    success
    verifier_pass

RETRY_OR_ESCALATE
    samples
    agreement
    success
    verifier_pass
```

可能出现：

```text
ReadyPriority = 非常好

RetryDecision = 很差
```

那么可以：

```text
ReadyPriority → Primary NanoJev

RetryDecision → Shadow
```

以后配置可以支持：

```yaml
decision:

  ready_task_priority:
    mode: nanojev

  retry_or_escalate:
    mode: shadow
```

但这个按类型配置建议在第二阶段再加。

第一阶段先全局 mode。

---

# 47. Shadow Report

建议做一个非常简单的 CLI：

```bash
simple-harness decision stats
```

输出：

```text
NanoJev Shadow Report

Total decisions: 4,821

READY_TASK_PRIORITY
  samples:               3,912
  agreement:             87.2%
  high-conf agreement:   95.8%
  verifier pass:         94.1%
  existing verifier:     93.8%
  nanojev p95:           41 ms

RETRY_OR_ESCALATE
  samples:               909
  agreement:             72.3%
  high-conf agreement:   90.1%
```

不要第一版做 Dashboard。

CLI 足够。

---

# 48. 测试计划

## Unit Test 1

Shadow 永远返回 existing：

```python
async def test_shadow_returns_existing_result():

    existing = FakeProvider("T1")

    shadow = FakeProvider("T2")

    service = DecisionService(
        mode=SHADOW,
        ...
    )

    result = await service.decide(...)

    assert result.selected == "T1"
```

这是最重要的测试。

---

# 49. Unit Test 2

Shadow failure 不影响 Production。

```python
shadow = CrashingProvider()

result = await service.decide(...)

assert result == production_result
```

---

# 50. Unit Test 3

NanoJev Primary 高置信度：

```text
0.91 / 0.05 / 0.04
```

应该：

```text
NanoJev
```

---

# 51. Unit Test 4

NanoJev Primary 低置信度：

```text
0.51 / 0.48 / 0.01
```

应该：

```text
fallback
```

---

# 52. Unit Test 5

NanoJev 不能处理 structural decision。

例如：

```text
DAG_REPLAN
```

必须：

```text
slow-path
```

甚至最好根本不生成 NanoJev request。

---

# 53. Unit Test 6

单 Ready Task：

```text
[T1]
```

必须：

```text
T1
```

并且：

```text
NanoJev call count = 0
```

---

# 54. Unit Test 7

MaxRuns：

```text
runs == max_runs
```

必须：

```text
ESCALATE
```

NanoJev：

```text
call count == 0
```

---

# 55. Integration Test

完整链路：

```text
Mission
↓
HTN
↓
DAG
↓
3 Ready Tasks
↓
Existing decides T2
↓
NanoJev Shadow decides T3
↓
实际执行 T2
↓
Verifier
↓
Event Log
```

检查：

```text
DecisionRequested

DecisionProduced(T2)

ShadowDecisionProduced(T3)

TaskDispatched(T2)

VerificationCompleted

DecisionOutcomeObserved
```

顺序和关联正确。

---

# 56. Event Replay 测试

因为你有 Event Sourcing：

一定测试 Replay。

新增 Decision Event 后：

```text
old event logs
```

必须仍然可以 replay。

Decision Event：

```text
只能是 additive
```

不能破坏原 Current State reconstruction。

---

# 57. 推荐实施 PR 顺序

不要一个巨型 PR。

## PR-1：Decision Contract

只加入：

```text
DecisionType
DecisionRequest
DecisionResult
DecisionProvider
```

没有行为变化。

验收：

```text
all old tests pass
```

---

## PR-2：ExistingDecisionProvider

把现有两个决策：

```text
Ready priority

Retry
```

封装进 provider。

但：

```text
behavior 100% unchanged
```

这是重要 checkpoint。

---

## PR-3：Decision Events

加入：

```text
DecisionRequested
DecisionProduced
DecisionOutcomeObserved
```

仍然没有 NanoJev。

---

## PR-4：Shadow Infrastructure

加入：

```text
DecisionMode.SHADOW

ShadowDecisionProduced

ShadowDecisionFailed
```

用 FakeShadowProvider 测试。

---

## PR-5：NanoJev Runtime

加入：

```text
NanoJevRuntime

MPS/CPU

model loading

inference
```

独立测试。

不要接 DAG。

---

## PR-6：NanoJev DecisionProvider

完成：

```text
DecisionRequest
↓
NanoJev input

NanoJev output
↓
DecisionResult
```

---

## PR-7：Shadow 接入

配置：

```yaml
decision:
  mode: shadow
```

正式开始收集真实数据。

这是第一阶段最终上线点。

---

# 58. Shadow 运行一段真实 workload

这个阶段：

```text
不要继续改架构。
```

只运行。

收：

```text
1k+
5k+
10k+
```

decision。

观察：

```text
哪些 DecisionType NanoJev 强

哪些弱

confidence 是否校准

margin 是否有意义
```

---

# 59. PR-8：Promotion Gate

加入：

```python
NanoJevDecisionGate
```

只负责：

```text
top1
margin
```

不要过度设计。

---

# 60. PR-9：NanoJev Primary

开启：

```yaml
decision:
  mode: nanojev
```

逻辑：

```text
NanoJev
│
├── confident → use
│
└── uncertain → existing
```

完成 Shadow → fast-path 切换。

---

# 61. 此时完整状态

```text
HTN Structural Intelligence
        │
        ▼
     Main LLM
        │
        ▼
       DAG
        │
        ▼
     Runtime
        │
    ┌───┴────┐
    │        │
 Rule      NanoJev
             │
       uncertain/error
             │
             ▼
          Main LLM
```

---

# 62. 不要在本阶段实现的东西

明确排除：

```text
× 3B Router

× Router Agent

× multi-model voting

× NanoJev fine-tuning

× RL

× automatic provider discovery

× dynamic model marketplace

× capability registry

× remote distributed NanoJev

× MLX migration

× UI dashboard

× NanoJev planning

× NanoJev DAG generation
```

这些全部不是当前 HTN milestone 的必要条件。

---

# 63. 后续阶段

等 NanoJev fast-path 证明价值以后，再讨论：

```text
Ready Task Priority

Retry/Escalate

↓

Parallel / Serial

Worker Selection

Tool Selection
```

最后才有可能：

```text
AgentOS-specific NanoJev fine-tune
```

而训练数据已经天然来自：

```text
DecisionRequested
+
ShadowDecisionProduced
+
DecisionOutcomeObserved
```

---

# 64. 对现有 DispatchIntent 的处理

你目前已经有：

```python
DispatchIntent
```

而且之前出现过：

```text
Task Critic
Mission Judge
```

配置/类型边界的问题。

这次不要把：

```text
DecisionRequest
```

塞入：

```text
DispatchIntent.config: Mapping[str, Any]
```

把 Decision 当成自己的 typed contract。

建议：

```text
DispatchIntent
负责：
执行派发

DecisionRequest
负责：
执行之前的选择
```

两个概念不能合并。

否则后面：

```text
NanoJev decision
Worker dispatch
Mission judge configuration
Task critic configuration
```

又会重新进入：

```text
kind: str
config: Mapping[str, Any]
```

的大字典问题。

---

# 65. recommended package boundary

结合目前已知 simpleHarness 结构，我建议最终类似：

```text
src/
└── agent_orchestrator/

    htn/
        ...

    dag/
        ...

    decision/
        __init__.py
        types.py
        provider.py
        policy.py
        service.py

        providers/
            existing.py
            nanojev.py

        runtime/
            nanojev_runtime.py

    storage/
        store.py

    events/
        ...

    verifier/
        ...
```

如果目前：

```text
DAG
HTN
Event
```

仍集中在其他 package：

不要为这个计划强制搬目录。

> 新增 Decision boundary 即可，不做顺手重构。

---

# 66. simpleHarness 这次形成的新原则

建议把下面这段写进 architecture 文档：

> simpleHarness 的 HTN/DAG 决策遵循确定性优先和结构/运行分离原则。可以由 Runtime 确定的行为不调用模型；改变 HTN/DAG 结构或任务语义的决策由高能力 Planner 处理；明确候选集的局部运行决策允许使用专用高速决策模型。任何新的高速决策模型在获得执行权之前必须先以 Shadow Mode 在真实 workload 上运行，并通过 Outcome 数据验证。Shadow Provider 永远不得改变生产状态；通过验证后，仅通过 Decision Policy 将其提升为 Primary Provider，不修改 HTN/DAG Runtime。

可以压缩成一句：

```text
Structure → strong model
Operation → fast model
Certainty → code
New model → shadow first
```

---

# 67. 本阶段 Done Definition

只有以下全部完成，才算 NanoJev Shadow Phase 完成：

* [ ] Existing HTN behavior 未改变
* [ ] Existing DAG behavior 未改变
* [ ] DecisionRequest / Result 强类型完成
* [ ] DecisionProvider 完成
* [ ] ExistingDecisionProvider 完成
* [ ] NanoJevDecisionProvider 完成
* [ ] NanoJev 模型常驻加载
* [ ] Apple Silicon MPS 或 CPU 能运行
* [ ] Shadow Mode 永不改变 production selection
* [ ] Shadow failure 不影响 Mission
* [ ] decision_id 贯穿执行与 Verification
* [ ] Decision Events 可 Replay
* [ ] ReadyTaskPriority Shadow 正常
* [ ] RetryOrEscalate Shadow 正常
* [ ] Shadow statistics 可读取
* [ ] Existing 全量测试 PASS
* [ ] 新增 Decision 测试 PASS

---

# 68. NanoJev Primary Phase Done Definition

之后只有：

* [ ] Shadow 样本达到约定数量
* [ ] 各 DecisionType 单独验证
* [ ] Outcome 不弱于 Existing baseline
* [ ] Verifier Pass 无明显下降
* [ ] Error Rate 满足要求
* [ ] Confidence/Margin gate 验证
* [ ] fallback 验证
* [ ] `mode=nanojev` integration test PASS

然后：

```yaml
decision:
  mode: nanojev
```

完成正式切换。

---

# 最终架构原则

整个改造不要发展成：

```text
HTN
↓
Router
↓
Router Model
↓
Decision Agent
↓
NanoJev
↓
LLM
```

最终只应该是：

```text
                         HTN / DAG
                             │
                             ▼
                     Need decision?
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
      Deterministic       Operational       Structural
           │                 │                 │
           ▼                 ▼                 ▼
        Runtime           NanoJev          MainWork LLM
                             │
                       uncertain/error
                             │
                             ▼
                          MainWork
```

Shadow 期间：

```text
Existing ──────→ Production
   │
   └───────────→ NanoJev Shadow
                     │
                     ▼
                  Event Log
```

验证完成：

```text
NanoJev ───────→ Production
    │
uncertain
    ▼
MainWork
```

这是目前最符合 simpleHarness 原则、同时又能让 NanoJev 真正带来 HTN/DAG 性能优化的实现路径。
