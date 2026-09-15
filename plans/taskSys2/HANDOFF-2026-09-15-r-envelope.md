# A 轮交接（R 信封新身份已复验，下一 session 不要重跑 96）

**已完成（2026-09-15 21:33 CST）：** 用户确认只读拆解后选方案 2（修协议 → 冻新身份 → 仅 `deepseek-flash` 数例 R 复验）。SDK `f7432dc` 已修计量预估并避免空选择掩盖预算关闭。新身份 `a96-flash256k-r-envelope-v2` **2/2 选择闭环**，官方 1/2，未知用量 0。v1 的 96 收条、Qwen 16/96、N5 均原样保留。

最后核查：2026-09-15 23:27 CST。本文件是当前接手入口。上一份只读拆解交接仍有效，但「下一 session 只读拆 Flash 96」已被本切片覆盖，**不要再拆同一批 96**。

可复制给新 session 的指令在文末。

## 1. 接手结论

A 轮现在是 **四套分开的身份**，分数不能加、不能改写：

| 身份 | 原始测试/结论文档 | 结果 | 能不能当正式 A 轮 |
|---|---|---|---|
| N5 Qwen 官方 slack 干净/攻击各一例 | [testPhase1-n5-blackboard-2026-09-15.md](testPhase1-n5-blackboard-2026-09-15.md) | 两例 official true；攻击未成功；KnowledgeUsed 0 | N5 切片，不是 96 矩阵 |
| Qwen `a96-qwen256k-v2` | [HANDOFF-2026-09-15-a-round.md](HANDOFF-2026-09-15-a-round.md) §5 | **停在 16/96**，3 未知用量 | **不能**。用户已决定不补 |
| Flash `a96-flash256k-v1` | [testPhase1-a96-flash256k-2026-09-15.md](testPhase1-a96-flash256k-2026-09-15.md) + [拆解](testPhase1-a96-flash256k-dissection-2026-09-15.md) | **96/96 收条**；official **19/96**；S/R 0/24；未知 0 | **不能**改写成过关。S/R 是计量把第一轮掐死，不是题不会做 |
| Flash `a96-flash256k-r-envelope-v2` | [testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md](testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md) | **2/2 R 信封闭环**；官方 1/2；未知 0 | **只证明**修好计量后 R 规定信封能走。不是满矩阵，不是 S，不是黑板收益 |

**下一 session 不要默认开跑。** 若继续：只能冻**更新的**身份；模型目前只许 `deepseek-flash`；S 尚未在新计量下复测。禁止重跑 v1 的 96、禁止补 Qwen 80、禁止开 512K、禁止把 v2 的 2 例写进 v1 的 19/96。

## 2. 仓库与代码

| 仓库 | 路径 | HEAD（编写时与 origin/main 一致） |
|---|---|---|
| Host | `/Users/denny/projects/simple_harness` | `f9b8a961` docs: record Flash R envelope retest after tool-schema reservation（本交接提交后会再前进一步） |
| SDK | `/Users/denny/projects/simple-harness-sdk` | `f7432dc` fix(appworld): reserve tool schemas before R self-selection |

生产代码已从 SDK `69d679c`（AgentDojo 观察晋级，v1 矩阵冻结源）前进到 `f7432dc`（R 计量/空选择）。v1 的 96 是在 `69d679c` 快照上跑的，**不要用新代码重放 v1 目录**。

SDK 改动文件：

- `src/simple_harness/agents/context/tokenizer.py` — `estimate_provider_request`
- `src/agent_orchestrator/evaluation/appworld_arms.py` — 候选全空则跳过选择轮
- `tests/orchestrator/gap_phase1/test_appworld_arms.py` — 25 PASS

SDK 公开仓：凭据、用量明细、ignored 证据不要写进去。工作区不要 reset。

## 3. 必须保持的约束

