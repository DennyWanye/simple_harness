# WI-0：反例与测量基线

日期：2026-07-20
分支：`codex/harness-wi0`
起始提交：`961c7d340334927acfa07cfaffe071510f56cff3`

## WI-0 前绿色基线

```powershell
$harnessTests = @(rg --files backend/tests | Where-Object { $_ -match 'test_agent_harness_.*\.py$' })
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest $harnessTests -q
```

结果：`30 passed in 4.70s`。

## 预期红契约

反例使用严格 `xfail`。普通回归保持绿色；加 `--runxfail` 后，旧生产行为会按预期违反目标契约。

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest backend/tests/harness_simplification -q
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest backend/tests/harness_simplification -q --runxfail
```

结果：

- 普通运行：`9 xfailed in 1.27s`，退出码 `0`
- 证明运行：`9 failed in 1.27s`，按预期退出 `1`
- 固定反例：安全/不安全工具次序、两个不安全调用重叠、host context 覆盖、Text/Voice 错误成功投影、跨会话 Subagent 注入、Code 中 Research/PPT 路由，以及 `/stop` 未取消持久 Workflow

## 可变 owner 审计

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts/acceptance/harness_owner_audit.py --json
```

稳定结果：`baseline=15`、`survivor_count=15`、`new_equivalent_count=0`。基线 JSON 固定 55 个宽 AST 候选，后续出现同义 dict / set / queue / task owner 时会被报告。

## 测量基线

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts/bench/harness_baseline.py --iterations 40 --output plans/2026-07-20-agent-harness-simplification/baseline.json
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts/bench/harness_baseline.py --iterations 40 --compare plans/2026-07-20-agent-harness-simplification/baseline.json
```

第二次运行的所有可复现比较均为 `comparison.passed=true`。冻结值：

- orchestration LOC：`21,563`
- 单节点 Workflow checkpoint 生命周期：`3` 个事务、`505` 个逻辑序列化字节
- 完成队列排空后，旧实现仍保留 `10,000` 个 completed-run 强引用

与机器相关的时间和 RSS 数据保存在 `baseline.json`，比较时使用明确的噪声容差。

## 测量边界

Phase-0 分支还没有 RunKernel，所以 local-start 基线是旧 AgentLoop 的 Provider 前开销。TTFT 使用受控 5 ms Provider，以便自动化重复比较。真实 Provider 的端到端 TTFT 需要运行中应用和凭据，因此 JSON 明确标为 `requires external live trace capture`，没有把受控 probe 冒充真人 E2E。

本隔离 WI-0 切片不单独更新架构事实源，更新统一在集成 worktree 完成。
