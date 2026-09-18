# 子代理通道：Grok Build 与 Codex（无人值守）

指挥者（Claude）只派任务与检查；实施、核验、诊断、文档交给这两个通道。两者接口同形：

```
grok_task.sh  <任务名> <工作目录> <任务书.md> [推理强度=high] [最大轮数=80] [续接会话号]
codex_task.sh <任务名> <工作目录> <任务书.md> [推理强度=high] [沙箱=workspace-write] [续接会话号]
```

- 产物目录：`$AGENT_TASK_OUT/<任务名>/`（缺省 `~/.cache/simpleharness-agent-tasks/{grok,codex}/`）。stdout 末段是一行摘要（`GROK_TASK …` / `CODEX_TASK … session=…`）加最终回复尾部；摘要里的会话号用于续接同一会话做修复或复核。
- 两个脚本都内置纪律：不 push / stash / checkout / reset；不读不打印任何密钥文件；只在给定目录工作；记录文档用中文；测试先行；最终回复以「## 结果」收尾。
- Grok：`grok` 命令行，模型 grok-4.6，走 SuperGrok 订阅（见 `docs/GROK-BUILD-LANE.md`；做真实模型验收时另需 `grok_build_runtime.py`，与本脚本无关）。
- Codex：`/Applications/ChatGPT.app/Contents/Resources/codex`（随 ChatGPT 桌面应用自带，可用 `CODEX_BIN` 覆盖）。模型与服务端点由 `~/.codex/config.toml` 决定（cc-switch 管理）；2026-09-18 时为自定义端点上的 deepseek-v4.1-flash。必须关闭标准输入（脚本已做），否则会挂住等输入。沙箱缺省 `workspace-write`：只能写工作目录、git 公共目录与 uv 缓存，命令无网络；纯只读任务传 `read-only`。
- 分工建议：核心代码实施与独立核验用 Grok（实施与核验分属不同会话）；Codex 用于并行的低风险任务——文档与台账编纂、数据表、只读排查、测试清单、对 Grok 结论的第二意见。需要升级时才用 Claude 子代理。
- 首次冒烟（2026-09-18）：Codex 在 SDK 主树跑 `test_repeated_failure_early_stop.py` 12 通过，17 秒，主树保持干净。

## 确定性闸门与切片流水线

`grok_task.sh` / `codex_task.sh` 负责跑单个代理任务；下面两个脚本把「多个代理任务」串成
可监视、可复现的切片流水线，并用确定性闸门把关。全部产物落在
`${AGENT_TASK_OUT:-$HOME/.cache/simpleharness-agent-tasks}/pipeline/<片名>/`。

### `sdk_gate.sh` —— SDK 切片的确定性闸门

```
sdk_gate.sh <sdk工作树> <基准提交> [--tests "<pytest 路径 …>"] [--allow <白名单文件>]
            [--full] [--max-sentinel N] [--out <报告.json>]
```

逐项检查，每项以 `{name, ok, detail}` 写进 JSON 报告（顶层 `ok` 为各项与）；任一失败退出码 1：

1. `clean`：工作树 `git status --short` 为空。
2. `allowlist`：`git diff --name-only <基准>..HEAD` 的每个文件都匹配白名单文件里的某一行 glob（bash `case` 模式；用 `#` 注释、空行忽略）；未给 `--allow` 则记 skipped。
3. `contracts_frozen`：`git diff --name-status <基准>..HEAD -- src/agent_orchestrator/contracts` 只允许状态 `A`（新增），出现 M/D/R 即红。
4. `no_secrets`：`git diff <基准>..HEAD` 新增行不得匹配 `sk-` / `xai-` / `Bearer …` / `eyJ…` 密钥样式。
5. `ruff`：对改动的 `.py` 跑 `uv run ruff check`（在 SDK 工作树内；无 `.py` 改动则 skipped）。
6. `import_origin`：`PYTHONPATH=src uv run python -c "import agent_orchestrator,os;print(os.path.realpath(agent_orchestrator.__file__))"` 输出必须位于该工作树内（防止测到别的工作树的源码）。
7. `targeted`：`PYTHONPATH=src uv run pytest <--tests 路径> -q -p no:cacheprovider`，解析尾行 passed/failed/errors，failed+errors 必须为 0（未给 `--tests` 则 skipped）。
8. 仅 `--full`：`full_target`（`tests/orchestrator/full_target`，failed+errors=0 且 passed ≥ 基线）与 `legacy`（step02 step05 step06 step07 p34 p35，同样只允许增不许减）。基线从 `<sdk工作树>/plans/llm-native-htn/H0/test-results.json` 的 `full_target.passed` / `legacy.passed` 读取。
9. `sentinel`：`grep -rn --include='*.py' "_new_mode" src/agent_orchestrator | wc -l` 只统计源码 `.py`（`.pyc` 等非源码文件不计），记录数值；给了 `--max-sentinel` 且超过则红（否则只记录）。

所有 pytest 输出与 ruff 日志存到报告同目录的 `*.log`。pytest 尾行解析用可 `source` 的函数 `parse_pytest_tail`（稳健处理 `12 passed, 2 skipped in 7.9s` / `1 failed, 11 passed` / `no tests ran`）。设 `GATE_SKIP_PYTHON=1` 可让第 5–8 项全部记 skipped（供离线自测）。

### `slice_pipeline.sh` —— 一个切片的实施 → 闸门 → 独立核验 → 处置 → 全量闸门

```
slice_pipeline.sh <片名> <sdk工作树> <基准提交> <实施任务书.md> <核验任务书.md>
                  [--lane codex|grok] [--verify-lane codex|grok] [--tests "…"] [--allow 文件]
                  [--max-sentinel N] [--max-rounds N] [--log 文件]
```

