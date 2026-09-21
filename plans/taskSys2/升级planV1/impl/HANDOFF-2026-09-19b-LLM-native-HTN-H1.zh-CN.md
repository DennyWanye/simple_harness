# 交接（第二份）：LLM-native HTN 升级 第 H1 阶段（2026-09-19 19:20 停手）

本文件**取代** `HANDOFF-2026-09-19-LLM-native-HTN-H1.zh-CN.md`（那份写于 13:45，之后又完成了 5 件）。
接手人：新会话。说明用大白话，代号只出现在命令与路径里。

---

## 1. 一句话状态

**这一阶段的 11 件零件已全部做完并合入主干，每一件都经过独立核验与确定性检查。**
只剩两件：**接入主干热文件**（让新协议真正跑起来）和**阶段收尾**（变异专项、真实模型冒烟、审计包）。

---

## 2. 代码在哪

SDK 仓库 `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk`，`main` = **`5ac3f05`**，**已推送**。
起点是发布版 `v0.12.2`（`7f839f0`）。工作树干净，所有切片分支与临时工作树已删除。

**主干上的完整检查（2026-09-19 19:00 实测）**

| 项目 | 数字 |
|---|---|
| 全量测试 | 3670 通过（升级前基线 2960） |
| 旧模式回归 | 560 通过（基线 560） |
| 新协议相关测试 | 691 条 |
| 冻结合同文件 | 零改动 |
| 密钥扫描 | 干净 |

---

## 3. 已完成的 11 件

| # | 内容 | 主要产物 | 核验 |
|---|---|---|---|
| 1 | 决定类型地基 | `contracts/planning_decisions.py` | 3 轮，可合 |
| 2 | 决定信封与各类载荷 | 同上（1836 行） | 4 轮，可合 |
| 3 | 数据库迁移 19 与决定存储 | `storage/planning_decision_store.py`、`…_schema.py` | 2 轮，可合 |
| 4 | 第 8 版提示词、版本配对、指纹登记 | `runtime/role_templates.py` | 1 轮，可合 |
| 5 | 新协议解码器 | `planning/decision_codec.py`（377 行） | 2 轮，可合 |
| 6 | 格式文件、11 个合法样例、40 个反例、打包 | `contracts/schemas/planning-decision-v1.schema.json`（875 行）+ fixtures | 4 轮，可合 |
| 7 | 规划请求包新增五项 | `planning/htn/planner_package.py` | 7 轮，可合 |
| 8 | 准入检查 | `planning/decision_admission.py`（1275 行） | 6 轮（Grok），可合 |
| 9 | 按裁定把两种决定降为只解码 | 多处 | 1 轮（Grok），可合 |
| 10 | 适配层 | `planning/decision_adapter.py`（318 行） | Grok 可合（裁定前那轮判不可合，也已归档） |
| 11 | 任务规格开关与协议绑定写入 | `orchestrator/planning_protocol_binding.py`（140 行）+ `commit_service.py` | 2 轮，可合 |

**归档位置**（都在 SDK 仓库里，已推送）：
- 核验报告：`plans/llm-native-htn/H1/reviews/`（12 份）
- 检查报告：`plans/llm-native-htn/H1/gates/`（20 份，含每件合入主干后重跑的那份）
- 实施日志：`plans/llm-native-htn/H1/journal-*.md`
- 任务书全部留档在 Host `plans/taskSys2/升级planV1/v1.4/任务书-H1-2026-09-19/`

**关键常量现状**：`HIERARCHICAL_PLANNER_PACKAGE_VERSION = 3`（缺省仍是旧协议，没动）；新增 `PLANNING_DECISION_PACKAGE_VERSION = 4`；协议名 `legacy-plan-proposal-v1` / `planning-decision-v1`；提示词 `planner-hierarchical-v8`；包内标签 `planner-package-hierarchical-v5`。

---

## 4. 本轮做出的一条裁定（必须先读）

