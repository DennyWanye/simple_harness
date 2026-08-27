# Testcase challenger — round 1

## AC 覆盖审查

- AC-SI-1→TC-SI-01/03；AC-SI-2→TC-SI-02；AC-SI-3→TC-SI-01/03；AC-SI-4→TC-SI-04；AC-SI-5→TC-SI-01/05；AC-SI-6→TC-SI-05。
- 5 条 required testcase 均绑定 delivery AC；其中 4 条同时绑定 change-risk，没有无目标用例。

## 既有资产与复用决策

- 已读取 `testcase/index.md`、`index.json`、reuse report 与候选原文。
- 三条 Capability/Skills UI 候选为 needs-review，Project 候选虽 active 但没有 Skill install 入口；逐 obligation 采用 create-new 并写明增量价值，未继承历史 PASS。
- selected TC-SI 均 active revision 1，无 replacement 链。

## 缺失的必要测试

- [AC-SI-4] TC-SI-04 初稿虽列恶意 archive 类别，却没有冻结 fixture 的唯一畸变、stable code、exact commit 与零副作用 oracle，执行者可能用任意本地 archive 冒充真实入口。
- [AC-SI-6] 需要可机器 fail-closed 检查 full-surface smoke 是否漏入口，否则文字 checklist 可能被部分执行后宣称全绿。

## 应删除或降级的测试

- 无。5 条分别对应 SI-M1～M5 的唯一业务链，负向/重启没有与正向重复。

## 步骤/预期不清的用例

- TC-SI-04：补 malicious fixture 表和逐 phase fault matrix。
- TC-SI-05：补声明范围内所有 surface 的固定 result schema/checker。

## 建议新增的 required testcase

- 不新增 testcase；上述缺口应补入 TC-SI-04/05 的 supporting assets，避免膨胀。

## 输入广度盘点

- distinct 类别 5 个：中文 multi、混合语言 single、拒绝、恶意/故障、冷启动 Settings/回归。
- retry/replay/continuation 只证明可靠性，不计新类别。
- required 仍 PENDING：全部（当前是 oracle 准备阶段，未执行，不能继承历史 PASS）。
- 标准术语、自然中文、中英混合与无关键词口语由 required matrix + smoke inputs 覆盖；排名/推荐/优缺点与安装意图无关，不应 required。

## 最小充分性评估

- 当前 required：5；直接证明 AC：5；同时防 change-risk：4；建议删除：0；最终：5。

## 结论

- AC 映射完整，但 supporting fixture 与 full-surface fail-closed verifier 尚缺，当前不可冻结。
- 本轮执行引擎为 current；配置 challenger=claude 因并发槽已占满未能启动独立代理，需在汇合闸披露该 advisory。

VERDICT: FAIL

