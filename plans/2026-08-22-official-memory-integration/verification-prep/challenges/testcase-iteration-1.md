## AC 覆盖审查

- AC-1：SDK-C01、SDK-R01 已覆盖。
- AC-2：SDK-C02、SDK-C03 与 SH-M1/M3/M5 已覆盖。
- AC-3：SDK-T01～T03 与 SH-M2/M6 已覆盖。
- AC-4：SDK-T03、SDK-I01/I02 与 SDK-R01 已覆盖。
- AC-5：SDK-S01～S03 已覆盖。
- AC-6：SH-M1～M6 与 SH-SURFACE 覆盖新数据路径，但初稿未直接证明 plan 声明的 v3→v4 升级、mixed-pair 恢复与 rollback。
- AC-7：故障、并发、隐私日志由 SDK-C03/T01～T03/I01～I02/S01～S03 与 SH-M4/M6 覆盖。
- AC-8：SDK-R01 与 SH-SURFACE 覆盖发布/未来消费者；升级回滚仍是缺口。
- 无未绑定 AC/risk 的 required testcase。

## 缺失的必要测试（仅列出直接证明 AC 或防范受影响范围内风险的测试）

- [AC-6/AC-8, TO-R5] 缺少 v3 eligible/suppressed/deferred 分类、mixed pair 启动恢复和 pin+DB rollback 的公开产品升级用例。
- [AC-2/AC-6, input-sensitive] smoke 中已有口语/中英混合/无关键词输入，但没有绑定到 required SH-M1，无法保证 gate 真执行。

## 应删除或降级的测试（无目标绑定或超出受影响范围）

- 无。远程 Memory/跨设备同步仍只列 TO-E1 exploratory，没有进入 required。

## 步骤/预期不清的用例

- SH-M1：需把自然表达变体写进 required 步骤和 scenario required lanes。
- 新增 migration case 必须只断言 manifest digest、export、启动选择和 rollback 等外部结果，不能绑定私有表/函数。

## 建议新增的 required testcase（必须说明绑定哪个 AC 或防范哪个受影响范围内的风险）

- SDK-M01：[绑定 AC-6/AC-7/AC-8、TO-R5] 使用隔离 v3 fixture 验证 eligible/suppressed/deferred、每个 swap crash window、mixed pair 恢复与完整 rollback。
- SH-M1 新增 required lanes：[绑定 AC-2/AC-6] 口语、中英混合、无“记住”关键词表达各一次。

## 输入广度盘点（仅当 input_sensitive=true 时必填）

- distinct 输入类别：5 个（个人事实/偏好、工具 committed turn、长上下文偏好、恶意 Memory、故障降级恢复）。
- 重跑/改写/continuation 冒充：重启追问、两个 root、同义改写、retry/continuation 都不增加类别计数。
- 覆盖跨场景、错误态和恢复态；本功能不以低证据搜索为目标，低证据类不适用。
- required 仍 PENDING：执行前全部为 NOT_RUN，gate 执行期不得以 PENDING 完成。
- 冷启动是个人事实类的状态路径，surface 是入口回归，均不冒充新语义类别。当前类别足以暴露只对
  “记住”关键词特判，但需要把自然变体升级为 required lane。

## 最小充分性评估

- 当前 required 测试总数：19。
- 其中直接证明 AC 的：19。
- 其中同时防范受影响风险的：15。
- 建议删除/降级的：0。
- 最终 required 测试数：20（新增一个不可与其它 case 合并的 migration/rollback 风险门）。

## 结论

- 当前初稿尚非最小充分集：数量不冗余，但缺少产品升级回滚这一关键 change-risk，且自然表达未进入 required lane。
- MUST AC 名义覆盖完整，AC-6/AC-8 的切换风险证明仍不完整。

VERDICT: FAIL
