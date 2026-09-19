# 交接：LLM-native HTN 升级 第 H1 阶段（2026-09-19 13:45 停手）

接手人：Codex（用户官方额度）。本文件自足，按「立刻可做的三件事」直接开工即可。
写作日期 2026-09-19；口径：**说明用大白话，代号只在命令与路径里出现**。

---

## 1. 这个升级在做什么

让分层规划的模型回复从「一大段计划提案」改成**一次只给一个结构化的决定**，系统按固定顺序校验后再翻译成现有流程执行。

- 权威计划：`plans/taskSys2/升级planV1/v1.4/simpleharness-llm-native-htn-execution-plan-v2.zh-CN.md`（下称 **V2 计划**）
- 裁定补遗（与 V2 同等效力，冲突时**补遗优先**）：`plans/taskSys2/升级planV1/v1.4/LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md`
  - 末尾有 2026-09-19 06:30 追加裁定：**请求包不加第六个模型可见字段**；核对引用以「请求记录里保存的清单」为准，不从包体重算。
- 源码定位对照：`plans/taskSys2/升级planV1/v1.4/H1-V2对照源码冲突检查-2026-09-18.zh-CN.md`
- 已用过的任务书全部留档：`plans/taskSys2/升级planV1/v1.4/任务书-H1-2026-09-19/`

---

## 2. 现在的代码状态

**SDK 仓库** `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk`

- `main` = `0d89307`，**已推送 origin**。
- 起点是发布版 `v0.12.2`（`7f839f0`）。

### 2.1 已合入 main 的 7 件（每件都经过独立核验 + 确定性检查，报告在仓库里）

| # | 内容 | 主要产物 |
|---|---|---|
| 1 | 决定类型地基 | `src/agent_orchestrator/contracts/planning_decisions.py` |
| 2 | 决定信封与各类载荷、封闭枚举 | 同上（现 1836 行） |
| 3 | 数据库迁移 19 与决定存储（三张表） | `storage/planning_decision_schema.py`、`storage/planning_decision_store.py` |
| 4 | 第 8 版规划提示词 + 与请求包版本配对 + 指纹登记 | `runtime/role_templates.py` |
| 5 | 新协议解码器 | `planning/decision_codec.py` |
| 6 | 格式说明文件 + 11 个合法样例 + 40 个反例 + 打包 | `contracts/schemas/planning-decision-v1.schema.json`、`tests/orchestrator/full_target/fixtures/planning_decision_v1/` |
| 7 | 规划请求包的新增五项内容 | `planning/htn/planner_package.py` |

**main 上的确定性检查（最近一次，2026-09-19 11:00 左右）**：全量 `3478 passed`（升级前基线 2960）、旧模式回归 `560 passed`（基线 560）、冻结合同文件零改动、无密钥泄漏。

关键常量：`HIERARCHICAL_PLANNER_PACKAGE_VERSION = 3`（缺省仍是旧协议，**没动**）、新增 `PLANNING_DECISION_PACKAGE_VERSION = 4`、包内字符串标签 `planner-package-hierarchical-v5`、提示词 `planner-hierarchical-v8`。

### 2.2 做完但**还差独立核验**的 1 件：准入检查

- 分支 `h1-f-decision-admission`，head `5804dc3`，**已推送 origin 备份**。
- 工作树 `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk-h1f`（干净）。
- 新增：`src/agent_orchestrator/planning/decision_admission.py`（1266 行）、`tests/orchestrator/full_target/test_planning_decision_admission.py`、`plans/llm-native-htn/H1/journal-F.md`。
- **我已跑过完整确定性检查，全绿**：专项 `132 passed`、全量 `3610 passed`、旧模式 `560 passed`、只动白名单文件、冻结文件零改动、无密钥。
- 实施者自己做了 27 个变异全部被测试杀死。
- **缺的就是**：另开一个会话做独立核验（变异 + 刁钻输入 + 逐条对规格）。核验任务书已写好：`任务书-H1-2026-09-19/h1f-verify.md`。

### 2.3 刚开工 2 分钟就被我停掉的 2 件（**代码为零，从头做**）

- `h1-g-decision-adapter`（工作树 `...-h1g`，基于 `5804dc3`）：适配层。任务书 `h1g-impl.md` / `h1g-verify.md`。
- `h1-s-protocol-switch`（工作树 `...-h1s`，基于 `0d89307`）：任务规格开关与协议绑定写入。任务书 `h1s-impl.md` / `h1s-verify.md`。

