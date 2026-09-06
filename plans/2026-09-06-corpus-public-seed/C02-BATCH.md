# C02：20条 setup 准备结果

更新：2026-09-06。源码终点0437fe14，Host公开来源/实际H078/M618/S0313；仅使用中文语料setup字段编译，没有用问题或gold构造记忆，没有质量评分。固定映射见 `backend/deskpet/quality/corpus_c02.py`。

20个setup分别验证通过：首批18PASS/2FAIL（6.29s）；C02-20修正测试取公开字段object_value后1PASS；C02-19最终1PASS/0.96s。通过的19项没有重跑，不把重试次数作为新增用例数。

C02-19保留B“偏好云端”为candidate/llm_inference/unverified，公开图status=inferred，未升级用户确认。实际Host完成一次确定性Provider Run，由真实assistant S1 `/source/message/content` 取引文，保留ASSISTANT/MODEL_OUTPUT，不把用户声明改成模型输出。原USER outbox先公开投递，再公开完整admit assistant与terminal来源，公开mutation及graph回读。来源准备仅1次确定性Provider调用，不是真实模型质量调用。

失败历史全部保留：r1错误USER来源被SDK拒绝；r3实际属主不一致；r4原USER ingestion未完成；r5误读assistant为`/text`；r6多来源EvidenceRef序号不连续；r7将conversation组ordinal2混用为完整ingestion item ordinal（公开SDK每envelope单payload ordinal1）。后继分别修实际fixture契约，未改SDK校验或降级为explicit_user。r2资源BUSY未启动，不计测试失败；没有反复运行绿色全批。

最后c19-r8：PG18123 exit0/1.512秒/170704KiB，remaining=[]，cleanup_error=null。前序实际测试进程也均清空。已有小target173/84/116成员核对，identity显示所有实际SDK模块均来自目标安装。

C01+C02共40条setup准备分批验证，并非40条真实质量执行。C02-19的准备Run仍需运行器隔离，避免近期对话泄漏；其余类别和完整runtime接线继续。240条真实质量完成仍为0。本叶独立审查结果另记，不扩大为整个评测系统完成。

原始证据仅本机ignored，摘要索引与SHA-256：

- `.local-test-evidence/2026-09-06/corpus-c02/r1/command.log`：`7ed1f5172c55bc2d014954501347ffef7f9c782e15c72642f59ac1c9fecd75d7`
- `.local-test-evidence/2026-09-06/corpus-c02/r1/resource.json`：`ac2b105d86a445964e4391ad7610b2e8411e63fa00508d610b8c3ef0dab1b8ab`
- `.local-test-evidence/2026-09-06/corpus-c02/r1/identity.json`：`3330dbabe6395224fafc7d1499e572518fe3bebea9057a668cf6daa423643a77`
- `.local-test-evidence/2026-09-06/corpus-c02/c20-r2/command.log`：`d9964908a46cc07b1eb7696a1f9043e89ade05ee311e07978e117417fff4da82`
- `.local-test-evidence/2026-09-06/corpus-c02/c20-r2/resource.json`：`f294a9aaa6e0352feb4b910d530923704bb6b158dcaf8887daa141cddc83152a`
- `.local-test-evidence/2026-09-06/corpus-c02/c20-r2/identity.json`：`fd64a493ad552042f4ab1c21a2d50dbd938ae163ad0cc1254763a2380cb7832e`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r3/command.log`：`6bcc9d3a0c9d60b928f4f01f5ec72a63d80a97734c1d0c42741945f7fd8816d4`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r3/resource.json`：`1293c48613e775d841927d286c65e01d12bc022a7e5f7cc1b12b6c7b3c9e4b2e`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r3/identity.json`：`5bd9d397d00f6a4f5f9e068be5327f8ce2962f83fdba12d81f75113a4680d4ca`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r4/command.log`：`e67d2eeb2fe0d8d19b2c9f37a2df183700037a4f8e519f3a0c1a8cf372c1522c`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r4/resource.json`：`02bd8a2bdb87c71989c793499afef0e4c8d5e6b8bfc41d06f6b7e30045c2618c`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r4/identity.json`：`f915e2f72d03f4b81aac7c0827879f206de8e726062646b9290d3df4f3b03479`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r5/command.log`：`c98bcd5cd387e8ba5d6fbe9ac20648d0edb23349ae6495c2d98ca8e24618b71a`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r5/resource.json`：`ff37aae8519e96e5d5ec3c8f93cb248cf535b6af9f5030a0f3b2ddd64715a70a`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r5/identity.json`：`f915e2f72d03f4b81aac7c0827879f206de8e726062646b9290d3df4f3b03479`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r6/command.log`：`23ed277f9e6916f35189d0ce921d9c3f649a69c94fa4b30da7e804e4a80ccfb9`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r6/resource.json`：`aeb4c50543ea5125db0bc5a40fd7751eb9c0e8ced78a6b657eb49808c4f906fb`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r6/identity.json`：`f915e2f72d03f4b81aac7c0827879f206de8e726062646b9290d3df4f3b03479`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r7/command.log`：`80c31ee7488ae7787980f2597536539475369292ac004644db3224fe93ff4520`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r7/resource.json`：`0b3362e52b3036979d27e09e31145d3c4870ac4e6e34c943eeddb9262d9c7a7d`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r7/identity.json`：`f915e2f72d03f4b81aac7c0827879f206de8e726062646b9290d3df4f3b03479`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r8/command.log`：`121f09133dc10d469c6f497f0929004c71a9c8e144c538d6a6e215c501ecaec4`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r8/resource.json`：`b14557c672aaddbfd3bda9a34aef0156a7b61c60778d6aaf8096e1763e78f34c`
- `.local-test-evidence/2026-09-06/corpus-c02/c19-r8/identity.json`：`47813293b8174943d599609e11475fd2b9584f6961b91159f660c97dfffe63e5`
