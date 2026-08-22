# Phase-3 D 验证准备轨索引

状态：black-box 准备完成，等待实现轨汇合、oracle freeze 与 gate init；未启动 UI，未产生原始证据。

| 产物 | 用途 | 当前状态 |
|---|---|---|
| `../testcase/index.md` + 5 组 testcase | AC-1～AC-8 / TO-01～TO-R5 required black-box oracle | READY_TO_FREEZE |
| `scenario-matrix.json` | gate 场景、lane、root runs、input class、cold start | DRAFT_READY |
| `applicability.json` | input_sensitive / llm_payload_driven / stateful_init 三维事实 | DRAFT_READY |
| `impact-paths.md` | 影响路径策略 | INTENTIONALLY_EMPTY_FAIL_CLOSED |
| `environment-fixtures.md` | 隔离身份、数据、故障、Provider、冷路径与清理方案 | DRAFT_READY |
| `surface-and-value-smoke.md` | 价值优先输入与 critical/affected/full surface 清单 | DRAFT_READY |
| `baseline-shards.md` | 大仓回归分片引用与判定 | DRAFT_READY |
| `challenges/testcase-iteration-1.md` | breadth challenge | FAIL_RESOLVED |
| `challenges/testcase-iteration-2.md` | diff/open-obligation challenge | PASS |

## 汇合前仍需由主编排者完成

1. 实现轨 A/A2/B/C 完成，并由独立完成度审计确认无新增回归。
2. 根据实际公开 fault-control 入口落地环境准备脚本；不得改变本目录 oracle。
3. 生成 gate manifest 时复制场景矩阵与 applicability；impact_paths 无可靠映射则保持空并全量复测。
4. 对 testcase 逐文件计算 hash 并写入 `testcase_lock`；此后修改必须走批准的 behavior change。
5. 先运行价值 smoke 和便宜自动化门，再运行 SH-M1～SH-M6 与 SH-SURFACE 真 UI。

## 边界

- 不修改或测试 K6/AgentOS、AIPhone、NovelTagSystem。
- 不把跨设备同步、PostgreSQL backend、hostile host 纳入 required。
- 原始截图、日志、数据库、录屏仅进入 `.local-test-evidence`，不得提交 Git。