- 主 session 模型设置不要改。不用 plan-test。不打包、不发布。
- **测试模型目前只许官方 `deepseek-flash`**（`api.deepseek.com`）。不要用 `deepseek-v4-pro`、svtun GPT、本地 `qwen38-flash-next`。
- 本地 `192.168.10.27:11434` 探活时实际 id 是 `mia-dsv41-ablit`，**不能当 Qwen**。Spark `192.168.11.157` 当时 SSH 超时。
- **Qwen 96 不补。** 不把 Flash 成绩写成 Qwen 成绩。
- 正式块不混模型、不混身份。B 轮仍是闲时 Flash **512K**，256K 不能代替。
- 失败保留，不重跑追 PASS，不加预算。未知用量停该身份继续扩跑（v1 与 v2 未知都是 0）。
- 原始收据只在 `.local-test-evidence/`（gitignore）。Git 只写文字结论、run id、相对路径、SHA。NAS 未配，**不要删**原始证据。
- 源码 UI 可能仍在 backend **18140** / Vite **15173**。不要再起第二套 UI。R 复验用过 **18260**，已退出。
- 权限：auto 默认、不弹窗。报告要有总体 / 当前 / 距上次增量三张表。
- 不要打印/复制 `.env` 或 API key。Flash 凭据字段名是 `DEEPSEEKER_APIKEY`。

## 4. 原始测试文档目录（不要弄丢引用链）

Git 里的文字结论是「测试文档」；ignored 目录才是原始收据。下一 session 先读文档，再按需打开收据，不要把整份 prompt 抄进 Git。

### 4.1 更早的四臂/协议原文（A96 之前）

这些是 R 信封协议和四臂定义的来源，不是这次 Flash 96。

| 文档 | 仓库 | 它记录什么 |
|---|---|---|
| [testPhase1-results-2026-09-14.md](testPhase1-results-2026-09-14.md) | Host | **原始 16 次四臂**（本地 Qwen3.8，4 题×4 臂）。R 当时已有自选 JSON 失败 |
| [testPhase1-followup-2026-09-14.md](testPhase1-followup-2026-09-14.md) | Host | 后续修复：R 改为 `appworld-r-self-selection-v1`，允许解释前缀 + 唯一封闭 JSON |
| [testPhase1-flash-final-2026-09-14.md](testPhase1-flash-final-2026-09-14.md) | Host | 独立 Flash 16 次回收，与 A96 v1/v2 不是同一身份 |
| [testPhase1-two-wave-evaluation-2026-09-14.md](testPhase1-two-wave-evaluation-2026-09-14.md) | Host | A96=Qwen256K、B96=Flash512K 的两轮协议；本次 256K Flash 不能代替 B 轮 |
| SDK `plans/2026-09-14-gap-phase1/RESULTS.md` | SDK | 与 Host 16 次结果对应的 SDK 侧原文 |
| SDK `plans/2026-09-14-gap-phase1/FOLLOWUP.md` | SDK | R 选择契约原文：`appworld-r-self-selection-v1` |
| SDK `plans/2026-09-14-gap-phase1/FLASH-FINAL.md` | SDK | Flash 16 次 SDK 侧原文 |
| SDK `plans/2026-09-14-gap-phase1/HANDOFF.md` | SDK | 旧总交接；N1–N8 历史检查点，A96 当时未完成 |

### 4.2 A96 冻结与 Flash v1 原文（这次拆解/复验的对照基线）

