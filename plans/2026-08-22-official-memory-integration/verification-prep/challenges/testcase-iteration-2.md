## AC 覆盖审查

- AC-1：SDK-C01、SDK-R01。
- AC-2：SDK-C02、SDK-C03、SH-M1、SH-M3、SH-M5。
- AC-3：SDK-T01、SDK-T02、SDK-T03、SH-M2、SH-M6。
- AC-4：SDK-T03、SDK-I01、SDK-I02、SDK-R01。
- AC-5：SDK-S01、SDK-S02、SDK-S03。
- AC-6：SDK-M01、SH-M1～SH-M6、SH-SURFACE。
- AC-7：SDK-C03、SDK-T01～T03、SDK-I01～I02、SDK-S01～S03、SDK-M01、SH-M4、SH-M6。
- AC-8：SDK-M01、SDK-R01、SH-SURFACE。
- 无未绑定 AC/risk 的 required testcase。

## 缺失的必要测试（仅列出直接证明 AC 或防范受影响范围内风险的测试）

- 无。第一轮两个 obligation 均已闭环：SDK-M01 覆盖协调升级/回滚；SH-M1 required lanes 覆盖口语、
  中英混合和无关键词表达。

## 应删除或降级的测试（无目标绑定或超出受影响范围）

- 无。SDK-S01/S02/S03 分别证明规模并发、embedding lineage、backup/corruption，不是重复测试。
- TO-E1 远程 Memory 保持 exploratory，不阻断。

## 步骤/预期不清的用例

- 无。每个步骤都有外部输入与可证伪预期；实现细节已改写为公开 receipt/export/hash/启动行为。

## 建议新增的 required testcase（必须说明绑定哪个 AC 或防范哪个受影响范围内的风险）

- 无新增 required obligation。未来 Remote backend、跨设备同步只能列 exploratory。

## 输入广度盘点（仅当 input_sensitive=true 时必填）

- distinct 输入类别：5 个（个人事实/偏好、工具 committed turn、长上下文偏好、恶意 Memory、故障降级恢复）。
- 重跑/改写/continuation 冒充：SH-M1 三种表达、SH-M3 第二 root、重启追问、retry/continuation 均明确不计新类别。
- required 仍 PENDING：执行前为 NOT_RUN；定义中没有允许跳过的 required case。
- 冷启动与 surface 回归不冒充语义类别；覆盖正向价值、跨 UI/Tool、恶意输入、故障与恢复，能够发现
  只对已知“记住”关键词过拟合的实现。
- 排名/比较/推荐/优缺点不是本次 Memory 生命周期的语义入口，不强加无关输入。

## 最小充分性评估

- 当前 required 测试总数：20。
- 其中直接证明 AC 的：20。
- 其中同时防范受影响范围内风险的：16。
- 建议删除/降级的：0。
- 最终 required 测试数：20。

## 结论

- 当前 required 集为最小充分集：每项都直接证明 MUST AC 或独立高风险边界，没有无目标与重复 obligation。
- 所有 MUST AC 均有 required black-box 覆盖。

VERDICT: PASS
