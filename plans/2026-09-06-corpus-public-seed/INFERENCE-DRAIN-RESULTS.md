# 多来源 analysis drain 新控制

2026-09-06。源码 cb98739d；896d790d 已获 Dirac delta 源码无确定P1，
Dirac已对73a37dd1（产品896d790d/测试cb98739d）给出最终限定ACCEPT，核4rawhash一致；
仅C03-20实际实例，不外推C02/自动prepare/跨进程proof/240。原 aea141af 的 reason/APPLIED 误判为只读确定P1，未伪造红测。

## 结果与范围

- r1 / e77a65f9：2 PASS、1 FAIL，4.35s，PG27665 exit1 / remaining[]，峰值181264KiB。
  合法两来源与非法 reason 两分支通过。取消恢复实际两APPLIED/两accepted、原application
  和首job零新executor已通过；后续graph整DTO比较错误包含推进clock后的generated_at。
- cb98739d 仅修完整graph预期的generated_at=可信now（public DTO重算payload_hash，
  保留全部节点/边和其他字段比较）。r2 只该红：1 PASS、1.85s，PG27780 exit0 /
  remaining[]，峰值176208KiB，资源总时2.35s。
- **3 unique 新控制分批绿，不是4，也不是一次最终3全跑。** 旧20setup/graph不重跑。
- 真实原USER/ASSISTANT不同Run、不同job分别由SDKrunner处理，USER lineage不变；
  graph内容不增、评分Host无source conversation。fixture delivery明确本地、零usage；
  deterministic source Provider每例1调用，不是质量模型。
- 非法reason实际送SDKvalidator，结果为job APPLIED但receipt REJECTED；adapter
  confirmed=False，拒绝被真实finalize不等于合法NO_MUTATION。
- actual audit_pending公开finalize接缝取消，close、可信clock+211秒、reopen恢复：
  原application exact，首job不调executor，第二job仅一次。未假造SDK存储或receipt。
- 完整prior入口拒绝自洽换job_ids、错误receipt result_hash、另一个实际job application。
  已完成库不提供proof时IDLE明确unconfirmed；原proof经公开SDK finalize重验后零executor。

## 命令与证据

现有 Python：`primary-m0615/venv/bin/python`；显式 installed target
`primary-078618/installed`（H078/M618），无SDK overlay/新env。
每批通过主 `scripts/run_resource_bounded.py` 默认共享锁，`--rss-mib 2048 --seconds 180`，
ignored `corpus-public-seed/run_batch.py`；r1目标 `tests/quality/test_corpus_inference_drain.py`，
r2只 `::test_inference_two_actual_jobs_applied_and_reopen_exact_finalize[cancel_audit_pending]`。
磁盘最低3326/3314MiB，无override、无模型/native/build。

原文路径前缀 `.local-test-evidence/2026-09-06/corpus-public-seed/`：

|证据|SHA256|
|---|---|
|inference-drain-r1/command.log|cea932bd73e59405c19654dd70bcc35372001ea75790bc9ab419fb2783bee3cc|
|inference-drain-r1/resource.json|8bae78b75277e02d4b4cc726825da401aa5218470cc022dc2267732f8e4b6b27|
|inference-drain-r2/command.log|99d161a645be50b349f708e7258256d26012e07682341df9edd3b5d8fc1da2cf|
|inference-drain-r2/resource.json|93a87b4eee859c9411c26aa599ff22dada6ac7c2278636937934f08e3d4e0047|

## 未完成

C02 main adapter接线与其新增控制未完成；未跟踪C02 reference不纳本提交。
已有prepare未自动调用drain，不能仅用旧返回值声称ready。跨进程proof序列化未提供，
不能用队列为空补证。完整两轮评分runtime/240质量仍0；不是全typed/privacy/E2E声明。
