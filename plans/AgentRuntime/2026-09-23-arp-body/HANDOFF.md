# ARP-EXEC-1.1.1 主体施工交接（arp-1.1.1 分支）

最后更新：2026-09-23（RP-B 完成时）。

## 当前状态

| 切片 | 状态 | 提交 | 记录 |
|---|---|---|---|
| 盘点 | 完成 | `0c6e853f` | `00-父源盘点.md` |
| RP-A 合同 / 规则 / v11 迁移 / Store / 创建链 / 工厂 | 完成 | `7211546d` | `00-父源盘点.md` §5 |
| RP-B Context 装填 / 计量 / 分区索引 / 检索 / 召回 | 完成（本片） | 见 git log | `01-RP-B-上下文计量索引召回实施记录.md` |
| RP-C 统一目录 / Skill / Tool | 未开始 | | |
| RP-D 生命周期 / Host verbs / GC / retention | 未开始 | | |
| RP-E 统一验收 + Assurance 接入（BW09） | 未开始，等 Assurance 线完成 | | |

## 怎么继续

1. 进 worktree：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-arp`，SDK 在 `sdk/simple-harness-sdk`，跑测试用 `uv run --frozen python -m pytest tests/agents/arp -q`（应 256 passed）。
2. 主仓库 `simple_harness` 上有他人未提交的 Assurance 文件，不要碰；本分支不向公开仓库推送。
3. 下一片先做 `01-…实施记录.md` §5 的前三项（模型侧检索工具接线、tick 卸载 embedding、delegate 走创建服务），再开 RP-C。
4. 每片：测试先行 → 定向测试 → legacy `tests/agents` 与 `tests/execution` 各跑一次对比基线 → 独立核验（子代理用 opus 5.5，禁 fable；≤2 轮，只报阻断级）→ 中文记录 → 提交 → 更新本文件。
5. 交付时更新 `ARCHITECTURE/`（AGENT_HARNESS / PROJECT_STATUS）并重生成 `scripts/verify_development_handoff.py` 的 SDK 清单。

## 已知基线失败（非本分支引入）

- `tests/agents`：16 项因本机缺 `tiktoken`。
- `tests/execution`：以 `4a4e07fd` 同环境结果为准（见 RP-B 记录 §3.1）。
