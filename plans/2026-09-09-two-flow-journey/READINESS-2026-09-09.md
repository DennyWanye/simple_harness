# 两轮完整流程（含重启）旅程 · 今晚可跑就绪说明（2026-09-09）

- 分支：`worktree-twoflow-ready`（工作树 `.claude/worktrees/twoflow-ready`，基线 `3c584de0`），**不合并**
- 目的：把 `00-PLAN.md` / `twoflow_driver.sh` / `twoflow_verify.py` 对齐到今天（2026-09-09）
  已经落地的口径，使 A6 第 12 次整跑一结束就能立刻起跑本旅程
- 本轮**没有启动原生应用**（18120 上正在跑 A6），全部结论来自静态核对、脚本自检与单元用例

---

## 一、今天变了什么 → 本轮改了什么

| 今天的变更 | 出处 | 本轮的动作 |
|---|---|---|
| 授权策略的唯一权威是 `workflow.db.authorization_policy_state`；`sdk-product-state.db` 的同名表是建库时 `INSERT OR IGNORE` 的 DDL 残留（恒为 `auto/0/factory_default`，无人写） | `plans/2026-09-09-manual-mode-journey/DECISION-MM-D1-D2.md` | 驱动新增 `WF="$DATA/workflow.db"` 与 `policy_mode/generation/provenance` 三个逐轮记录；核对器新增 `Evidence.policy_state()`（**只**读 `workflow.db`，缺表即 `SchemaMissing`，绝不回落）与新判定项 **TF-17** |
| 读类工具（`read_file`/`glob`/`grep`/`list_directory`）要求目标目录已绑定；越界读走 S4 绑定提案通道，Auto 策略自动授予，模型需**重路由一次** | `DECISION-F-Z1-READ-TOOL-CALL-GATE.md` / `-F-Z1B-` / `-F-Z1C-`；run12b 实测 `read_workspace_binding_revised` → `context_route` → `read_file` | **T4 改成读任务托管家目录之外的夹具**（驱动自动生成 `~/SimpleHarnessWorkSpace/twoflow-fixture/clips-source.md`），**T15 重启后重读同一夹具**；驱动新增 `binding_roots` / `binding_proposals` 两个逐轮计数；核对器新增 **TF-16（读绑定跨重启复用）** |
| 分页 offset 是**字节**偏移，拒绝时披露 `valid_offsets` | `DECISION-AF-PAGE-OFFSET-GUIDANCE.md` | 本旅程的读夹具刻意做小（< 16 KiB，实际 ~230 B），**不进分页路径**——分页由 A6 负责，两轮旅程只验重启 |
| 模型改用 `deepseek-v4-flash`；`model_overrides.toml` 必须钉 `context_window = 32000` 与 `reasoning_mode = "fast"` | Host `CLAUDE.md` 2026-09-09 用户决定；`DECISION-Y-REASONING-ECHO.md` | 计划 §0.1 写清**写入时机**（userdata 由启动器创建，只能在 launch 之后、第一轮之前写；带 `--userdata` 重启时原样保留） |
| 启动器新增 `--memory-probe` / `--memory-probe-every`（事件 X-3） | `DECISION-X3-MEMORY-PROBE.md`；`launch_native_candidate.py:56-59,143-145` | 两次启动的命令里都带上 `--memory-probe` |
| Manual 旅程出现 `no new Run head after two sends`：上一轮留下未应答的授权卡，或发送键仍是 `■ 停止`，`send.sh` 点到了不生效的按钮 | `plans/2026-09-09-manual-mode-journey/DECISION-JOURNEY-HARDENING.md` §1 的同族症状 | **两个驱动**的 `send_confirmed` 前都加了有界门 `wait_ui_send_ready`（见 §四） |
| bundle id 形如 `com.dennywanye.simpleharness.verify0907<sha8>p18120` | 实测 14 个 bundle 的 `CFBundleIdentifier` | 计划与本文的命令用真实 id，不再写占位 |

**没有改的**：22 轮的骨架、TF-1..TF-15 的判据、5 条负控、判定规则（PASS/FAIL/BLOCKED/INCONCLUSIVE）
的既有条目、`a6_verify.py`、任何产品代码。本轮只动旅程脚本、旅程计划与旅程用例。

---

## 二、精确的启动 / 驱动命令

