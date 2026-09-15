# 交接（2026-09-16）：Grok-4.6 单模型 A96 基线已完成并冻结，其余停手

**最后核查：2026-09-16 05:10 CST。本文件是当前接手入口。** 上一份交接 [R 信封](HANDOFF-2026-09-15-r-envelope.md) 的"只许 deepseek-flash / A、B 两轮"已被用户 2026-09-16 的决定覆盖，其余约束（失败保留、不混身份、原始收据不进 Git、不打包）仍有效。

## 1. 接手结论

1. 评测计划 N1–N8 现在**统一用 grok-4.6 中等推理**（SuperGrok 订阅通路），不再分 A 轮 Qwen / B 轮 Flash。Qwen 16/96、Flash v1 19/96、R 信封 v2、N5 Qwen 一对全部只作历史保留，不比较、不混算。
2. **N4 已完成**：身份 `a96-grok46-256k-v2`，12 题 × S/R/D/F × 2 次 = 96 局全部有官方收据，官方通过 93/96（S 24、R 24、D 22、F 23），未知用量 1 局，3023 万 token。结论文档：[testPhase1-a96-grok46-2026-09-16.md](testPhase1-a96-grok46-2026-09-16.md)。
3. **N7 配对分析已做**（同文档 §6）：同题同模型下 D/F 没有多解出任何题，成本是 S 的 3.4–4.0 倍，两例"错误宣布完成"都在编排臂，知识复用事件 0。题目对 Grok 太容易（S 满分），这是**基线**，不是编排收益证明。
4. **题单与 runner 已冻结**（`FREEZE.json`，SHA-256 见结论文档 §7）。用户指令：**不再改题单和 runner，其余工作包停手**。这 96 局是将来 HTN（分层任务网络规划）上线后第一次配对对照的现成前半。
5. **下一 session 不要开跑任何新批次。** 除非用户明确说 HTN 已就绪要跑对照，届时按 §4 的配方、只换 SDK 提交、冻新身份。

## 2. 仓库与代码（本机 taiwan）