**位置**：`plans/taskSys2/升级planV1/v1.4/LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md` 末尾「追加裁定（2026-09-19 15:20）」。

**内容**：「绑定已有目标」与「提出后继任务」这两种决定，**本阶段降为只解码、不执行**。

**为什么**：现网编译器只接受"恰好一个展开，最多再加一个退役方法"，这两种操作在 0.12.2 **只有类型定义和解析，没有任何代码真正执行它们**。经 Grok 独立只读调查确认（留档 `v1.4/核验留档-2026-09-19/独立事实调查-绑定与后继操作是否接线-Grok-2026-09-19.txt`），另有实施者写的最小复现脚本，结论一致。

**影响**：V2 第 12 节启用矩阵、第 44 节映射表中这两行本阶段不生效；解码、格式文件、样例、存储都不变；提示词里教模型用它们的段落已删除并重新登记指纹。等编译器支持了再启用，届时裁定自动失效。

---

## 5. 还剩的两件

### 第一件：接入主干热文件（最危险的一件，原计划留给 Grok）

V2 第 45 节。要改 `orchestrator/hierarchical_dispatch.py` 与 `orchestrator/event_handler.py`，做**严格双分支**：

- 旧协议：**走原路径，一字不改**（560 条旧模式回归必须一条不掉）。
- 新协议：第 8 版提示词 + 包 4 → 存请求 → 解码 → 准入 → 适配 → 原有编译提交。

零件都已就绪，这一件只做接线，不写新算法。

**同时要处理的遗留**（实施者留下的阻塞备忘 `plans/llm-native-htn/H1/BLOCKER-H1-S.md`）：
Host 想通过 HTTP 请求体里的 `planning_protocol_version` 选新协议，目前那个键会被**静默丢弃**——因为转换请求体的那个文件不在上一件的白名单里，实施者按规矩没有越界。
接线这一件的任务书**必须显式把这两个文件放进白名单**，并把断言从"源码里有这段字符串"换成真正的行为断言：
1. `src/agent_orchestrator/api/missions.py` —— 合法协议名 → 契约带上新协议；未知名字 → 报错；缺省 → 规格字节逐字节不变（已有黄金哈希 `d0903d35…d161` 钉住）。
2. `src/agent_orchestrator/orchestrator/event_handler.py` —— 只在现有校验里转发，不改校验语义。

另一份备忘 `BLOCKER-H1-H-live-operations.md` 说的就是第 4 节那条裁定的事，**已经解决**，读的时候别当成未决问题。

### 第二件：阶段收尾

V2 第 47、55、56、59、60 节：变异专项（≥12）、真实模型冒烟（四种场景都要出现、硬约束不能破）、审计包。

**冒烟对照基线已经跑好了**：目录 `simple_harness/.local-test-evidence/2026-09-16/htn-acceptance/runs-deepseek/h-arm-0.12.2-legacy/`，口径是发布版 0.12.2 + 旧协议 + DeepSeek flash，7 题 × 2 遍。
结果：官方隐藏测试通过 10 局；系统判"完成" 5 局（全部通过官方测试，假完成 0）；**代码已过官方测试、但任务被判失败 5 局**（规划失败 2、无可派发工作 1、根评审修复用尽 2）；真没做出来 4 局。
**新协议上线后重跑同一批题，主要看那 5 局"做对了却判失败"能不能变成"完成"。** 同一批题 Grok 的成绩是 14 局过 10 局、12 局判完成；两个模型的成绩分开记，不混算。

---

## 6. 三条干活通道的现状

