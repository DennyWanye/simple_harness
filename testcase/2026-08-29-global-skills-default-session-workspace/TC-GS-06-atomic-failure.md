---
id: TC-GS-06
purpose: Verify workspace and global Skill failures expose no half state and retry converges safely
status: active
surface: integration
type: scripted
obligations: [TO-A6, TO-R4, HM-TO-R2]
tags: [fault-injection, atomicity, recovery, malicious-skill]
entrypoint: supported test fault seam
revision: 2
---

# TC-GS-06 — 原子失败与恢复

## 步骤与预期

固定输入、fault token、前后快照与 identity 规则以 `verification/fault-matrix.json` 为唯一来源；每 fault 使用独立 run-id 调用其 `runner_command`，不得临场选择 seam。

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 独立运行 malicious source/archive/manifest fixture。 | 稳定拒绝且无可批准伪清单、无 publish、上一 catalog hash 不变。 |
| 2 | 分别在 fetch、validate、publish-intent、materialize、catalog-swap、commit-before-ACK、runtime verify 注入一次故障。 | 每 lane 只能是 full-old、full-new 或 fenced-unknown；从不出现 mixed visible Skill。 |
| 3 | 分别在 TaskScope mkdir、binding-set persist、receipt persist 注入一次故障。 | 不出现可使用的半绑定 TaskScope；主对话、其他 TaskScope 与用户文件不变。 |
| 4 | 每 lane 冷重启并重放相同 operation/request。 | recovery 幂等收敛；成功重放返回稳定 receipt，失败 staging/owned 空目录安全回收。 |

## 决定性证据

- 每 lane 的 DB/catalog/tree 前后 hash、operation terminal、retry result 与 fenced recovery 出口。
- `fenced-unknown` 必须对普通 Run 不可见并提供可审计恢复出口；runner 的 durable gate root-attempt ID 作为 `root_run_id`，不代表创建了 Provider Run。
