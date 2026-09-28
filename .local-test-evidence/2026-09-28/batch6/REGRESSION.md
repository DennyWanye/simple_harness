# NEXT-TG-1.0 第六批全量回归（2026-09-28，推送前一次）

制品：SDK `0.13.0.dev20260925+opt.60`（源提交 19e549f2），Host d1630b81 起；opt.61（da122cb6，收尾判定抽函数 + 限定审阅意图）后 full_target 再跑一次：47 失败 / 4398 通过，**无新增**（见 `artifact-identity.txt`：已安装包与内嵌源码 733 个文件逐字节一致）。
脚本：`run_full_regression.py`（SDK 与 Host 按目录分进程、每份 30 分钟硬时限；前端整套）。日志与逐目录汇总：`full-1/{sdk,host,frontend}/`。

比对方法：
- SDK `tests/orchestrator`（按子目录）与 `gap_phase1`、Host `tests/orchestration`：与改动前基线名单（`2026-09-27/baseline/`，基线提交 4a1678ae + 本机任务过程视图）逐条比对。
- 其余目录没有改动前名单：把本次每条失败在**基线提交 4a1678ae 的独立工作树**（SDK opt.32、同一台机器、Python 3.12 环境）上逐条重跑。

| 范围 | 本次结果 | 与改动前比 |
|---|---|---|
| SDK 编排 full_target（opt.60 重跑） | 47 失败 / 4397 通过 / 1 收集错误 | **无新增**；比基线少 14 个失败 |
| SDK 编排其余子目录（host_support、lc2、p32–p36、step02–09、根目录） | p32 7、p33 3、p35 8、step08 1、step09 2 失败，其余全过 | **无新增**；p33 比基线少 4 个 |
| SDK gap_phase1 | 1 失败 / 299 通过 | 同基线（同一条） |
| SDK agents（含 ARP）、conformance、providers、runtime、workflow | 全过（agents 588、conformance 300、providers 86、runtime 116、workflow 33） | — |
| SDK artifact、execution、integration、unit | 8 + 6 + 68 + 7 条失败/错误 | 在基线工作树上**同样失败**（冻结的版本号、库结构版本、锁文件哈希、公共接口快照等过期期望） |
| Host `tests/orchestration` | 26 失败 / 442 通过 | **与基线 26 条逐条相同** |
| Host 其余目录与根目录测试（24 份） | 共 137 条失败/错误 | 在基线工作树上**同样失败** |
| 前端 vitest | 112 个文件 937 条全过 | 基线 958 条全过；条数减少来自第三批重写/删除的执行图测试 |
| 前端类型检查 | 通过 | — |

结论：**没有本次引入的失败**。既有失败保持原样，未在本轮修复（与本计划无关）。
