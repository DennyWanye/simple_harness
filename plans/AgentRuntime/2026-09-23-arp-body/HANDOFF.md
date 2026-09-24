# ARP-EXEC-1.1.1 主体施工交接（arp-1.1.1 分支）

最后更新：2026-09-24（RP-E3 完成时；已合并 origin/main bd0f6d59 = Assurance 1.1 完成）。

## 当前状态

| 切片 | 状态 | 提交 | 记录 |
|---|---|---|---|
| 盘点 | 完成 | `0c6e853f` | `00-父源盘点.md` |
| RP-A 合同 / 规则 / v11 迁移 / Store / 创建链 / 工厂 | 完成 | `7211546d` | `00-父源盘点.md` §5 |
| RP-B Context 装填 / 计量 / 分区索引 / 检索 / 召回 | 完成 | `35a4b67a` | `01-RP-B-上下文计量索引召回实施记录.md` |
| RP-B 收尾：模型侧检索工具 / tick / 委派走创建服务 | 完成 | `325d2606` | `02-RP-B收尾-检索工具-tick-委派实施记录.md` |
| RP-C1 统一目录 / 能力解析 / 工具曝光 | 完成 | `534f2ab0` | `03-RP-C1-统一目录-能力解析-工具曝光实施记录.md` |
| RP-C2 Skill 包导入 / 依赖锁 | 完成 | `77f2f1f0` | `04-RP-C2-Skill包导入-依赖锁实施记录.md` |
| RP-C3 试用 / 准入 / load / execute | 完成 | `b91d3bda` | `05-RP-C3-试用准入-load-execute实施记录.md` |
| RP-D1 会话销毁 / 处置证明 / 清理 / 同身份恢复 / 重建门 / 保留许可 | 完成 | `0dcb7bd5` | `06-RP-D1-会话销毁-清理-恢复实施记录.md` |
| RP-D2 Host verbs typed 服务（`api/runtime_plane.py`，23 个动词 + 信封 + 命令回执） | 完成 | `20cfdccc` | `07-RP-D2-Host动词typed服务实施记录.md` |
| RP-E1 Assurance 接入（BW09：真实 `SkillAcceptancePort`）+ 87 条验收场景对账 + 5 条补缺测试 | 完成 | `717dbeec` | `08-RP-E1-Assurance接入与验收对账实施记录.md` |
| RP-E2 状态机随机测试小号版（6 动作 / 5 种子 × 400 步）+ 同键创建复活缺陷修复 | 完成 | `adeca07f` | `09-RP-E2-状态机随机测试实施记录.md` |
| RP-E3 接线：编排装配层 native 分支（`build_arp_runtime` + 真实授权 + 意图派生调用方）、DeepSeek EXACT 计量认证 + 先前输出储备读取器、Host 五端口（根/计量/验收读口/产物/嵌入；脚本执行器暂无）+ 原生池并列默认 ON + 控制通道 `agent_runtime_request` / 评估 Mission 两动词 + SDK 钉版 `0.13.0.dev20260924+arp.1` | 完成 | SDK `37c3a3ac`、`7308e50b`；Host `b510a60d` | `10-RP-E3-原生平面接线实施记录.md`（§5 六条遗留） |
| RP-E4 真实模型 12 局 + 安装后导入核对 + 真机路径 | 进行中（被环境阻塞） | SDK `78eb1e0e`（独立部署交接前冻结 + 读取器只读映射 + 清点跳过 claimed）、`195e166d`（arp.2）；Host `dea1da70`（钉 arp.2）；运行器 `99cb4aa5`、`535a986c`；Host 金丝雀 4（arp.2）原生池计量与先前储备已验证（§7.6） | `10-RP-E3-原生平面接线实施记录.md` §6–§7 |
| RP-E4 12 局：修复后长上下文重跑 2/3，四类各 2/3、合计 8/12（第 1 轮 4 局网关空完成保留为失败），硬不变量全过。已修：固定部分按精确计数器计费（SDK `96efe456`）、运行器 24 次截停。后续：历史搜索进度页让模型误判为空；Host 需重钉 arp.3；待审：先前输出储备随会话单调累积 | 进行中 | SDK `96efe456` | §7.7–§7.9 |
| **2026-09-24 12:00 起换新 DeepSeek 线路 + 计量口径纠正**：DeepSeek 认证改 WIRE_ONLY、去掉先前输出储备（对抗审阅裁定原设计无依据）；记账「可以多算不可以少算」：0 用量=未结算、先前未结算按上限计不再冻结、已结束调用不占槽（共享侧表 `provider_grant_wire_terminal_v1`）、收尾导入记未知；流式占位块/全零用量处理；适配器 `extra_body`（关思考）与 `response_model_aliases`；运行器加用量校对。受影响套件 1781 通过、17 失败=基线。opus 两轮核验阻断全部处理。新线路长上下文 3 局：修流式后 2/3，声明别名后的重跑因线路积分耗尽无结果，待充值重跑。Host 已重钉 arp.3 | 进行中（等线路积分） | SDK+Host `5c37bab2`，Host 钉版见后一提交 | §7.12 |
| **2026-09-24 下午：思考/不思考双模式 + 中转余量** — 适配器思考开关、私有思考逐条回传、计数按模式精确（arp.4）；新 Mission 默认开思考，产品后台独立思考池；中转带工具多留余量（下限 160、按回报用量学习、只增不减），旧身份池沿用旧计数器，候选估算器按冻结准入身份挑选（arp.5–7）。中转长上下文金丝雀 1/1、完整 3 局 3/3 通过，59 次用量校对无少算。开思考需同时发强度 high（中转只在带强度时思考）；真实思考线路开思考 3 局：2/3 答对（1 局提问回合线路错误），硬性检查 3/3 含思考回传，无少算 | 进行中 | SDK `925c694a` / Host `a20a393f` | §7.13–§7.14 |
| **2026-09-24 晚：向量模型本地可用** — 用户选 BGE-M3 INT8（本机量化、外部数据格式，常驻约 1.07 GB），Host 端口改用 onnxruntime + tokenizers，不再依赖 torch/FlagEmbedding；SDK 支持嵌入端口声明后台计算，准备阶段不再同步跑真实模型（arp.8） | 完成 | SDK `5755aa34` / Host 钉 arp.8 | §7.16 |
| **2026-09-24 晚：遗留缺陷清理** — SCRIPT 技能在 Host 沙箱执行（异步等待，不阻塞事件循环）；创建键按所有者区分；技能暂停/恢复/退役崩溃重试按重放处理；转发器积分不足的 429 不再重试占槽（SDK arp.9） | 完成 | SDK `d5d6f5ba` / Host 钉 arp.9 | §7.17 |
| **2026-09-24 晚：审阅阻断 + Assurance 检查策略 + 不稳定测试** — 沙箱脚本输出不再跟随链接（审阅阻断）；任务派生判据去掉 `critic_review` 后检查策略可投影（金丝雀 4 卡点）；召回分页崩溃测试去竞态（SDK arp.10） | 完成 | SDK `75a916dc` / Host `afacd26b` + 钉 arp.10 | §7.18 |
| **2026-09-24 晚：最终代码真实模型 12 局** — 开思考 12/12 答对、硬性检查 12/12，校对 119 次少算 0；关思考对照 3/3（少算 0） | 完成 | SDK arp.10 | §7.19 |
| **2026-09-24 夜：产品后台真实链路跑通** — 原生思考池 + 本地向量 + 保证审阅，七趟修 7 个真缺陷（SDK arp.11–arp.16），第七趟 Mission 完成、记账无少算；用户决定：文档任务 code_test 无可证明内容不计入、审阅格式不放宽 | 完成 | SDK `26a0a2bc` / Host `1759a5b2` | §7.20 |
| **2026-09-24 夜：合并前全量回归** — Host 159 失败（153 原有、6 新增已修）；SDK 后半 10 失败全原有；SDK 前半与合并基点同条件对照 163=163、新增 0；全量抓到 1 个真缺陷（准入被拒调用被记成用量未知）已修 | 完成 | SDK `ca79a7ce`(arp.17) / Host `ea6d06e9` | §7.21 |