### 2.4 本阶段剩下没开始的

- **接入主干热文件**：`orchestrator/hierarchical_dispatch.py`、`orchestrator/event_handler.py` 严格双分支（旧协议走原路径一字不改；新协议走 v8 + 包 4 → 存请求 → 解码 → 准入 → 适配 → 原有编译提交）。V2 第 45 节。**原计划留给 Grok，因为它是最危险的热文件**。
- **收尾**：变异专项 ≥12、真实模型冒烟、审计包（V2 第 56 节）。

---

## 3. 立刻可做的三件事（按此顺序）

### 第一件：给「准入检查」做独立核验

```bash
cd /Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk-h1f
# 核验要求全文见 plans/taskSys2/升级planV1/v1.4/任务书-H1-2026-09-19/h1f-verify.md
```

- 核验员**不要 commit、不要 push**，报告写到 `plans/llm-native-htn/H1/reviews/verify-H1-F-2026-09-19.md`（纯英文文件名）。
- 结论三选一：可合 / 修后可合 / 不可合。
- 判为「修后可合」就回到实施者补，再核验；判为「可合」按第 4 节合入 main。

### 第二件：适配层（`h1-g-decision-adapter`）

任务书 `h1g-impl.md`。一句话：把「已通过准入的决定」翻译成现有主链能直接吃的提案对象，**不换编译器**。
核心验收：同一个意图，旧写法解析出的提案与新决定翻译出的提案**规范 JSON 逐字节相同**（有意差异必须逐条写明理由）。

**注意依赖**：它建在 `h1-f` 分支头上。如果准入检查因核验又改了，先把 `h1-g` 变基到新的 `h1-f` 头。

### 第三件：开关与协议绑定写入（`h1-s-protocol-switch`）

任务书 `h1s-impl.md`。要点：

- `MissionSpec.planning_protocol_version` 缺省 `legacy-plan-proposal-v1`；`to_json()` **只在非缺省时**写出该键 → **缺省任务的规格字节与哈希必须一字不变**（先写黄金测试钉死再改代码）。
- 新协议任务在**创建任务的同一笔事务**里写 `mission_planning_protocols` 一行；旧协议不写行；读不到行 = 旧协议。
- 任务创建后不可改协议；恢复路径只读这张表，**不看环境变量**。
- 这个开关**不得进入** `policy_snapshot()`。
- 本片**不做**派发分支，开关不产生任何运行期行为差异。

`h1-g` 与 `h1-s` 互不碰同一个文件，可并行。

---

## 4. 合入 main 的固定流程（每件都照做）

```bash
SDK=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk
L=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/scripts/agent_lanes
T=tests/orchestrator/full_target

# 1) 归档核验报告与检查报告到切片分支
#    plans/llm-native-htn/H1/reviews/  与  plans/llm-native-htn/H1/gates/
# 2) 密钥扫描（新增行里不得出现 sk-/xai-/Bearer/端点 IP）
git -C $SDK-<片> diff <基准>..HEAD | grep -E '^\+' | grep -cE '\bsk-[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,}|171\.80\.'
# 3) 合入
git -C $SDK merge --no-ff <分支> -m "merge(<片>): ..."
# 4) 在合并后的 main 上重跑完整检查，报告存档到 gates/
$L/sdk_gate.sh $SDK <合并前的 main> --tests "$T/<本片测试>" --full --max-sentinel 26 --out /tmp/gate.json
# 5) 推送、删工作树与分支
git -C $SDK push origin main
git -C $SDK worktree remove --force $SDK-<片>; git -C $SDK branch -D <分支>
```

检查脚本 `sdk_gate.sh` 会查这些项：工作树干净、只改白名单、冻结合同文件未动、无密钥、ruff、导入来源、专项测试、全量测试（对比基线 2960）、旧模式回归（基线 560）、旧路径标记计数（必须 ≤ 26）。

---

## 5. 规矩（必须遵守）