约定：`$H` = 主仓 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`；
`$W` = 本工作树 `$H/.claude/worktrees/twoflow-ready`；`$PY` = `$H/backend/.venv/bin/python`
（**必须**用它，指纹重放要 import 安装目标的 `simple_harness`，系统 Python 3.9 会因 `StrEnum` 失败）。

### 0. 起跑前（不启动 App）

```bash
bash -n $W/scripts/native/twoflow_driver.sh
TWOFLOW_DRY_RUN=1 bash $W/scripts/native/twoflow_driver.sh \
  com.dennywanye.simpleharness.verify09073f30a17bp18120 /tmp/ud /tmp/ev all
$PY $W/scripts/native/twoflow_verify.py --selftest        # 期望 22 项全部可执行
bash $W/scripts/native/preflight_native.sh \
  $H/.local-test-evidence/2026-09-07/credentials/deepseek.env deepseek-v4-flash 18120 32000
```

预检第 1/2 项要求端口 18120 空闲、无 launcher/driver 在跑——**A6 第 12 次整跑必须先结束**。

### 1. 第一次启动（冷启动，全新 userdata）

```bash
cd $W && $PY scripts/native/launch_native_candidate.py --launch \
  --source $W \
  --bundle "$H/tauri-app/src-tauri/target/debug/bundle/macos/SimpleHarness Memory Verify 3f30a17bp18120.app" \
  --installed-target $H/.local-test-evidence/2026-09-07/installed-h0710-m0638-s0313 \
  --env-file $H/.local-test-evidence/2026-09-07/credentials/deepseek.env \
  --model deepseek-v4-flash --memory-probe \
  --evidence-root $H/.local-test-evidence/2026-09-09/twoflow-<host-sha8>/ --port 18120
```

它会打印 `{"evidence": "<E1>", …}`。记下 `<E1>`；userdata 在 `<E1>/userdata`。

> `--source` 指向本工作树：Tauri 只是外壳，后端从 `--source/backend` 起（`DESKPET_BACKEND_DIR`），
> 所以 bundle 的 sha 与 Host 源码 sha 不一致是正常的（A6 第 12 次也是 bundle `3f30a17b` + 源码 `3308af01`）。
> `--installed-target` 用 **m0638**（Memory SDK 0.6.38，与主干 pin 一致）。

### 2. 立刻写窗口覆盖，再补一次预检

```bash
cat > <E1>/userdata/model_overrides.toml <<'EOF'
[models."deepseek-v4-flash"]
context_window = 32000
reasoning_mode = "fast"
EOF
bash $W/scripts/native/preflight_native.sh \
  $H/.local-test-evidence/2026-09-07/credentials/deepseek.env deepseek-v4-flash 18120 32000 <E1>/userdata
```

第 4 项必须转绿。A6 第 12 次实测：launch 后 71 s 写入即生效（`llm.model_info.resolve` 每次取值都重读）。

### 3. 流程一（T1–T11）

```bash
bash $W/scripts/native/twoflow_driver.sh \
  com.dennywanye.simpleharness.verify09073f30a17bp18120 <E1>/userdata <E1> flow1
```

驱动会先生成读夹具、打印 `policy(workflow.db)=auto …`（不是 auto 会显式告警），
然后逐轮发送。`@UI@` 轮用 FIFO 放行（`echo "<观察结果>" > <fifo>`，同 A6/Manual 约定）。

### 4. 重启（驱动的 `@RESTART@` 会把这三步原样打出来）

```bash
# 1) 杀干净——三件都要，少一件启动器就会抛 native_test_already_running
pkill -f 'launch_native_candidate.py' ; sleep 1
pkill -f '/Contents/MacOS/simple-harness' ; sleep 3
lsof -nP -iTCP:18120 -sTCP:LISTEN | awk 'NR>1{print $2}' | xargs -r kill
lsof -nP -iTCP:18120 -sTCP:LISTEN            # 必须无输出

# 2) 同 userdata 重启 -> 新 run 目录 <E2>
cd $W && $PY scripts/native/launch_native_candidate.py --launch \
  --source $W \
  --bundle "$H/tauri-app/src-tauri/target/debug/bundle/macos/SimpleHarness Memory Verify 3f30a17bp18120.app" \
  --installed-target $H/.local-test-evidence/2026-09-07/installed-h0710-m0638-s0313 \
  --env-file $H/.local-test-evidence/2026-09-07/credentials/deepseek.env \
  --model deepseek-v4-flash --memory-probe \
  --userdata <E1>/userdata \
  --evidence-root $H/.local-test-evidence/2026-09-09/twoflow-<host-sha8>/ --port 18120

