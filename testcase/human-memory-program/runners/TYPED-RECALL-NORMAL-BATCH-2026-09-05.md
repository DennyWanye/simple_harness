# TC-HM-13 normal/source execution increment

## Lifecycle/short 已复审增量（2026-09-05）

代码 `1e72f2ff`：fresh0.6.5两层 **178 PASS / 0 FAIL / 223 BLOCKED**，349 public +10 source
OBSERVED，42 public尚无执行器。14个非初始lifecycle通过真实授权历史建立，4个完整Episode/
Semantic cell通过，其余applicability/signal继续BLOCKED。6个新short/mixed路径实际完成
11组公开注册、owner登记、projection、typed recall/replay及suppression/future控制，0 short
正式PASS；原5日TTL与任意expires_at、旧payload/hash差异仍保留。

独立复审 `typed-recall-lifecycle-short-review-r2.log` ACCEPT，76桥专项通过（不计产品cell）。
初审发现中间payload与action grant绑定两项P2，曾暂不接纳4个新增PASS；修复后完整重跑，
每步payload/receipt/target/evidence及v2 grant/intent、v4 operation-intent、v5 plan-intent
独立核验，三个重算外围hash的篡改反例回归通过。旧r4原始结果保留，不倒填。

当前索引 `.local-test-evidence/2026-09-05/typed-recall-clock065-r5/bridge-summary.json`，SHA256
`255e40bb644ecbd32b4987eb892d67318348bd7bc6331fd40f148e2f6aabc2c0`。12个执行文件hash
与提交Git blob逐字节相同。最小命令及exact候选沿用下方0.6.5段；artifact-dir取新路径。
仅分支工具增量，未并入主树、S3/program未完成，源层10仍0正式PASS。Popper的两个typed
fixture helper文件保持其独立未提交工作，本提交未改动/纳入。

## 0.6.5 已复审增量（2026-09-05）

代码 `0a3cd209`，fixture rev7/layers rev5 pin `18324022`。本机 fresh clean venv 两层实际结果：
**174 PASS / 0 FAIL / 227 BLOCKED**；343 public + 10 source OBSERVED，48 public executor未实现。
74桥专项回归通过，均不计产品cell。独立复审接受拒绝控制P1和跨principal setup P2修复。
首次0.6.5的14攻击PASS曾撤回；现在要求独立非空业务基线、完整hash、exactreplay、公开manifest
的终态行精确增量和攻击前后绑定，再消费immutable invocation-bound receipt，整数5只接受
protocol/typed_recall_protocol_unsupported。14攻击、可信typed authority新增6、UNKNOWN新增20
均由真实调用及独立oracle通过；额外6状态路径仍因完整绑定欠缺BLOCKED。旧候选和错误证据保留。

当前完整运行索引 `.local-test-evidence/2026-09-05/typed-recall-clock065-r3/bridge-summary.json`，
SHA256 `5f2b4be4e06849b54ba8b101481725a747e91fbc12d3299cf9d27d53183b8dd0`。执行时工作树
随后提交为0a3cd209；提交后逐一核验11个execution_code_sha256对应文件和Git blob字节一致，
没有为了提交重复跑同一401。独立review `typed-recall-witness-review-r2.log`；它确认P1/P2
resolved并另跑34个专项通过。SDK public安装身份每层151 Harness /61 Memory文件一致；
source为独立detached30743bb。未使用用户提供的producer测试数冒充本机验证。

Memory0.6.5 source `30743bb17ed8301d01028357de6e4c5adcdde26b`，wheel SHA
`0977159d043d409d39232d0f14f91d27f1b09ac1a4523cf8aba9028f0d0a71df`。最小复跑沿用下方命令，
将memory-wheel替换为`/Users/denny/projects/simple-harness-memory-sdk-recall-observability/.local-test-evidence/2026-09-05/rejection-candidate/build1/simple_harness_memory_sdk-0.6.5-py3-none-any.whl`，
memory-wheel-sha256/source-commit用本段身份，source-checkout用
`/Users/denny/projects/simple-harness-memory-sdk-typed-recall-source-30743bb`，RUN_DIR取新目录。
Harness身份不变，退出3（总体BLOCKED），不启provider/UI/MPS，不安装主树venv。

剩余项分层：
- 执行/setup工作：48执行器、合法lifecycle历史、typed双span、procedure applicability/
  prospective signal、short conversation及current-use；Popper正独立提供typed helper叶子，尚未整合。
- 完整oracle工作：冲突/返回/source PK与非final状态绑定、projection canary、vector executed-lane。
- 明确契约差异：原24 AUDIT recipient均非AUDIT_REVIEWER，公开构造器禁止；原16
  verified_external非source_verified组合也被公开DTO禁止；原procedure eligible不属于
  公开enum；128-byte完整page binding不适配。不得换输入/增阈值通过；这些不等于已证实产品缺陷。
- 尚无本批确认的新SDK产品缺陷。不能把构造或入库拒绝当recall资格断言通过。

**分支未并入Host，S3/program未完成。** 主共享venv仍0.6.3；原401/391+10/14攻击/阈值保持。

## 0.6.4 历史已复审增量（证据保留）

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
