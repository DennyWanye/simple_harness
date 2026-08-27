# Testcase challenger — round 2

## AC 覆盖审查

- 复核 AC-SI-1～6 均至少由一条 required TC-SI 用例直接证明；没有无 AC/risk 绑定的 required testcase。
- Round 1 后只审新增 `malicious-fixture-spec.md`、`full-surface-smoke.md`、smoke inputs 与 checker；没有新增 required obligation。

## 既有资产与复用决策

- inventory/reuse report 未改变；5 条 selected testcase 为 active revision 1，逐 obligation create-new 理由可验证。
- 未继承 legacy 或 active Project testcase 的历史执行结果。

## 缺失的必要测试

- 无。恶意 source/archive、batch phase fault、拒绝/replay、三域隔离、runtime exact hash、冷启动与 Settings/历史入口均在 required 主链。

## 应删除或降级的测试

- 无。`SMOKE-SI-NATURAL-NO-KEYWORD` 是 AC-SI-1/ROUTE-BYPASS 的输入泛化，不是无关探索；排名/推荐类表达未错误加入。

## 步骤/预期不清的用例

- 无。每步含可判定预期、零副作用/业务终态和 primary evidence 合约。

## 建议新增的 required testcase

- 无；继续增加 case 只会重复现有 AC/risk。未配置真实 malicious GitHub fixture 是执行环境阻塞，不应通过放宽 oracle 或新增重复 case 处理。

## 输入广度盘点

- distinct 类别 5 个：multi、single、deny、malicious/fault、cold/settings；重放与长上下文不冒充新类别。
- required 仍 PENDING：5 条均待当前候选执行，状态正确；没有伪 PASS。
- 2 个正向自然语言安装样本 + 1 个冷启动正向链达到配置下限；2 次独立完整 root 与 ≥10 轮长上下文要求已冻结在 TC-SI-01/spec。

## 最小充分性评估

- 当前 required：5；直接证明 AC：5；同时防 change-risk：4；删除/降级：0；最终：5。

## 结论

- 5 条 testcase 是 SI-M1～M5 的最小充分集合，所有 MUST AC 与 required risk obligation 完整双向覆盖。
- Round 1 两个 supporting-asset finding 已关闭；未新增 required obligation。

VERDICT: PASS