# 3) 等 UI 不再显示「等待主对话就绪 / 正在重新读取…」
```

三条硬约束（读自 `launch_native_candidate.py`）：

1. `--userdata` 走 `resolve(strict=True)`，目录**必须已存在** → 只能是第一次跑出来的 `<E1>/userdata`；
2. 带 `--userdata` 时启动器**不重写** `llm_runtime.json`（`:124-125`），
   `model_overrides.toml` 同样原样保留 → 窗口仍 32000、thinking 仍关闭；
3. 启动器探到任何 `/Contents/MacOS/simple-harness` 进程即抛 `native_test_already_running`（`:75-79`）。

### 5. 流程二（T12–T22）

```bash
bash $W/scripts/native/twoflow_driver.sh \
  com.dennywanye.simpleharness.verify09073f30a17bp18120 <E1>/userdata <E1> flow2
```

**evidence-dir 两次都传 `<E1>`**：`twoflow-progress.jsonl` 必须是同一份，核对器靠它做逐轮增量。
`<E2>` 只在核对时用 `--second-log/--second-launch` 带进来。
flow2 的首轮之前驱动会停在 `@RESTART@` 提示上，把观察结果（`E2=… pid=… primary_id_unchanged=1`）
喂进去后才继续；这一行会以 `turn=-1` 记进 progress。

### 6. 核对

```bash
$PY $W/scripts/native/twoflow_verify.py --evidence <E1> \
  --second-log <E2>/native.log --second-launch <E2>/launch.json \
  --installed-target $H/.local-test-evidence/2026-09-07/installed-h0710-m0638-s0313 \
  --out <E1>/twoflow-verify.json