| 文档 | 它记录什么 |
|---|---|
| [testPhase1-a96-freeze-2026-09-15.md](testPhase1-a96-freeze-2026-09-15.md) | 12 题 dev 身份冻结；D-arm smoke 超时失败保留；当时 96 未开跑 |
| [testPhase1-a96-flash256k-2026-09-15.md](testPhase1-a96-flash256k-2026-09-15.md) | **Flash v1 96/96 收条原文**：official 19/96，S 0、R 0、D 11、F 8 |
| [testPhase1-a96-flash256k-dissection-2026-09-15.md](testPhase1-a96-flash256k-dissection-2026-09-15.md) | **只读拆解原文**：S 1 调用 0 工具；R 24/24 `self-selection has no output`；D/F 知识复用全 0 |
| [HANDOFF-2026-09-15-a-round.md](HANDOFF-2026-09-15-a-round.md) | 上一份交接：要求只读拆 96，不要重跑 |
| [HANDOFF-2026-09-15.md](HANDOFF-2026-09-15.md) | 更早总交接；其中 N5「未实现」、A96「未开始」已被后续文件覆盖，约束仍有效 |
| [testPhase1-n5-blackboard-2026-09-15.md](testPhase1-n5-blackboard-2026-09-15.md) | N5 黑板正负控 + Qwen 官方一对 |
| [testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md](testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md) | **本次 R 信封小复验结论**（2 例） |
| 本文件 | 当前接手入口 |

### 4.3 本机原始收据（`matrix-v2/` 仍 gitignore；其余已按用户要求进 Host main，不要删）

根：`/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-15/a96/`

| 身份 | 目录 | 关键文件 |
|---|---|---|
| Flash v1 96（拆解对象） | `matrix-flash256k-v1/` | `identity.json`（experiment_id `a96-flash256k-v1`，sdk `69d679c`）、`summary.json`（96/96，official_true 19，calls 2120，tokens 28574880）、`episodes/<run_id>/result.json` **96 份**；R 失败例有 `exception-trace.txt` 与 `episode/turns.json` |
| Flash v1 监督脚本 | `run-a96-flash6.py` | 6 worker，端口 18250–18255；**不要再跑** |
| Flash R v2（本次） | `matrix-flash256k-r-envelope-v2/` | `identity.json`（`a96-flash256k-r-envelope-v2`，sdk `f7432dc`）、`summary.json`、两份 `episodes/<run_id>/result.json` + `episode/turns.json` |
| Flash R v2 脚本 | `run-r-envelope-v2.py` | 单进程，端口 18260，已退出 |
| Qwen 截断 | `matrix-v2/` | `progress.json` completed 16 / pending 80；`operator-stop.json` |
| 12 题单 | `appworld-taskset.json` | 已核 public instruction SHA-256，dev split |
| N5 | `../n5-blackboard/` | `audit.json` |
| AppWorld venv / 更早 gap-phase1 | `/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-14/gap-phase1/` | v1/v2 都用这里的 `appworld-venv` |

v2 两个 run id（可在 Git 引用，原始 JSON 不要 commit）：

- `530b157_1` → `3d31e983d8f0d3f177a097fd3f900dc0299541e21bcd607399fba86aa88bc695`（selected 2，官方 true）
- `0d8a4ee_1` → `45ffffa34ac87eee95a644c521e251fd9db4dd46ba537bf826b5baa9aa95b056`（selected 1，官方 false）

对照锚点：v1 同题 `0d8a4ee_1` 第一轮 billed input **980** 关 meter；v2 同题第一轮预留 1549 / 实计 **980**，选择轮 committed。

## 5. 机制（给下一 session 避免再误判）

v1 S/R 全灭的直接原因：

1. `MeteredProvider` 要求 `estimate_input_tokens` 是上界。
2. A96 runner 的 `FlashCounter.estimate_input_tokens` 只数 message 文本 + 128，**漏了 tool schema**。
3. 第一轮实际 input≈980 > 预留 → `_settle` 抛 `ExperimentBudgetExhausted`，meter 关闭。
4. 后续候选/选择轮 admission denied。
5. R 仍去跑选择轮，`public_output is None`，报 `self-selection has no output`。

Agent 自己的 context selection 当时已经把 tools 计成 ~927 tokens（fingerprint `upper-bound-utf8-bytes-div-2:v1`），计量器没用上。

`f7432dc` 后：预估含 tools；候选全空则跳过选择。v2 两例都是 candidate-0 / candidate-1 / self-selection **三轮 committed**，信封为：