每步往日志写一行 `=== [时间] <片名> <步骤> <结果>`，便于外部监视。流程：

- a. 用 `--lane`（缺省 `codex`）在 SDK 工作树跑实施任务书，从摘要行取 `session`。
- b. 跑 `sdk_gate.sh`（不带 `--full`）；红 → 把闸门报告 JSON 路径与失败项写进一个「修复任务书」，用同一 session 续接实施者再做一次 → 再跑闸门；仍红 → 写 `PIPELINE RED gate` 退出 2。
- c. 在 `<sdk工作树>-verify-<片名>` 建分离副本（`git worktree add --detach`，HEAD = 工作树当前提交）。第 1 轮用 `--verify-lane`（缺省 `codex`，**新会话**）跑给定的核验任务书；核验任务书要求核验员在最终回复里写一行 `VERDICT: 可合` / `VERDICT: 修后可合` / `VERDICT: 不可合`。脚本按 `last.txt` → 摘要 → `out.json` 的优先级 grep 这一行（正则把 `不可合`、`修后可合` 排在 `可合` 前，避免子串误匹配）。
- d. 每轮（`--max-rounds N`，缺省 2）循环，步骤名带轮次（如 `d-disposition-2`）：
  - `修后可合` → 生成处置任务书（内容同前，另加一句「这是第 k 轮处置」）→ 续接实施者 session → 闸门（红则 `PIPELINE RED gate` 退出 2）→ `save_verify_artifacts` → 把核验副本移到新提交（remove + re-add，不 checkout）→ **把上一轮保存的核验报告拷回新副本的同一路径**（核验员在原报告上追加）→ 续接核验员 session 复核。
  - 复核用的任务书：第 1 次核验用原核验任务书；第 k≥2 次用脚本生成的「复核任务书」（同一会话续做；只需看上一轮 HEAD 到当前 HEAD 的改动；逐个重做上一轮报告里的存活变异确认已被杀死；再补做若干新变异；结果追加到原报告的「## 复核 k」；判定规则与 VERDICT 行格式不变）。
  - 任一轮 `可合` → 跳出循环进入全量闸门；轮数用尽仍非 `可合` → `PIPELINE RED verify` 退出 3；`不可合` 或取不到 VERDICT → 立即红。
- e. `可合` → 跑 `sdk_gate.sh --full`；绿 → 删除分离副本（`git worktree remove --force`），写 `PIPELINE GREEN <片名> head=<短哈希>` 退出 0；红 → 退出 2。

脚本自己 **不 merge、不 push**。

### `selftest_gate.sh` —— 离线自测（不调用任何模型）

用 `mktemp -d` 建一次性 git 仓库伪装成 SDK（含 `src/agent_orchestrator/contracts/a.py` 与假的 `plans/llm-native-htn/H0/test-results.json`），用 `GATE_SKIP_PYTHON=1` 让 `sdk_gate.sh` 跳过 5–8 项，验证：①干净 + 白名单内改动 → 绿；②改了既有 contracts 文件 → `contracts_frozen` 红；③白名单外文件 → `allowlist` 红；④新增行含 `sk-abcdefghij1234` → `no_secrets` 红，而 `task-abcdefghij1234` 不红；⑤工作树不干净 → `clean` 红。另外：⑥造一个含 `_new_mode` 字样的 `.pyc` 假文件与一个 `.py` 文件，`sentinel` 计数应为 1（只数源码）；⑦用可 `source` 的 `slice_pipeline.sh`（`SLICE_PIPELINE_SOURCE_ONLY=1` 时不跑主流程）单测 `VERDICT` 提取函数（三种结论各一，且优先取 `last.txt`，缺失时回落到摘要）与轮次判断 `round_action`（`可合`/`修后可合`+未用尽/`修后可合`+用尽/`不可合`/无 VERDICT）。再单测 `parse_pytest_tail` 的三种尾行。全过打印 `SELFTEST OK n/n`。

## 2026-09-18 指挥者修正与使用纪律

- `codex exec resume` 不接受 `-s / -C / --add-dir`：`codex_task.sh` 改为用 `-c sandbox_mode=…`、`-c sandbox_workspace_write.writable_roots=[…]` 传沙箱与可写目录，并先 `cd` 到工作目录；续接已实测。
- `slice_pipeline.sh` 在删除核验副本前先把其中未跟踪的 `.md/.json/.txt`（即核验报告）存到 `<产物目录>/pipeline/<片名>/verify-artifacts/`，由指挥者随切片归档。
- **不要相信代理最终回复里的数字与路径。** 实测中 Codex（deepseek-v4.1-flash）把命令输出的目录名抄错了一个词。验收一律以 `sdk_gate.sh` 的 JSON 报告、pytest 日志、`git` 输出为准；代理回复只当线索。
- `slice_pipeline.sh` 的「核验 → 处置 → 复核」改为循环（`--max-rounds`，缺省 2）；每轮把上一轮保存的核验报告拷回移动后的副本同一路径，核验员在原报告上追加「## 复核 k」，便于横向对比存活变异。续接复核**复用核验员同一 session**（勿另开新会话），否则上下文丢失。
- `VERDICT` 提取优先看 `<任务产物目录>/last.txt`（runner 最终回复），其次摘要文件，最后 `out.json`；正则把 `不可合`、`修后可合` 排在 `可合` 前，避免把「修后可合」误判成「可合」。脚本改动后先 `bash -n` 再 `bash backend/scripts/agent_lanes/selftest_gate.sh`（离线，不调模型）。
- `sentinel` 项只数源码：`grep -rn --include='*.py' _new_mode src/agent_orchestrator | wc -l`（0.12.2 为 29），与测试里的哨兵口径（19）不同；上限按行数给（H1 建议 `--max-sentinel 32`）。