$PY $W/scripts/native/memory_probe_report.py <E1>/native.log <E2>/native.log   # 事件 X-3
```

---

## 三、证据落点（重启不搬家）

```
<evidence-root>/
├── primary-ui-xxxxxxxx/            # <E1> 第一次启动
│   ├── launch.json                 # userdata / pid / model / memory_probe
│   ├── native.log                  # 第一段日志
│   ├── twoflow-progress.jsonl      # ★ 两个阶段共用的唯一进度文件
│   ├── twoflow-verify.json         # 核对产物
│   └── userdata/
│       ├── model_overrides.toml    # 32000 + fast（跨重启保留）
│       ├── llm_runtime.json        # 重启时不被重写
│       ├── memory-probe/           # --memory-probe 的 tracemalloc 快照
│       └── data/
│           ├── state.db            # 路由/任务/绑定/Run 头/搜索回执
│           ├── workflow.db         # ★ 授权策略唯一权威
│           ├── sdk-product-state.db# task_grants；同名策略表是残留，不采信
│           ├── human_memory_v7.db  # 认知记忆/召回/抑制/证据信封
│           ├── operation-audit.db  # 受控审计面
│           └── simple-harness-sdk/execution-v6.sqlite3
└── primary-ui-yyyyyyyy/            # <E2> 重启后，只多出 native.log + launch.json
```

`<E1>` 与 `<E2>` 的 `launch.json` 里 `userdata` 字段必须**相同**、`pid` 必须**不同**——这是 TF-3 的核心证据。
若 flow2 中途因 F06 再重启一次，会多出 `<E3>`：把它的 `native.log` 一并记进结果文档，
核对时 `--second-log` 仍传 `<E2>`（TF-3 只需要证明「至少发生过一次同 userdata 重启」）。

SHA-256 归档清单已由核对器自动输出，本轮新增 `workflow.db` 一项。

---

## 四、发送前的有界门（本轮唯一一处驱动健壮性修复）

**症状**：一轮的 `send_confirmed` 报 `no new Run head after two sends`，但 App 一切正常。
**成因**：上一轮留下一张没被应答的授权卡（底部『允许本次绑定 / 拒绝』或 SDK 弹窗『允许一次』），
或发送键还没变回 `发送`（运行中是 `■ 停止`，`tauri-app/src/code-panel/InputBar.tsx:836`）。
`send.sh` 把文本填进 `AXTextArea` 后无条件点 `发送`——点了一颗当时不存在/不生效的按钮，
于是没有新 Run 头，重试一次仍然撞同一张卡。

**修复**（`twoflow_driver.sh` 与 `manual_driver.sh` 各一处，逐字节同源）：

```bash
UI_BLOCKERS='允许一次|允许本次绑定|重试完成已允许的绑定|拒绝|停止'
wait_ui_send_ready() { ... }   # 用 ax_dump.sh 枚举 AXButton，按 5 s 轮询，上限 A6_UI_SETTLE（默认 180 s）
```

- 在 `send_confirmed` 里**首发前与重试前各调用一次**（只在首发前调用的话，重试仍会撞同一张卡）；
- **永远返回 0**：等不到也照旧发送（不比修改前更糟），但把 `:ui_blocked` 拼进
  `send_confirmed` 的回声 → 落进 progress 的 `note`，判定时按「该轮环境受扰」看待；
- `ax_dump.sh` 不存在时回 `axdump_missing` 并立刻放行——不因为缺一个辅助脚本把旅程卡死；
- 主循环的分支由 `[ "$SENT" = send_failed ]` 全等匹配改成 `case ... send_failed*)` 前缀匹配，
  否则带后缀的失败会被当成成功往下走。

回归用例 `test_both_drivers_wait_for_pending_authorization_cards_before_sending` 把上述四条全部钉住。

---

## 五、判定规则的增量

`00-PLAN.md` §1 / §4 已同步，这里只列本轮新增的两项与两条 FAIL：

| 项 | PASS | FAIL | INCONCLUSIVE / BLOCKED |
|---|---|---|---|
| **TF-16** 读绑定跨重启复用 | T4 `binding_roots` +≥1（日志有 `read_workspace_binding_revised`）；重启后 T15 `binding_roots`/`binding_proposals` 增量**均为 0** 且本轮有真实读效应 | **T15 又新增了根或提案** —— flow1 建的读绑定没活过重启 | T4 没新增根（模型压根没读夹具 → 链路未触发）；T15 无任何工具效应（只证明了没重绑，没证明读到了）；T4/T15 任一 timeout/send_failed → BLOCKED |
| **TF-17** 策略=Auto | `workflow.db` 的 `mode='auto'`，且 progress 逐轮 `policy_mode` 全程 auto | `workflow.db` 不是 auto，或旅程中途被改过策略 | `workflow.db` 读不到策略（**绝不**回落读 `sdk-product-state.db` 的残留行） |

新增的 FAIL 条件（§4）：重启后 T15 重新绑定；`workflow.db` 策略不是 auto。

TF-17 不是形式主义：Auto 是「读闸门提案自动授予」的前提。若策略被误设成 manual，
T4 会弹出目录授权卡等真人应答，TF-16 的判据整个不成立——先判 TF-17 才能读懂 TF-16。

---

## 六、本轮的验证

| 项 | 结果 |
|---|---|
| `bash -n scripts/native/twoflow_driver.sh` | 通过 |
| `bash -n scripts/native/manual_driver.sh` | 通过 |
| `TWOFLOW_DRY_RUN=1 …  all` | 打印 T1–T22 完整计划（含两条 `@UI@` 的操作要点）；**不建证据目录、不建 progress、不建夹具、不读 DB、不发消息** |
| `twoflow_verify.py --selftest` | **22 项全部可执行**；TF-1..TF-14 / TF-16 / TF-17 / NC-T1..T5 = PASS，TF-15 = INCONCLUSIVE（自检不带 `--installed-target`，指纹重放器未装配，属预期） |
| `backend/tests/native/test_twoflow_verify.py` | **新增 17 例，全绿** |
| `backend/tests/native/`（全目录，单进程） | **116 例全绿**（原 99 + 新 17） |

自检夹具里两个库的策略**故意相反**（`workflow.db=auto` / `sdk-product-state.db=manual`）：
核对器一旦回落读错库，TF-17 会立刻由 PASS 转 FAIL，自检就红——这正是 MM-D1 那条教训的牙齿。

---

## 七、明确没做

- **没有启动原生应用**（18120 上 A6 第 12 次整跑仍在进行），因此本轮**不能声称**
  T4 的读闸门重路由在两轮旅程的语境里已实测走通——它只在 A6 run12b 的 T6 上被证明过。
  若今晚 T4 的 `binding_roots` 增量为 0，按 TF-16 的 INCONCLUSIVE 分支处理，
  备用杠杆：把绝对路径再念一遍重问一次。
- 没有改任何产品代码、没有动 `a6_verify.py`、没有合并本分支。
- 没有为 `manual_driver.sh` 补 dry-run（任务只要求 `twoflow_driver.sh` 有；Manual 侧维持 `bash -n`）。
- `send_failed` 仍是「记录并继续」，没有改成硬失败——它与 Manual 的绑定硬判据是两类失败，
  本轮不改其语义。
