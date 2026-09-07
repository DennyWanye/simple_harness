# TC-HM-13 有界 oracle 修订批准与执行基线

plan-status: finalized
日期：2026-09-05；执行分支：feature/human-memory-typed-recall-runner。

用户明确授权原话：

> 我批准你进行，另外，我不喜欢你把token和资源浪费在反复的确认上

本轮用户明确该批准覆盖 SDK 限定解冻与此前 S3 §3–4 有界 oracle 修订，并要求先固化批准/方案、再修改冻结fixture/runner与401cells验收，无需再次确认常规选择。

## 被批准的具体方案

- [提案](TYPED-RECALL-ORACLE-REVISION-PROPOSAL.md)，批准前字节 SHA-256：`7f3f7d1ff2be15972720ce3874233ff2f575f323fcd76e4e352ab32c8374b455`；Git 基线 `0b746a181872a9eb21f9e37501a48d57d9f28b13`。
- 含独立审查通过后追加的两项 P2：14变异执行前固定攻击点→字段→配套绑定→拒绝层/reason；no-fault对照先通过独立业务断言，pre-commit只允许start attempt，post-commit ACK丢失必须exact replay，不能任选old/new。
- fixture/验收义务 authority 仍为原 program plan、S3 Task5、TC-HM-13，方案只修正已列 canonical 字段/domain/真实状态见证；候选pin升级与同source重建分别记录。不得产品输出回填预期。
- 固定401个cell IDs，391 public + 10 source；全集hash `49e4433ebdc7d4551a3425e6006b8143dffd89ca2545331072a9196f4ba66dea`。负例、预算/质量阈值、exact-limit数值不变。真实公开API不能兑现时保留具体BLOCKED，不移层、不用私有SQL冒充public。
- 当前执行身份：Harness0.7.1/source f5fe0dc7e8c5b521444e01c40cab176f3666c627/wheel4d5d2b7ba5c2f8ef4956af77769d75e1ac7889a037acbdcf853d0b9a5b3a3218；Memory0.6.3/source2f3d73814fe6a884e0458d87567b918c5863033e/wheel6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78。版本为执行者修复并经独立复审采用，非用户亲选。

## 本分支执行范围与顺序

集中执行：oracle与adapter相互依赖；仅Host testcase工具/已批准fixture修订与对应架构事实，SDK checkpoint修复由独立主执行线处理，本分支不改SDK生产代码。保留独立worktree，不合主树、不push。

1. 本批准记录先commit，保留原方案Git身份；基线桥回归40项。
2. 固定独立canonical/state/fault oracle与14行变异映射；必要回归先于消费者实现。
3. 真实public调用/隔离与source/fault10分别执行，逐cell记录事实和缺口，合并时复核身份/集合/hash/freshness。
4. 正确性独立review、受影响测试与提交态复验；按实际结果更新TC13和架构，不将deterministic gate或selfcheck宣称为真实质量门完成。

这是已批准的API测试工具增量，沿用原验收集与独立评审；不新建整program机器账本，不触碰主执行者正在使用的active-run。journal记录范围兑现与遗留；原始证据仅在本分支ignored .local-test-evidence。批准不表示任何cell已通过。

## 后续候选指令（执行前追加）

用户明确指定本轮采用已批准、独立复审并提交的 Harness 0.7.2，source `2b8428465cbd41032ba024a0b7199183161f5ecd`，wheel `53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`。Memory0.6.3身份不变。只运行确定性消费者，不加载 WeMM/MPS。