## 怎么继续

-1. RP-E4 续跑口令：网关问题有决定后，`cd sdk/simple-harness-sdk && uv run --frozen python ../../plans/AgentRuntime/2026-09-23-arp-body/tools/arp_real_games.py ../../.local-test-evidence/2026-09-24/arp-acceptance/games --rounds 1`（先一轮金丝雀，不与 Host 金丝雀并跑），通过后 `--round-from 2 --rounds 3`；离线机制回放脚本 `lm02_offline.py` 在会话暂存区（脚本提供方 + 崩溃窗口，30 s）。Host 金丝雀：`cd backend && PYTHONPATH=. .venv/bin/python ../plans/AgentRuntime/2026-09-23-arp-body/tools/host_native_real_run.py <证据目录>`。

0. Host 侧：本 worktree 的 `backend/.venv` 已用 `uv sync --frozen --extra dev --python 3.12` 建好（钉 `0.13.0.dev20260924+arp.2`）；Host 定向测试 `cd backend && .venv/bin/python -m pytest tests/orchestration/test_native_plane_host.py -q`。改 SDK 后按 HANDOFF-2026-09-23.md §6 重钉（升两处 version.py → 提交 → `scripts/build/development_candidate.py --output ../../backend/vendor` → sdk_candidate/pyproject/uv lock → `uv sync --frozen --extra dev`）。
1. 进 worktree：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-arp`，SDK 在 `sdk/simple-harness-sdk`，跑测试用 `uv run --frozen python -m pytest tests/agents/arp -q`（应 353 passed（RP-E4 后；RP-E3 时 352））；SDK venv 需 `uv sync --extra skill-import --extra testing`。
2. 主仓库 `simple_harness` 上有他人未提交的 Assurance 文件，不要碰；本分支不向公开仓库推送。
3. 下一片 RP-E：统一验收（对照规格 `implementation/sdk-cases.json` / TEST-PLAN 的用例清单，把 01–07 记录 §5 的遗留项排入）+ Assurance 接入（BW09：`SkillAcceptancePort` 由 Assurance 后继实现替换 `PendingAssuranceAcceptance`）。Host 侧接线入口：`RuntimePlaneService(runtime).handle(HostRequest, caller)`（`simple_harness.api`），Host 需提供 `ArpPorts.artifacts`（认证 artifact 读口）与 `ArpPorts.script_runner`。
4. 每片：测试先行 → 定向测试 → legacy `tests/agents` 与 `tests/execution` 各跑一次对比基线 → 独立核验（子代理用 opus 5.5，禁 fable；≤2 轮，只报阻断级）→ 中文记录 → 提交 → 更新本文件。
5. 交付时更新 `ARCHITECTURE/`（AGENT_HARNESS / PROJECT_STATUS）并重生成 `scripts/verify_development_handoff.py` 的 SDK 清单。

## 已知基线失败（非本分支引入）

- Host `tests/sdk_adapters`：34 项（effect gate / hardening / objective events / S5a 验收矩阵 / task_scope_update 等旧适配链）在 arp.7 与 arp.8 上逐条相同地失败（2026-09-24 核对），非本分支近期改动引入。

- `tests/agents`：16 项因本机缺 `tiktoken`（2026-09-24 已在 SDK venv 补装）。
- SDK `tests/orchestrator/full_target/assurance_exec/test_a_assurance.py` 2 项：外部脚本导入 `jsonschema` 失败（SDK venv 缺包）。
- `tests/execution`：以 `4a4e07fd` 同环境结果为准（见 RP-B 记录 §3.1）。
