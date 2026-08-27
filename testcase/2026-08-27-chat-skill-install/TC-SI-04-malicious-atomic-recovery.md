---
id: TC-SI-04
purpose: Verify malicious repositories and every batch publish fault leave all-old or all-new state with structured recovery
status: active
surface: integration
type: hybrid
obligations: [TO-SI-4]
tags: [skill-install, malicious, atomicity, recovery, idempotency]
entrypoint: typed installer and bound Project chat
revision: 1
---

# TC-SI-04 — 恶意仓库、原子性与恢复

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 对 fixture matrix 的每个 invalid archive/source 独立 stage。 | HTTPS/GitHub/redirect/size/count/path/mode/manifest/tool 校验按规范拒绝；不显示可批准的伪清单。 |
| 2 | 用含 3 个合法成员的 batch，在每个 member prepare 与 publish phase 注入一次独立故障。 | 每轮终态为 full-old、full-new 或 fenced unknown；从不出现部分成员可见且整体 success。 |
| 3 | 在故障后完整重启并用相同 operation 重试。 | recovery 幂等收敛；full-old 清理未提交 staging，full-new 返回同一 receipt，unverifiable 保持 fenced 并给恢复出口。 |
| 4 | 注入网络超时、404/429/5xx、确认过期、catalog publish/runtime verification 失败。 | 返回 stable structured code/correlation/retryability；不得记录 token/cookie/repo body；已发布但未验证只显示 pending/failed，不显示 success。 |
| 5 | 重复成功请求、失败请求和 lost-ACK 请求。 | 同 Project + content digest 只产生一个 committed operation/receipt/binding set；失败重试不累计副作用。 |

## 通过条件

- 每个 fixture/fault 都有目录、binding、operation、receipt、catalog 的前后计数；所有断言 all-old/all-new。
- UI 场景 SI-M4 必须使用真实聊天输入；fixture 注入只制造仓库/transport/故障，不得注入模型答案或 UI terminal。

