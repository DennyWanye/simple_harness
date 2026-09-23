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
| RP-E4 阻塞点：日卡网关对部分提示稳定返回空完成（无内容无 usage）→ SDK 定性失败 → 该 Agent 后续按名拒绝（先前用量未解决）；本机无官方 DeepSeek 密钥，无法回退 | 等用户决定 | | §7.3 |

## 怎么继续

-1. RP-E4 续跑口令：网关问题有决定后，`cd sdk/simple-harness-sdk && uv run --frozen python ../../plans/AgentRuntime/2026-09-23-arp-body/tools/arp_real_games.py ../../.local-test-evidence/2026-09-24/arp-acceptance/games --rounds 1`（先一轮金丝雀，不与 Host 金丝雀并跑），通过后 `--round-from 2 --rounds 3`；离线机制回放脚本 `lm02_offline.py` 在会话暂存区（脚本提供方 + 崩溃窗口，30 s）。Host 金丝雀：`cd backend && PYTHONPATH=. .venv/bin/python ../plans/AgentRuntime/2026-09-23-arp-body/tools/host_native_real_run.py <证据目录>`。

0. Host 侧：本 worktree 的 `backend/.venv` 已用 `uv sync --frozen --extra dev --python 3.12` 建好（钉 `0.13.0.dev20260924+arp.2`）；Host 定向测试 `cd backend && .venv/bin/python -m pytest tests/orchestration/test_native_plane_host.py -q`。改 SDK 后按 HANDOFF-2026-09-23.md §6 重钉（升两处 version.py → 提交 → `scripts/build/development_candidate.py --output ../../backend/vendor` → sdk_candidate/pyproject/uv lock → `uv sync --frozen --extra dev`）。
1. 进 worktree：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-arp`，SDK 在 `sdk/simple-harness-sdk`，跑测试用 `uv run --frozen python -m pytest tests/agents/arp -q`（应 353 passed（RP-E4 后；RP-E3 时 352））；SDK venv 需 `uv sync --extra skill-import --extra testing`。
2. 主仓库 `simple_harness` 上有他人未提交的 Assurance 文件，不要碰；本分支不向公开仓库推送。
3. 下一片 RP-E：统一验收（对照规格 `implementation/sdk-cases.json` / TEST-PLAN 的用例清单，把 01–07 记录 §5 的遗留项排入）+ Assurance 接入（BW09：`SkillAcceptancePort` 由 Assurance 后继实现替换 `PendingAssuranceAcceptance`）。Host 侧接线入口：`RuntimePlaneService(runtime).handle(HostRequest, caller)`（`simple_harness.api`），Host 需提供 `ArpPorts.artifacts`（认证 artifact 读口）与 `ArpPorts.script_runner`。
4. 每片：测试先行 → 定向测试 → legacy `tests/agents` 与 `tests/execution` 各跑一次对比基线 → 独立核验（子代理用 opus 5.5，禁 fable；≤2 轮，只报阻断级）→ 中文记录 → 提交 → 更新本文件。
5. 交付时更新 `ARCHITECTURE/`（AGENT_HARNESS / PROJECT_STATUS）并重生成 `scripts/verify_development_handoff.py` 的 SDK 清单。

## 已知基线失败（非本分支引入）

- `tests/agents`：16 项因本机缺 `tiktoken`。
- `tests/execution`：以 `4a4e07fd` 同环境结果为准（见 RP-B 记录 §3.1）。