| 仓库 | 路径 | HEAD |
|---|---|---|
| Host | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness` | 本交接提交后见 `git log -1`；Grok 通路提交 `d107ba10` |
| SDK | `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk` | `61a85eb`（含 f7432dc 计量修复）；**本轮 SDK 未改** |

Grok 通路：`docs/GROK-BUILD-LANE.md`；`backend/scripts/grok_build_runtime.py write/probe/apply/restore/status`。跑 Host 真实模型用例前 `apply`，跑完必须 `restore`。A96 runner 不经过 Host，直接读用户目录的 `llm_runtime.grok.json`（token 不进 Git、不打印）。

## 3. 证据与脚本（`.local-test-evidence/2026-09-16/a96-grok/`）

| 项 | 路径 | 进 Git |
|---|---|---|
| 冻结记录 | `FREEZE.json`（`freeze-record.py --verify` 核对） | 是 |
| runner（冻结） | `run-a96-grok46.py` | 是 |
| 汇总 / 配对分析脚本 | `summarize.py`、`n7-paired-analysis.py`、`recompute-valid.py` | 是 |
| 汇总输出 | `full-summary.md`、`rep0-summary.md`、`n7-paired-analysis.md` | 是 |
| 身份 v2 | `matrix-grok46-256k-v2/identity.json`、`summary.json`、`progress.json`、`grok-usage-audit.jsonl` | 是 |
| 身份 v1（废弃） | `matrix-grok46-256k-v1/identity.json`、`summary.json` | 是 |
| 96 局原始收据 | `matrix-grok46-256k-v2/episodes/<run_id>/result.json` + `episode/`（822 MB） | **否，不删** |
| AppWorld 数据 / venv | `data/`（183 MB）、`appworld-venv/`、`experiments/` | 否 |
| 题单 | `../../2026-09-15/a96/appworld-taskset.json`（已在 Git） | 是 |

## 4. 复跑配方（仅 HTN 对照时用）

```bash
cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-16/a96-grok
./appworld-venv/bin/python freeze-record.py --verify          # 必须 MATCH
# 1) 复制 run-a96-grok46.py 为新文件，只改 EXPERIMENT_ID / OUT 为新身份，其余参数不动（这是配对对照的前提）
# 2) SDK checkout 切到 HTN 提交（工作区必须干净），appworld-venv 里是 editable 安装，自动生效
# 3) 冒烟 4 局：--workers 1 --arms S,R --tasks 530b157_1,0d8a4ee_1 --reps 0
# 4) 全量：nohup ./appworld-venv/bin/python <新runner> --workers 2 --reps 0 ... 再 --reps 1（rep 编号是 0/1）
# 5) 对照：n7-paired-analysis.py 改 OUT 指向两套身份按题配对
```

约束：Grok 周额度先看 grok.com Usage 页（96 局约 3000 万 token）；`STOP` 文件可随时停批，已完成局不会重跑；未知用量 / 失败保留不重跑；不用 `sleep`/`setsid`（macOS 无 setsid，用 nohup 子壳）。

## 5. 三张进度表

### 总体（N1–N8）

| 包 | 已完成 | 未关闭 | 状态 |
|---|---|---|---|
| N1 稳定性/容量 | 共享容量、进程身份、长响应、独立 Judge | 长时恢复 | 停手 |
| N2 可信知识负控 | 确定性正负控、可信观察、失败预算审计 | 困难任务知识消费收益 | 停手 |
| N3 检索/摘要优化 | 两题契约修复 | 完整矩阵 | 停手 |
| N4 96 局四臂对照 | **Grok v2 96/96 完成，93 通过** | 无（基线已冻结） | **完成** |
| N5 AgentDojo | 确定性正负控 40 PASS | 用 Grok 的干净/攻击对照 | 停手 |
| N6 Gaia2/ARE | 动态硬判通过 | 完整 Gaia2、judge | 停手 |
| N7 消融/归因 | **12 题配对分析（vs S）** | 消融（graph_changes/blackboard/critic）未做 | 配对分析完成，消融停手 |
| N8 正式评测/交付 | 源码 UI 与 main 同步 | 正式 split | 停手 |

### 当前（N4/N7 收尾）

| 项 | 结果 |
|---|---|
| 96 局 | 完成，worker exit 0/0，1 未知用量局（网络断连）保留 |
| 配对分析 | R/D/F vs S 通过率差 CI 上界均为 0；成本差 CI 全在零上 |
| 冻结 | FREEZE.json 已生成并 verify MATCH |
| 文档 | 结论文档 + 本交接 + ARCHITECTURE 索引 |
| 进程 | 无 run-a96 / appworld_service / chain 进程；端口 18270/18271 空闲 |

### 自上次报告（02:45）以来

| 事项 | 结果 |
|---|---|
| 第一遍剩余 12 局 | 完成，48/48，46 通过 |
| 第二遍 48 局 | 链式脚本 03:16 自动启动，04:54 完成，47 通过 |
| N7 分析 | 脚本 + 输出 |
| 冻结 | FREEZE.json |
| 用户新指令 | 跑完两遍 → N7 → 冻结 → 停手；记入记忆 |

## 6. 给新 session 的指令（可整段粘贴）

> 请接手 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/plans/taskSys2/HANDOFF-2026-09-16-grok-a96.md`。N4 的 Grok-4.6 96 局基线已完成并冻结（FREEZE.json），N7 配对分析在 `testPhase1-a96-grok46-2026-09-16.md` §6。不要开任何新批次、不要改题单和 runner、不要打包。只有用户明确说 HTN 就绪要做对照时，按交接 §4 配方冻新身份跑。旧模型成绩（Qwen/Flash）只保留不比较。报告按总体 / 当前 / 距上次三张表。
