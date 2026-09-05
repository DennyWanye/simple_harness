# TC-HM-13 normal/source execution increment

2026-09-05，独立分支 `feature/human-memory-typed-recall-runner`，受测代码 `4b1c6dcf`。
**S3/program 未完成；不合并、不 push、不改主树。**

已提交：`f1371506` 纠正 §3 的事实性 NUL 错误为 `E(d,x)=H(C({domain:d,payload:x}))`，
四条完整独立向量先于执行冻结，§4 state NUL 保留；`bda1bac5` 保留前序 lineage 后 pin
经独立复审采用的 clock-only Memory0.6.4；`2ab053d0` 在0.6.5执行前冻结整数5版本攻击映射。
原401 ID /391 public+10 source/14攻击/负例/数值阈值不变。fixture rev6，layers rev4。

本机提交态结果：**132 PASS / 0 FAIL / 269 BLOCKED**。Public391 中334 OBSERVED、
56 executor未实现、1 protocol-version无输入；source10全部OBSERVED，尚无source正式PASS。
132通过来自原 eligibility 与两个原 literal budget cells；桥回归 **59 passed** 不计入产品通过。
新建 clean venv，无PYTHONPATH；两层逐字节核验 Harness0.7.2 的151文件、Memory0.6.4的61文件。
source使用独立 detached exact16dc707，不使用正在修改的 recall-observability worktree。
共享 Host backend/.venv 当前仍Memory0.6.3，本轮未修改。

真实路径：独立SQLite入库、admitted evidence与mutation apply/receipt、typed recall/replay；
原有效期/lifecycle/epistemic/disclosure/attribute输入；literal预算整项选择；vector不可用；
真实授权r1..r7→CONTEST r8→resolution r9；strict parser与page；7处sourcefault/restart与3处
真实member腐败/reopen。只有完整独立断言可PASS；无法建立前置状态的真实拒绝仍BLOCKED。

独立初审发现3P1（实际边界时间、receipt/evidence绑定、invalid-plan空结果误认资格门）和1P2
（证据失效不撤销PASS），均已修复并有决定性反例回归；复审确认四项resolved。旧130PASS
初判曾明确撤回，旧原始记录保留；当前132来自修复后重新执行，不回填旧receipt。
复审接受 public apply-result→receipt-ref→receipt-view 作为recall source identity绑定；
未假称独立重算未公开的私有receipt完整preimage。

仍未完成：56 public executor、typed observation/procedure applicability/prospective signal与
short-horizon注册等前置链，拒绝内部stage/read见证与版本输入，完整冲突组/成员/resolution
canonical绑定、source完整PK/非final roots、返回攻击完整见证、projection canary/cross-scope。
source7在注入前已有独立两源业务控制，precommit final表严格old，ACK丢失严格已commit与
exactreplay，不以old/new任选通过；完整state绑定欠缺所以仍BLOCKED。

本批未确认新SDK产品缺陷。明确契约差异：原128-byte page bound不能容纳现有完整binding，
实际返回`typed_recall_page_budget_too_small`，原bound保留；Episode原`occurred_interval`与
公开`occurred_start/occurred_end`差异待完整projection验收。初期9失败是runner契约拼写/解释
错误，纠正依据见NORMAL-EXECUTION文档，未改生产/fixture来迎合结果。

原始证据（ignored，不提交）：
`.local-test-evidence/2026-09-05/typed-recall-committed-clock064-r3/bridge-summary.json`
SHA256 `39ff6c293f6e83635f8d3356f9e2fb34a3e082d693603c852f719a2a7a250805`。
其中索引关联两层requests/observations/runtime及字节hash；数据库、日志、venv原样留本机。
独立review日志：同日`typed-recall-normal-review.log`、`typed-recall-normal-review-r2.log`。

最小复跑（在本分支worktree，RUN_DIR必须全新）：

```sh
/Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest testcase/human-memory-program/tests/test_typed_recall_bridge.py testcase/human-memory-program/tests/test_typed_recall_a2_oracle.py testcase/human-memory-program/tests/test_typed_recall_normal_bridge.py -q
/Users/denny/projects/simple_harness/backend/.venv/bin/python testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --harness-wheel /Users/denny/projects/simple_harness/backend/vendor/simple_harness_sdk-0.7.2-py3-none-any.whl \
  --harness-wheel-sha256 53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed \
  --harness-source-commit 2b8428465cbd41032ba024a0b7199183161f5ecd \
  --memory-wheel /Users/denny/projects/simple-harness-memory-sdk-recall-observability/.local-test-evidence/2026-09-05/clock-candidate/build1/simple_harness_memory_sdk-0.6.4-py3-none-any.whl \
  --memory-wheel-sha256 595e754d752c3f7ecdbc5d4613d9c302aea632f06362ec271a561d9606ace696 \
  --memory-source-commit 16dc707cb6216e9172623eecc5a3a300f128ffe3 \
  --source-checkout /Users/denny/projects/simple-harness-memory-sdk-typed-recall-source-16dc707 \
  --source-adapter testcase/human-memory-program/adapters/typed_recall_source_cases.py \
  --child-timeout 180 --artifact-dir "$RUN_DIR"
```

预期runner退出3（整体仍BLOCKED），不能把退出0/合成self-check视作401通过。
