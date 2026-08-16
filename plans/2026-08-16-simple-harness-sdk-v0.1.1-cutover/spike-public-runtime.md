# Disposable spike — public Workflow/Runtime composition seam

> 日期：2026-08-16  
> 目的：在实现前验证消费者只使用 SDK public namespace 时，真实 SQLite
> Runtime/Workflow close→reopen→recover 路线是否可行。  
> 隔离：所有 spike 代码、venv 与数据库位于
> `/tmp/simple-harness-public-spike.n0pL3N/`；两个仓库均未修改。

## Exact v0.1.0 baseline

```bash
/Users/denny/.local/share/uv/python/cpython-3.11-macos-aarch64-none/bin/python3.11 \
  -m venv /tmp/simple-harness-public-spike.n0pL3N/venv
/tmp/simple-harness-public-spike.n0pL3N/venv/bin/python -m pip install \
  /Users/denny/projects/simple_harness/backend/vendor/simple_harness_sdk-0.1.0-py3-none-any.whl
/tmp/simple-harness-public-spike.n0pL3N/venv/bin/python \
  /tmp/simple-harness-public-spike.n0pL3N/public_only_spike.py
```

实际结果：Python `3.11.15`、SDK `0.1.0`、SQLite schema `1` 可正常
open/close；但 consumer 在第一个 Workflow public import 处失败：

```text
ImportError: cannot import name 'CheckpointExecutionAdapter'
from 'simple_harness.workflow' (unknown location)
```

`simple_harness.workflow` 没有 consumer public exports，因此 v0.1.0 无法开始
Profile 注册/Runner 构造，更谈不上 start/reopen/recover。脚本没有 deep import、
monkeypatch 或产品 Harness 回退。

## `/tmp` 最小 API patch

临时副本只做了六类结构修改：

1. `workflow/__init__.py` 使用 lazy public exports；eager exports 的第一次尝试触发
   Runtime/Workflow 循环导入，因此正式实现必须保留 lazy boundary。
2. `workflow/contracts.py` 增加官方 Workflow typed Host port vocabulary。
3. `WorkflowRunner(host_ports=...)` 在 composition 时验证、冻结并注入所有
   start/run/resume/recover 路径。
4. terminal Run 的 `recover()` 幂等返回，避免误进 repair/quarantine。
5. `workflows.personal_v1` 暴露 compiled graph/profile factory。
6. `simple_harness.runtime` 公开 SDK-owned `ReActDriver`。

## Patched-route command and actual output

```bash
PYTHONPATH=/tmp/simple-harness-public-spike.n0pL3N/sdk-patched/src \
  /tmp/simple-harness-public-spike.n0pL3N/venv/bin/python \
  /tmp/simple-harness-public-spike.n0pL3N/patched_route_spike.py
```

实际结果：

- public imports 仅来自 `simple_harness`、`simple_harness.runtime`、
  `simple_harness.workflow`、`simple_harness.execution.sqlite`、
  `simple_harness.workflows.personal_v1`；
- Runtime `started=true`、`closed=true`；Profile=`workflow.personal_v1`；
  official graph=`personal_workflow@v1`；
- Host `personal_workflow_runtime.execute()` 物理调用恰好 `1` 次；
- terminal=`completed`，durable Run state=`completed`；
- close 前 checkpoint=`2`，reopen 后仍=`2`，history=`2`；
- `recover_expired()`=`0`，显式 `recover(run_id)`=`completed`；
- reopen 后 Host port 重复调用=`0`，新增 trace=`0`。

## 判定与未证明项

技术路线 **PASS**：小型 SDK public seam 足以让真实 SQLite UoW、Runtime、官方
`personal_v1`、terminal 与 close/reopen/recover 在无产品旧 authority 情况下闭环。

未证明项继续作为 slice A 硬门：`durable_task` Protocol/handler 对齐、
`capability_build`、mid-node crash recovery、durable Tool permission HITL、delivery
pump 与真实 conformance CLI。spike 不能作为这些项目的完成证据。
