# S3 / TC-HM-13 首批真实执行记录

日期：2026-09-05。分支 `feature/human-memory-typed-recall-runner`。**整体 BLOCKED，非 S3/program 完成，未并入主树、未 push。**

批准先于修改：`38356e7f`。oracle/14 攻击映射先于产品执行：`e46edaa0`。本次受测代码提交：`d4036037`。
后续 Harness 候选指令单独保存在批准记录；不把候选升级当作 oracle 修订授权来源。

## 当前已落实内容

- fixture rev4 / layers rev2；旧 fixture 字节 hash、两份不同旧 Memory pin 分别保留 lineage。主 fixture 与 layers 当前候选身份一致。
- 独立完整 Semantic claim source/projection 向量与 hash；消费路径读取 approved source vector，不再把旧 `{}` qualifiers 当完整 source。
- 401 IDs 和 391 public/10 source 分层保持原 hash；14 原攻击点全部保留。原场景、负例、预算/阈值各段落的 hash 保持不变，有独立回归锁定。
- 14 行映射在执行前固定；13 个可表达输入实际调用公开 Manager，独立核对变异及配套输入、公开异常类/文本、真实 manifest 零状态增量。**内部拒绝层与 candidate-read=0 仍缺公共见证，不能声称已验收这些属性。** `protocol_version` 无公开 request 输入，明确 BLOCKED，未用另一个版本 parser 冒充。
- 完整实际 manifest 保存所有 roots、payload_hash、access_event_hash。独立 protected hash 只使用预声明表集；拒绝操作还核对全表集合不变，只允许已知 manifest 读取审计表变化。old/new cell 标签不再进入新 fixture 的产品判定。
- fault/control 的提交阶段限制及“控制先有独立业务正确性”的拒绝逻辑已落实为验证器回归；**没有执行 source10，也没有声称 fault 通过**。
- 旧 rev3 合成 artifact validator 对 rev4 禁止使用；当前 observation 适配器无 PASS 权限，父进程的业务失败优先于 BLOCKED。

尚未完成 oracle 部分：request/context/plan/receipt 的已批准 NUL preimage 与固定 SDK 公共契约不符；完整 conflict/member/resolution 的动态 binding 执行器与全部 receipt 场景尚未实现。旧 literal commitments 已明确降为历史，不以其通过率作为产品证据。

## 本机提交态结果（不是历史 receipt）

| 范围 | 结果 |
|---|---|
| 验证器/桥回归 | **54 passed**，不计入产品 PASS |
| formal401 | **PASS 0 / FAIL 0 / BLOCKED 401** |
| public391 | **19 OBSERVED**，371 执行器未实现，1 原 protocol-version 攻击无公开输入 |
| source10 | 未执行/未配置，不能归咎于产品失败 |
| clean consumer | 新建 venv；Harness0.7.2 与 Memory0.6.3；逐字节核验 151/61 个 SDK 文件 |
| 产品缺陷 | 本批未确认新的 S3 产品缺陷。分页冻结时钟与异常读取见证是 API/验收可观测性缺口 |

19 个观察：unbounded Semantic recall 1、exact replay 1、conflicting replay 1、13 个真实输入变异、unsupported 3。
unsupported fixture 列表仅列攻击 capability；实际完整 Context 使用必要的受支持 MEMORY_TYPE carrier，空 modes 使用受支持 FULL_TEXT。保留原攻击列表，不能添加/删除 unsupported reason；三个执行均返回原负例要求的精确有序 reasons、REJECTED/invalid-plan、零 query 与零 payload。该构造及原始输入都保存于观察，不计额外 cell。

真实 recall 返回原 seed 对应非空 payload、正确 source/projection hash、revision/receipt identity 与独立 RRF/evidence bindings；exact replay decision/result bytes 相同、query=0。
冻结 recall now 传入真实 `execute_typed_recall`；audit authority 使用真实当前时间。分页内部按当前时间判断，得到 `typed_recall_result_expired`，没有将冻结 now 平移以凑 PASS。

证据目录（均 ignored，仅本机保存）：
`.local-test-evidence/2026-09-05/typed-recall-a2-committed-d4036037/`。
`bridge-summary.json` SHA-256：`456d9d330e1d5d1cc23c1bd360bcdaeff8883926e5e5619d3b1f6d3bc30b950b`。
同文件索引 request、observations、runtime 的 SHA-256；`cell_results` 保留全部401项的执行状态、验收状态、业务断言及具体阻点。
回归日志：`.local-test-evidence/2026-09-05/typed-recall-a2-committed-unit.log`。
早期三个执行器预置/接线错误（audit max_reads、principal 注册顺序、分页异常未捕获）及 run 配套字段的中间态构造错误已修；早期失败日志保留，不计为产品缺陷。

独立只读初审 task `01a06f2b-efce-74b3-84bf-2f4ae5172162` 提出2项P2：过度声明拒绝层见证、成功分页分支未校验。已在 `d4036037` 整改并增加决定性回归；提交态回归及真实 smoke 如上。未冒充再次独立复审。原始 review 日志：`.local-test-evidence/2026-09-05/typed-recall-a2-review.log`。

## 最小可复跑命令

从本独立 worktree 根运行；输出目录名必须未存在。默认建立新的 clean venv，不加载 WeMM/MPS、provider 或 UI。

