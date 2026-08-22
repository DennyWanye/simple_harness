# 官方 Memory 一等集成：black-box testcase 索引

状态：已冻结并执行完成；最终 Gate 记录 21/21 required 场景 PASS。

唯一 oracle 是同目录上级的 `acceptance.md` 与 `assurance-contract.json`。本目录只描述公开入口、
输入、可观察结果与证据；不得用实现代码、私有 registry、直接数据库注入或协议直注代替用户链路。

## Required 测试集

| 文件 | Required cases | AC | TO / 风险 |
|---|---|---|---|
| `01-composition-recall.md` | SDK-C01～C03 | AC-1, AC-2, AC-7 | TO-01, TO-02 |
| `02-committed-turn-durability.md` | SDK-T01～T03 | AC-3, AC-4, AC-7 | TO-03, TO-R2 |
| `03-identity-privacy.md` | SDK-I01～I02 | AC-4, AC-7 | TO-04, TO-R2 |
| `04-storage-release.md` | SDK-S01～S03, SDK-M01, SDK-R01 | AC-5, AC-6, AC-7, AC-8 | TO-05, TO-08, TO-R3, TO-R4, TO-R5 |
| `05-simple-harness-ui.md` | SH-M1～SH-M6, SH-I01, SH-SURFACE | AC-4, AC-6, AC-7, AC-8 | TO-04, TO-06, TO-07, TO-R1, TO-R2, TO-R5 |

共 21 个 required case。每个 case 都直接证明 MUST AC 或阻断本次受影响风险；没有独立 AC/risk
绑定的扩展探索不进入 required 集。

## 执行纪律

- SDK/API/存储/发布用例通过 clean environment 的公开接口、CLI 或测试 fixture 执行。
- SH-M1～SH-M6 与 SH-SURFACE 必须从真实 simple_harness UI 进行模拟人工点击和输入。
- 每次 UI 动作前记录 `坐标=(x,y)|动作=...|期望=...`，动作前后截图，并关联脱敏日志/receipt。
- WebSocket 直注、后端脚本回放和读取内部 registry 不能作为 UI root evidence。
- 原始截图、日志、数据库、录屏仅写 `.local-test-evidence/2026-08-22/<run>/`；Git 只保存结论、
  scenario/run ID、相对索引与 SHA-256。
- 任何 required case 为 PENDING、PARTIAL 或 NOT_RUN 时，不能宣布完成。

## AC / TO 覆盖

| AC | 直接证明 cases |
|---|---|
| AC-1 | SDK-C01, SDK-R01 |
| AC-2 | SDK-C02, SDK-C03, SH-M1, SH-M3, SH-M5 |
| AC-3 | SDK-T01, SDK-T02, SDK-T03, SH-M2, SH-M6 |
| AC-4 | SDK-T03, SDK-I01, SDK-I02, SDK-R01 |
| AC-5 | SDK-S01, SDK-S02, SDK-S03 |
| AC-6 | SDK-M01, SH-M1～SH-M6, SH-SURFACE |
| AC-7 | SDK-C03, SDK-T01～T03, SDK-I01～I02, SDK-S01～S03, SDK-M01, SH-M4, SH-M6 |
| AC-8 | SDK-M01, SDK-R01, SH-SURFACE |

TO-01～TO-08 与 TO-R1～TO-R5 均至少由一个 required case 覆盖；TO-E1 保持 exploratory，明确不阻断。
