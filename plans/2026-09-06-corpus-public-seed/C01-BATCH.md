# C01后继公共setup批次

2026-09-06，既有7a1aebed后继；旧C01-10/graph测试未重跑。

实现：corpus_c01.py固定20条setup原文/hash（不收gold/initial/Case）。18个新非revision
case完整A/B record在同public atomic plan建Semantic/Episode/Prospective，公开receipt
逐label核actualID/revision/type/完整contenthash/evidence，公开graph回读当前节点。
C01-10保留上一片已测入口，C01-06由独立真实public revision入口执行。

C01-06：限定fixture issuer，不是自然语言产品authority。实际Host S1、真实公共旧B
receipt/contentHash、当前graph head校验后，公共issue_memory_action_authority；
HostProgramStore.append_evidence持久化专属runtime_event后才暴露ref。真实REVISE生成
同memory的1→2；不是两条不关联的CREATE。changedpayload直接issuer拒绝，SDK同key
改hash拒绝，wrongissuer/未持久ref拒绝；重开读原grant，不续期；消费后过期replay
保持原result。MEMORY-only suppression后公开graph空、实际S1historyvisibility=false。
原receipt保留，不冒充当前可见性证明。

批次（raw ignored）：
- c01-r1:18PASS/5.50s，PG7283exit0/resource6.198s/peak176816KiB/remaining[].
- revision-r1:1FAIL，negative错误期望ValueError，实际typed MemoryIdempotencyConflict；
  主正向revision/head已执行。原红保留。
- revision-r2:1FAIL，history evidence binding错传ID/hash；实际SDK要求envelope/receipt。
  未绕校验，改正确公共DTO。revision/reopen/suppressiongraph此前均执行。
- revision-r3:1PASS/.78s，PG7688exit0/resource1.323s/peak162944KiB/remaining[].

19个新增setup/control通过；与此前C01-10来源分批保留，不拼成最新240质量通过。
纯setup还不是模型运行：没有Provider，没有setup→recent history隔离验收，没有SDK
seed ingestion pending分析任务的真实消费闭合，没有两轮评分。下一运行适配必须解决
这些实接口，不准把seedqueue初始上下文带给query或额外analysisProvider。其他类仍NOT_RUN。

运行命令：默认shared run_resource_bounded.py --rss-mib2048 --seconds180 --
<existingPython> -I -B .local-test-evidence/2026-09-06/corpus-public-seed/run_batch.py
<batch> tests/quality/test_corpus_c01_batch.py（revision批为test_corpus_revision.py）。
复用主H078/M618 installed，无SDKoverlay、新env、模型、native或SDK修改。

- `.local-test-evidence/2026-09-06/corpus-public-seed/c01-r1/command.log` SHA256 `a9ff617d3aa3544c92932fd61794fe23469f2e1de6b11e7f2ebfe772adb1e3c1`
- `.local-test-evidence/2026-09-06/corpus-public-seed/c01-r1/resource.json` SHA256 `7a225716d3f9fa541e7db20f8032f4b3a3a085ea559ff76484b5777f4b71fba1`
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r1/command.log` SHA256 `417666682757aa89c49595705641939c6f19db311280df193c5a69ec37da39ac`
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r1/resource.json` SHA256 `1747e555378f113ca65e1b8351b42cd184c67778c835642bebccc457f55a2a74`
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r2/command.log` SHA256 `15567dfbfb8997d76644352930d4592bb06f6a809dc8147a3c29d2719ee571ad`
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r2/resource.json` SHA256 `9017ba2eaf5a555c5fa5fd966fd2c5a47f2aa067ea6df7c99b95d1c18734adfa`
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r3/command.log` SHA256 `fedceb118a93f8e9bed4f5b262246c28f6bfdace0d13713f5045fa2fb6554486`
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r3/resource.json` SHA256 `6b88c65d83e5ecc9e7778198f578f4eec5cad92f3b041c0abaf70a1db370abbb`

## Dirac P2 causal oracle followup

65340d13 source+18/1 results received limited ACCEPT. Its original history check
showed post-forget denial only. New revision-r4 adds the same binding/disclosure
visible BEFORE suppression and invisible AFTER; no other policy/context change.
1PASS/.79s, PG8072exit0/resource1.520s/peak162560KiB/remaining[].
This strengthens the existing test, not an additional unique corpus case.
Old C01 matrix/graph controls were not rerun.
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r4/command.log` SHA256 `ac0988b070cfb8585ee6cce3a7e8654f43da14bf7e316bc0b9277c6ad43db855`
- `.local-test-evidence/2026-09-06/corpus-public-seed/revision-r4/resource.json` SHA256 `4e54116af0fb6cec4f11863ab34bccd70ac1e768888c72b6d09a9fef3dda3706`