```sh
/Users/denny/projects/simple_harness/backend/.venv/bin/python -B -m pytest -q -p no:cacheprovider testcase/human-memory-program/tests/test_typed_recall_a2_oracle.py testcase/human-memory-program/tests/test_typed_recall_bridge.py

/Users/denny/projects/simple_harness/backend/.venv/bin/python -B testcase/human-memory-program/runners/run_typed_recall_public_consumer.py \
  --harness-wheel /Users/denny/projects/simple_harness/backend/vendor/simple_harness_sdk-0.7.2-py3-none-any.whl \
  --harness-wheel-sha256 53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed \
  --harness-source-commit 2b8428465cbd41032ba024a0b7199183161f5ecd \
  --memory-wheel /Users/denny/projects/simple_harness/backend/vendor/simple_harness_memory_sdk-0.6.3-py3-none-any.whl \
  --memory-wheel-sha256 6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78 \
  --memory-source-commit 2f3d73814fe6a884e0458d87567b918c5863033e \
  --artifact-dir .local-test-evidence/2026-09-05/typed-recall-a2-NEW_RUN
```

当前预期 exit3 / NOT_RUN/BLOCKED。真实业务断言失败或运行/身份/证据失败 exit1，绝不被 hash 差异掩盖。
没有重跑401合成自检、SDK全量测试或真实模型质量门；没有新的机器 SHIP receipt。

## 具体最小修正与后续执行边界

1. **提案事实性错误，不能先归罪生产**：提案 §3 的 `D(d,x)=H(UTF8(d)+NUL+C(x))` 与 SDK 已存在的 `E(d,x)=H(C({domain:d,payload:x}))` 不同。Harness 固定源码 `runtime/disclosure_protocol.py:_domain_hash`、Memory 固定源码 `core/recall.py:_digest/request_hash` 均为 E；前者被 Context/Plan/use-receipt 调用。建议仅将 §3 的 SDK 既有 domain 算法更正为 E，保持所有 domain 字符串、字段、14攻击、401及阈值不变，再冻结独立输入向量；§4 **新验证侧** protected-state 的 NUL 定义可保持。不要用实际 result 的 hash 回填 gold。
   若反而要求生产迁为 NUL，context/plan/request/receipt/decision/result 历史 hashes 与 replay identity 都会变，必须版本化/迁移并另作 backcompat 验证；这并非候选 pin 更新。本批保留已批准公式并报告差异，未静默选择。
2. **公共时钟缺口**：`execute_typed_recall(now=...)` 可固定时间，`page_typed_recall_result` 内部读取 Manager 时钟；仅设置 page request 时间不能得到冻结时刻一致性。最小生产方案是在可信公共 builder 注入同一 clock port，所有相关操作使用该 port，默认保持生产时钟。不能让不可信请求任意回拨授权时钟，也不在本批修改 Memory 生产源码。
3. **零查询/拒绝层见证缺口**：exception 路径没有每次调用的公开 candidate-read 计数和稳定 admission stage。实际 manifest 能证明写状态，却不能证明未读候选。最小生产方案提供内容无泄露的 typed rejection receipt/audit ref，绑定请求、稳定阶段、reason、candidate query started/count；保留原负例要求。当前只承认已核对输入及异常，不拿 SQL 或内部 traceback 冒充 public 证据。
4. **protocol-version 原攻击无 public input**：请求版本目前是 backend 隐式常量；不能用 Context schema_version 或 Result v3 parser 的另一个错误替代。保持该 cell BLOCKED；需明确公开 versioned request admission 或等价原契约映射，不能删 cell。
5. **剩余执行器是真实欠账**：371 public 与10 source 执行器仍需实现，尤其 conflict/member 公开绑定、current-use/suppression、全 eligibility/selection/budget 和 source corruption/fault。不得因为上述 blocker 就将这些欠账描述成产品已完成；source fault 必须在 no-fault 控制满足独立业务断言后才注入，commit 后 ACK loss 只能 exact replay，不能任选 old/new。

本批未推进 S3/program DoD 或合并。保留可执行、可复跑的首批证据与后续实施范围，等待公共契约差异获得具体修正后继续完整验收；不重复申请已经获得的常规实施授权。

## 本次文件清单（相对本独立 Host worktree）

```text
ARCHITECTURE/MEMORY_SDK_BOUNDARY.md
ARCHITECTURE/PROJECT_STATUS.md
testcase/human-memory-program/TC-HM-13-typed-recall-result.md
testcase/human-memory-program/adapters/typed_recall_public_cases.py
testcase/human-memory-program/adapters/typed_recall_public_manager.py
testcase/human-memory-program/fixtures/typed-recall-execution-layers-v1.json
testcase/human-memory-program/fixtures/typed-recall-v3.json
testcase/human-memory-program/runners/TYPED-RECALL-A2-APPROVAL-2026-09-05.md
testcase/human-memory-program/runners/TYPED-RECALL-A2-FIRST-BATCH.md
testcase/human-memory-program/runners/TYPED-RECALL-ORACLE-REVISION-PROPOSAL.md
testcase/human-memory-program/runners/run_typed_recall_public_consumer.py
testcase/human-memory-program/runners/typed_recall_a2_oracle.py
testcase/human-memory-program/runners/typed_recall_bridge.py
testcase/human-memory-program/tests/test_typed_recall_a2_oracle.py
testcase/human-memory-program/tests/test_typed_recall_bridge.py
```

提交顺序：批准 `38356e7f` → oracle `e46edaa0` → 实现 `d4036037` → 本首批文字结论提交。均留在独立分支；当前不执行 cherry-pick/合并。后续文字记录不改变受测代码身份。