| 通道 | 脚本 | 状态 |
|---|---|---|
| Codex 官方 | `backend/scripts/agent_lanes/codex_task.sh` | **不可用**：19:07 起登录令牌被吊销（401，提示需登出重登）。需用户在 ChatGPT 应用里重新登录，或跑 `codex login`。默认模型已钉成 `gpt-5.6-luna`，可用 `CODEX_TASK_MODEL` 覆盖 |
| DeepSeek 日卡 | `deepseek_task.sh` | 可用。用 codex 命令行当运行器，靠 `CODEX_EXTRA_CONFIG` 把地址指到本机排队闸口 28182；密钥运行时从 Host `.env` 读，不打印 |
| Grok | `grok_task.sh` | 可用。脚本已自动把 `~/.grok/bin` 加进 PATH。周额度有限，建议留给接线那件和正式验收 |

`slice_pipeline.sh` 三种通道都支持：`--lane / --verify-lane codex|grok|deepseek`。
**排队闸口** `daycard_gate.py` 当前仍在跑（2 个名额）。只用 Codex/Grok 时可以停掉。

---

## 7. 本轮踩过的坑（都已修，别再踩）

| 坑 | 说明 |
|---|---|
| 闸口注入打死官方通道 | cc-switch 接管配置后没有 `custom` 服务商，脚本自动注入地址会让 codex 整个配置加载失败，表现是**每一步 1 秒内失败**。已改成必须 `CODEX_USE_GATE=1` 显式开启 |
| 杀进程漏孙进程 | `codex exec` 是孙进程，只杀两层会留下孤儿继续干活并提交。停手后**务必**用 `ps -axo pid,command \| grep "Resources/codex exec"` 核对 |
| 变基后核验基准没跟着改 | 核验员拿旧基准比 diff，把主干上别的切片的改动算成本片越界，误判"不可合"。变基后**一定**要同步改核验任务书里的基准 |
| 带"续接实现"参数重启流水线 | 会**跳过实现**直接去核验。要让实现者返工，得直接调 `codex_task.sh`/`deepseek_task.sh`，返工完再跑流水线做核验 |
| 白名单开太窄 | 提示词改了必然带动冻结指纹表；这类连带改动要预先写进白名单，别让代理回退好代码 |
| 中文文件名的未提交报告 | `git ls-files --others` 会转义中文名导致漏拷。核验报告一律用**纯英文文件名** |
| 做题程序输出目录 | `run_h_arm.py --out` **必须传绝对路径**，否则评分子进程找不到交付物，整批记成评分出错 |
| 两个偶发失败的旧测试 | 高负载下 `step02/test_live_provider_progress` 与 `p35/test_context_cold_recovery` 各偶发超时一次，单独重跑即过。判定前先单独重跑 3 次 |

---

## 8. 合入主干的固定流程

```bash
SDK=/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-sdk
L=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/scripts/agent_lanes
T=tests/orchestrator/full_target

# 1) 把核验报告与检查报告归档到切片分支的 plans/llm-native-htn/H1/{reviews,gates}/
# 2) 密钥扫描（新增行里不得出现 sk-/xai-/Bearer/端点 IP）
git -C $SDK-<片> diff <基准>..HEAD | grep -E '^\+' | grep -cE '\bsk-[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,}|171\.80\.'
# 3) 合入
git -C $SDK merge --no-ff <分支> -m "merge(<片>): ..."
# 4) 在合并后的 main 上重跑完整检查，报告存档进 gates/
$L/sdk_gate.sh $SDK <合并前的 main> --tests "$T/<本片测试>" --full --max-sentinel 26 --out /tmp/gate.json
# 5) 推送、删工作树与分支
git -C $SDK push origin main
git -C $SDK worktree remove --force $SDK-<片>; git -C $SDK branch -D <分支>
```

---

## 9. 规矩（必须遵守）