```json
{"protocol_version":"appworld-r-self-selection-v1","selected_candidate":N}
```

允许解释前缀。这只证明 **R 协议能走**，不证明编排/黑板。

S 与 R 在 v1 是同一条第一轮预算路径（S 也是 24/24 `experiment_budget_exhausted` / `tool_parse`）。**S 还没有新身份复测。**

## 6. 三张进度表

### 总体

| 包 | 已完成切片 | 未关闭 | 时间/用量 |
|---|---|---|---|
| N5 | 确定性正负控；Qwen 官方干净/攻击各一例 | 完整对照；自然多 Task KnowledgeUsed | 确定性 7.11 秒；Qwen 17 调用 85419 tokens；攻击未成功 |
| N4/A96 Qwen | 12 题 dev hash 已核 | **不补** 剩余 80 | 停在 16/96；3 未知用量 |
| N4/A96 Flash v1 | **96/96 + 只读拆解** | 不是 512K；S 未在新计量下复测 | 2120 调用 / 28574880 tokens；utility 19/96 |
| N4/R 信封 v2 | **2/2 选择闭环** | 不是满矩阵 | 59 调用 / 822754 tokens；官方 1/2 |
| N1–N3 / N6–N8 / B512K | 局部 | 长时恢复、困难消费、Gaia2、B 轮 | 费用上限未定 |

### 当前

| 项 | 事实 |
|---|---|
| 任务 | 写交接并推远程 main；不新开矩阵 |
| 进程 | 无 run-a96 / 无 18260；UI 可能仍 18140/15173 |
| 模型 | 后续测试只许 `deepseek-flash` |
| 输出 | 本文件 + SDK 指针；原始 JSON 不 commit |

### 本交接编写前的增量

| 事项 | 结果 |
|---|---|
| 只读拆 v1 | Host `3580f26e`；S/R 协议未执行，D/F 无知识复用 |
| 修计量 + R 空选择 | SDK `f7432dc`，定向 25 PASS |
| 新身份 2 例 R | 信封 2/2；官方 1/2；未知 0 |
| 结论文档 | Host `f9b8a961` |

## 7. 阅读顺序

1. 本文件
2. [R 信封 v2 结论](testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md)
3. [Flash v1 96 收条](testPhase1-a96-flash256k-2026-09-15.md)
4. [v1 只读拆解](testPhase1-a96-flash256k-dissection-2026-09-15.md)
5. [上一份 A 轮交接](HANDOFF-2026-09-15-a-round.md)（约束仍有效；「只读拆 96」已被覆盖）
6. 若要核对 R 协议最初定义：[testPhase1-followup-2026-09-14.md](testPhase1-followup-2026-09-14.md) 与 SDK `FOLLOWUP.md`
7. `ARCHITECTURE/index.md` 顶部

## 8. 给新 session 的指令（可整段粘贴）

> 请接手 `/Users/denny/projects/simple_harness/plans/taskSys2/HANDOFF-2026-09-15-r-envelope.md`。原始测试文档链在第 4 节：v1 96 收条是 `testPhase1-a96-flash256k-2026-09-15.md`，只读拆解是 `testPhase1-a96-flash256k-dissection-2026-09-15.md`，R 信封小复验是 `testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md`；更早的四臂原文是 `testPhase1-results-2026-09-14.md` / `testPhase1-followup-2026-09-14.md`。不要重跑 v1 的 96，不要补 Qwen 96，不要混身份，不要开 Flash 512K，不要打包。代码以 SDK `f7432dc` 为准。后续真实测试目前只许 `deepseek-flash`。v1 的 19/96 和 Qwen 16/96 截断、N5 攻击未成功都必须保留。S 若要复测必须冻新身份、数例即可。原始收据除 Qwen `matrix-v2/` 外已在 Host `.local-test-evidence/2026-09-15/a96/` 并推远程 main；`matrix-v2/` 仍只留本机，不要删。按总体 / 当前 / 距上次三张表报告。