1. **测试先行**：先写会失败的测试并提交，再写实现。
2. **独立核验**：实现与核验必须是**不同的会话**；核验员只读不写、不 commit、不 push；必须做变异（每个变异前把原文件复制到 /tmp，变异后相关测试至少 1 条转红，再从副本恢复并确认工作树干净）。
3. **存活且行为可分辨的变异 = 测试缺口**，按必修处理；等价变异只备案。
4. **不许改**：`contracts/` 下 v0.12.2 时就存在的文件（新增文件可以）；旧协议的任何行为与字节。
5. **不许**在共享工作树上 `git stash` / `checkout` / `reset`。
6. **密钥**：任何情况下不打印、不提交 API key、token、`.env`、`llm_runtime*.json`、`~/.codex/auth.json`、`~/.grok/auth.json`。
7. **文档用中文**，代码与注释用英文。
8. **数字不许编**：测试尾行、git 输出一律原样粘贴。
9. 规格没覆盖的新问题 → 写 `plans/llm-native-htn/H1/BLOCKER-<片名>.md` 并**停在那一点**，交给计划作者裁定，不要自己改线上字段名或语义。

---

## 6. 已知坑

| 坑 | 说明 |
|---|---|
| 两个偶发失败的旧测试 | 高负载下 `tests/orchestrator/step02/test_live_provider_progress.py::test_live_worker_progress_prevents_false_stall_without_new_attempt` 与 `tests/orchestrator/p35/test_context_cold_recovery.py::test_rotated_worker_context_sigkill_cold_unknown_preserves_frozen_request` 各偶发超时一次，单独重跑即过。判定前先单独重跑 3 次。 |
| 旧路径标记计数 | 检查脚本数的是 `grep -rn --include='*.py' "_new_mode" src/agent_orchestrator \| wc -l`，正常值 **26**；Python 缓存文件会干扰，先 `find . -name __pycache__ -exec rm -rf {} +`。 |
| 中文文件名的未提交报告 | 曾因 `git ls-files --others` 转义中文名而丢过一份 395 行核验报告。核验报告建议用**纯英文文件名**。 |
| 做题程序的输出目录 | `run_h_arm.py --out` **必须传绝对路径**，否则评分子进程找不到交付物，整批记成评分出错。 |

---

## 7. 基线测试数据（给最后的真实模型冒烟用）

- 目录：`simple_harness/.local-test-evidence/2026-09-16/htn-acceptance/runs-deepseek/h-arm-0.12.2-legacy/`
- 口径：发布版 0.12.2 + **旧协议** + DeepSeek flash，7 题 × 2 遍 = 14 局。
- 结果：官方隐藏测试通过 **10** 局；系统判「完成」**5** 局（全部通过官方测试，假完成 0）；**代码已过官方测试、但任务被判失败 5 局**（规划失败 2、无可派发工作 1、根评审修复用尽 2）；真没做出来 4 局。
- **新协议上线后重跑同一批题，主要看那 5 局「做对了却判失败」能不能变成「完成」**。
- 同一批题 Grok 的成绩：14 局过 10 局，其中 12 局判完成。**DeepSeek 与 Grok 的成绩分开记，不混算。**
- 做题程序用法见 `.../htn-acceptance/runner/RUNBOOK.zh-CN.md` 与 `JOURNAL.zh-CN.md`（§33 记了 DeepSeek 通道的接法）。

---

## 8. 本机上还留着的东西

| 东西 | 位置 / 状态 |
|---|---|
| 排队闸口（DeepSeek 用） | `simple_harness/backend/scripts/agent_lanes/daycard_gate.py`，**进程还在跑**（2 个名额）。Codex 若走官方额度就用不上它，可以 `pkill -f daycard_gate.py` 停掉。 |
| 工作树 | `...-h1f`（有代码，待核验）、`...-h1g` / `...-h1s`（空的，可删可留）、`...-v0122`（0.12.2 分离副本，做题程序钉版用，**别删**）、`...-p23d/p31/p32/p36/p51`（旧阶段遗留） |
| 流水线脚本 | `backend/scripts/agent_lanes/{sdk_gate.sh, slice_pipeline.sh, codex_task.sh, grok_task.sh, selftest_gate.sh}` —— 这些是为 DeepSeek 通道写的编排脚本，Codex 官方额度下可以不用，直接按第 3 节的任务书干活即可。 |
| 已停的后台进程 | 所有流水线、代理任务、哨兵均已按进程号停止，工作树全部干净。 |

---

## 9. 一句话总结

7 件已进主干且全绿；准入检查代码做完、检查全绿、**只差独立核验**；适配层与开关两件有任务书但零代码；之后还剩「接入主干热文件」和「变异 + 真实模型冒烟 + 审计包」。
