# Testcase challenger — round 1

## AC 覆盖审查

- AC-1→TC-PS-01；AC-2→TC-PS-02；AC-3→TC-PS-03；AC-4→TC-PS-04。
- AC-5→TC-PS-05；AC-6→TC-PS-06；AC-7→TC-PS-07；AC-8→TC-PS-08。
- 没有未绑定 AC/risk 的 required testcase。

## 既有资产与复用决策

- 已读取 inventory、机器索引、reuse report 与六份最接近候选全文。
- 13 条义务均无 active 候选；相关 legacy 资产是 `needs-review`，且其入口/oracle 分别只覆盖扁平侧栏、删除、userdata 重启或 SDK Context，不足以证明 Project Binding。
- 每条 obligation 均记录 `create-new` 与增量理由；没有继承历史 PASS。

## 缺失的必要测试

- 无。Windows identity、lost ACK、projectless fail-closed、authority 冲突、分页并发、root 分离、missing/relocate 与迁移中断均进入对应 required 主链。

## 应删除或降级的测试

- 无。8 条用例各直接证明一条 MUST AC；5 条风险义务合并进同一交付链，没有为风险另造重复 case。

## 步骤/预期不清的用例

- 无。每个动作均有可判定的即时结果、通过条件与 primary evidence 类型。

## 输入广度盘点

- `input_sensitive=false`，不要求自然语言语义类别；表中的 input_class 仅说明确定性入口。

## 最小充分性评估

- 当前 required 测试总数：8。
- 其中直接证明 AC 的：8。
- 其中同时防范受影响风险的：5。
- 建议删除/降级的：0。
- 最终 required 测试数：8。

## 结论

- 当前集合以每条 MUST AC 一条行为主链为最小骨架，把相关风险并入主链，没有重复证明。
- 所有 MUST AC 与 required risk obligation 均有覆盖。

VERDICT: PASS