1. **测试先行**：先写会失败的测试并提交，再写实现。
2. **独立核验**：实现与核验必须是**不同会话**，最好是**不同模型**；核验员只读不写、不 commit、不 push；必须做变异（每个变异前把原文件复制到 /tmp，变异后相关测试至少 1 条转红，再从副本恢复并确认工作树干净）。
3. **存活且行为可分辨的变异 = 测试缺口**，按必修处理；等价变异只备案。
4. **不许改** `contracts/` 下 v0.12.2 时就存在的文件（新增文件可以）；不许改变旧协议的任何行为与字节。
5. **不许**在共享工作树上 `git stash` / `checkout` / `reset`。
6. **密钥**：不打印、不提交 API key、token、`.env`、`llm_runtime*.json`、`~/.codex/auth.json`、`~/.grok/auth.json`。
7. **文档用中文**，代码与注释用英文。
8. **数字不许编**：测试尾行、git 输出一律原样粘贴。
9. 规格没覆盖的新问题 → 写 `plans/llm-native-htn/H1/BLOCKER-<片名>.md` 并**停在那一点**，交给计划作者裁定，不要自己改线上字段名或语义。
10. 核验多轮后若只剩"变异存活"类测试缺口、没有功能问题，登记进收尾的变异专项后可以先合，不要无限循环。

---

## 10. 本机还留着什么

| 东西 | 位置 / 状态 |
|---|---|
| 排队闸口 | `backend/scripts/agent_lanes/daycard_gate.py`，进程还在跑（2 个名额） |
| 工作树 | `…-v0122`（0.12.2 分离副本，做题程序钉版用，**别删**）、`…-p23d/p31/p32/p36/p51`（旧阶段遗留） |
| 代理进程 | 全部已按进程号停止，已核对无残留 |
| 做题程序 | `.local-test-evidence/2026-09-16/htn-acceptance/runner/`，用法见其 `RUNBOOK.zh-CN.md`；`JOURNAL.zh-CN.md` §33 记了 DeepSeek 通道接法 |

---

## 11. 2026-09-22 追加：测试节奏调整（必须遵守，优先于上面第 9 节的相应条目）

**起因（实测，数据来自 09-18/19 全部 38 个代理会话的逐条命令记录）**：代理干活共 31.3 小时，其中干等测试 7.3 小时；**代理自己启动全量回归 102 次、耗时 4.9 小时**，与流水线自动检查完全重复。核验轮数不封顶，请求包那件核验 7 轮，后几轮只挖出测试缺口、没有功能错误。每个切片都要求 8～12 条变异，而计划第 47 节只要求在**阶段验收门**做 12 条。测试行数是源码的 1.6 倍，记录行数是源码的 1.7 倍。

**已经落进工具的改动（不用再靠任务书记得）**：

1. `backend/scripts/agent_lanes/任务书通用规则.zh-CN.md`：`slice_pipeline.sh` 会把它**自动追加到每一份实施/核验任务书末尾**。要点：
   - 实施者只跑定向测试，**不跑全量回归、不跑旧模式回归**；日志只记改了什么、为什么、遗留什么。
   - 核验员：P0 才挡合并（功能错误、合同不符、安全、越界、改动旧协议）；变异存活记为 P1 测试缺口，列出但不单独挡合并；变异做 4～6 条；不跑全量回归。
2. `slice_pipeline.sh` 收口规则：核验**默认最多 2 轮**；第 2 轮仍是「修后可合」→ 跑完整检查，全绿则判 `PIPELINE GREEN-WITH-GAPS`，测试缺口清单写到流水线目录的 `gaps.md`，统一登记到阶段收尾的变异专项里补。「不可合」仍立刻 RED。
3. 全量回归只由自动检查跑两次：**合并前一次**（流水线最后一步）、**合并后在主干上一次**（编排者手动）。

**接线那件是例外**：它改的是全仓最危险的两个热文件，切片任务书应**明确要求**核验做 ≥12 条变异，并把新旧协议分流、旧协议字节不变、旧任务不得多发事件三处作为变异重点。通用规则写明了「切片任务书的明确要求优先」。

**阶段收尾时**：把各片 `gaps.md` 汇总成一张测试缺口清单，在变异专项里集中补，一次做满计划第 47 节要求的 12 条。
