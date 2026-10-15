# 阶段 5 SDK 发版独立核验（opt.190）

日期：2026-10-16
核验员：独立子代理（只读，模型 Opus 5.5）
口径：只报阻断级问题（会改变运行行为、会让产品或测试坏掉、不该发版）。

## 核验范围

- 工作树 `simple_harness-cq`，分支 `cq-phase5`。
- 基准：opt.189 发版提交 cc339895f（Host 钉版 3752c6f34）。
- 被核提交：5fd5f1a55。`git log cc339895f..5fd5f1a55` 只有 3752c6f34（Host 钉版）和 5fd5f1a55 两个提交。
- 只用 git、读文件、AST 计算、只读的 `manifest_fresh.py`，加指定的一条 pytest 命令。没有改源码，没有提交。

## 1. 源码改动只有所述内容，运行行为不变 —— 通过

- `git diff cc339895f 5fd5f1a55 -- sdk/simple-harness-sdk/src` 只涉及两个文件：
  - `agent_orchestrator/orchestrator/assurance_content_review.py`：+2/−5 行。
  - `agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json`（重生成的部署清单）。
- 源码改动只有两处：
  - 注释 "读取器带授权与身份……" 从独立一行挪到 `metadata = tuple(` 行尾。注释不影响字节码。
  - `permissions = tuple((item.ref, cast(...)) for item in metadata)` 改成 `m`，并写成一行。
- 生成器表达式有自己的作用域。循环变量 `item`/`m` 不绑定到外层函数，改名不影响外层。表达式结构、调用顺序、`cast` 参数都不变。
- 其余 SDK 源码字节不变。

## 2. 部署清单与源码字节一致 —— 通过

- `python3 scripts/code_quality/manifest_fresh.py` 输出 "部署清单：最新"，返回码 0。
- 清单只改了三处：`assurance_content_review.py` 的哈希、`deployment_id`，以及验收状态重置。
- 验收状态现为 `taskgraph_acceptance: "NOT_RUN"`、`acceptance_evidence: null`。这是重生成后的预期状态，阶段 5 中间提交都是这样。opt.189 发版提交是在发版时跑验收门后才写回 `VALIDATED` 的。不算阻断，但下面有发版前提。

## 3. 函数行数 ≤ 300 —— 通过

- 用 `ast` 计算 `ensure_task_content_review`：第 41～339 行，共 299 行。

## 4. 指定测试 —— 通过

- 命令：`cd sdk/simple-harness-sdk && PYTHONPATH=src .venv/bin/python -m pytest tests/orchestrator/full_target/assurance_exec/test_batch2_i2_rounds_and_dead_code.py tests/orchestrator/product_world/test_disclosure_certificates.py -p no:cacheprovider -q`
- 结果：8 passed（51.68 秒）。
- 说明：工作区 HEAD 含后续测试提交 09416964e（死代码扫描按整个提交服务包读源码），本次跑的是这一版测试文件。

## 发版前提（不是阻断，必须照做）

1. `src/simple_harness/version.py` 仍是 `opt.189`。顺序必须是：改版本号为 opt.190 → 重生成部署清单 → 跑 `scripts/acceptance/taskgraph_gate.py` → 用 `taskgraph_manifest.py validate` 写回 `VALIDATED` → 再打包。
2. 原因：Host `backend/deskpet/orchestration/service.py` 第 325～339 行在清单不是 `VALIDATED` 时拒绝启动编排服务（`taskgraph_acceptance_not_validated`）。如果带着当前 `NOT_RUN` 的清单打包，产品里编排起不来。
3. 打包后再跑一次 `manifest_fresh.py`，确认清单与改版本号后的源码一致。

## 总结论

PASS
