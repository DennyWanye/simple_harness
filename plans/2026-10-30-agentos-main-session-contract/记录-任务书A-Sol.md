# 任务书 A 执行记录

2026-10-30。分支 `sol/A-session-tree`；工作树 `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-A`。
基线 `47fd34f0da65667817d30f1994e73523b5aaf8ba`。先测试提交 `b2f220ce3`（25 项红灯），最后实现/被测提交 `db6fe469b3566cafd9c9625913654099a67f1c4c`；本页随后单独提交。不推送、不合并。

## 改了什么

沿用 records/events 表，加入配置版本 3、18 个合同错误码及安全公开错误、事务命令回执、单层父子关系与恢复、子委派路由、父结果镜像、子排队输入和本地能力发现。委派行/回执保留 `goal_ref=None`。父槽位冻结目标，重放切换 child/direct 不再重复建 Mission；父关闭等待在途子打开与已打开子关闭。文档新增“子会话”并更正调用槽位派生键。

验证覆盖：I1 身份/版本；I2 深度/根别名/伪造父/跨当事人/根不能被收养；I3 重开查询命令；I5 重放/冲突/不重复建任务；I6 结果只作资料、父确认；`child_registered`、子/父 `inbox_written`、子委派登记后故障恢复；两子各一个在途、第三次 busy、父独立委派；真宿主重开保留子历史；模型命令创建→追加→查询→委派。

## 测试命令与结果

以下均在 `sdk/simple-harness-sdk/`，只跑任务书指定测试文件，未跑全量回归。

| 命令 | 结果 |
|---|---|
| `uv run --frozen python -m pytest tests/orchestrator/host/test_session_tree.py -q`（直接工作树） | 39 通过、1 失败：受限的 TaskGraph 源清单未包含新文件/源码哈希 |
| `uv run --frozen python -m pytest tests/orchestrator/host/test_session.py tests/orchestrator/host/test_session_replay.py tests/orchestrator/host/test_host_sessions.py tests/orchestrator/host/test_session_tree.py -q`（下述候选环境） | 116 通过（226.76 秒） |
| `uv run --frozen python -m pytest tests/test_public_api.py -q`（直接工作树） | 105 通过 |
| `uv run --frozen ruff check src tests` | 失败：1766 条；与基线逐条相同，新增 0、减少 0 |
| `uv run --frozen ruff format --check` | 失败：50 个既有文件；基线同样 50 个（仅 src/tests 为 18 个） |
| 对本次 12 个 Python 文件执行 Ruff / format check | 全通过 / 12 个均已格式化 |

候选环境：保留 tracked `taskgraph_deployment_manifest.json` 原样；将被测 HEAD 源码复制到本工作树 `.local-test-evidence/2026-10-30/session-tree/final-candidate/`，使用仓库官方 `scripts/build/taskgraph_manifest.py` 的 `generate` 和现存真实上游证据生成 **NOT_RUN** 清单，不伪造 VALIDATED。849 个运行源文件与工作树逐字节相同（仅候选生成清单不同）；设置 `PYTHONPATH=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-A/.local-test-evidence/2026-10-30/session-tree/final-candidate/src` 后执行表中四文件命令。这验证相同业务源码，不能声称原工作树直接全绿或已通过完整 TaskGraph 发版门。

复跑/证据均在上述 ignored `session-tree/`：`prepare_candidate.py`（重建合法候选）、`source-identity.json`（被测 HEAD 与逐文件 SHA-256）、`final-session-tests.txt`、`direct-tree-tests.txt`、`baseline-ruff.json`、`final-ruff.json`、`lint-comparison.json`、`baseline-format.txt`、`final-format.txt`、`baseline-format-all.txt`、`final-format-all.txt`、`review.md`。原始证据不入 Git。证据索引 `evidence-index.json` 的 SHA-256：`49ed6ca8b32e3ccb48c71c9894f36c1ed27b3da376ac3d4bc52e6fb8ed59c639`。独立 review 对被测 HEAD 无未关闭 finding，曾发现的恢复顺序、委派换目标重复创建及关闭竞态均已配反例修复。

## 没做的

未做 Goal、自动汇总/续办、授权回调、运行中 steer、排队输入执行、迁移旧库、模型真联网验收、发版；这些不属于 A。未修改车道外架构、任务层、runtime、host 或受限部署清单；主工作区草稿未改动。

## 拿不准的

- §3/§8 禁改 `session_chat.py` 分发，§4.9 又要求新增分发：按严格车道在允许的 `session.py` 内用 `TreeSessionChatService` 扩展 `_command`。同步网关先持久登记关系/子账本，父 send 返回前和恢复泵再经 `sessions.open_session` 异步打开；恢复旧模型回合前先恢复子句柄。`session_chat.py` 仅动 schema、追加提示词、指定池串；已用 AST 核对。
- 部署清单及全库既有 lint/format 问题均在车道外，按 §9 继续完成允许范围并如实保留失败；没有扩大修改范围。架构事实源也遵守本任务车道，交审核人合并时统一更新。
- 自我复盘：真实宿主/重放边界测试和独立审查有效发现问题；首轮编辑态试跑不作最终证据，最终以提交态与等字节候选复验为准。

VERDICT: BLOCKED — A 功能实现及定向候选验证已完成；第 7 节要求的工作树直接全绿、全库 Ruff/format 尚未达到，原因及证据如上 — 2026-10-30 — db6fe469b3566cafd9c9625913654099a67f1c4c
