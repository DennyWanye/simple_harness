# Chat Completions 可选字段合同

2026-09-07，基线主324aa613，自有树simple_harness-corpus-clock，分支feat/corpus-context-null（沿前诊断分支名，不实现nullable）。

物理Product OpenAI-compatible Provider为每个function显式写strict:false，保留Chat Completions既有非严格可选字段合同。parameters与arguments不重写；context_route只增加两个字段的省略说明及原workspace reuse拒绝的公开message。memory_standalone应完全省略reuse_workspace_of/expected_source_hash；非空假ID/hash继续原拒绝，不把字符串null/none或空格清洗为缺省。真实create_new复用的精确来源、绑定及hash校验不变。原始proposal继续按原内容进入审计hash。

主审已核[官方Strict mode合同](https://developers.openai.com/api/docs/guides/function-calling)：Chat Completions缺省非严格，显式strict:false保留optional语义。这不是线上relay确实改写schema的证明。

原C01-13保存15次Provider/14次工具拒绝及语义FAIL，不重跑刷绿；连续失败未在10次停止仅记录待后继定位，本叶不改状态机。C01-10原FAIL同样保留。

已完成本地诊断（非本叶验收）：实际fake HTTP wire无strict、required仅route；typed JSON null被原string schema拒绝。H079工具validator既不支持type数组也不支持anyOf，直接nullable会TypeError；本叶不改SDK/版本/installed。证据.local-test-evidence/2026-09-07/context-null-wire/r1/result.json，SHA-256 5d85e46e29da86617a9334a273b7248b6c0c5303257eb4e3a0df74144ebc44a1。

唯一新增组合控制：fake HTTP→实际Provider解析→SDK参数校验→Host handler/持久ledger→公开ToolResult；省略字段合法memoryroute，假reuse字符串及全零hash仍拒绝且不再次调用recall，proposal hash保持。HTTP响应与recall空结果是确定性夹具；不称真实模型、Memory召回质量或main全栈验收。源码9073b965经Dirac窄审无确定P0/P1后，默认资源锁2GiB/180s首跑该1控：1PASS0.19s；PG71603 exit0、remaining[]、cleanup_error=null，资源总时长0.657s，峰75632KiB，最低磁盘3388MiB。共享锁已释放。


复现命令（只运行本叶1控；已通过不重复）：

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python /Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir /Users/denny/projects/simple_harness-corpus-clock/.local-test-evidence/2026-09-07/context-null-wire/nonstrict-r1 --rss-mib 2048 --seconds 180 -- /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python -B /Users/denny/projects/simple_harness-corpus-clock/.local-test-evidence/2026-09-07/context-null-wire/run-nonstrict.py /Users/denny/projects/simple_harness-corpus-clock/.local-test-evidence/2026-09-07/context-null-wire/nonstrict-r1
```

Python加载既有H079/M619 target与自有backend；不修改vendor、installed或.env。原始证据只留ignored。下列路径均相对本树，SHA-256：

|路径|SHA-256|
|---|---|
|backend/deskpet/sdk_adapters/provider.py|9756433b8abaf1102b9b9b7bb75fa5a652f523af7f2de0146cf18cfc88bfc312|
|backend/deskpet/sdk_adapters/context_route.py|441c647b288557ff1fc93ce80ebccc44d31760446246876531d8a1dca4dc3bf5|
|backend/tests/sdk_adapters/test_context_route_nonstrict_wire.py|c4ba8d7031139ef0c753121b2378e35adbf695c00f5b6117696b05b2d049f7cd|
|.local-test-evidence/2026-09-07/context-null-wire/run-nonstrict.py|45f374da994d61839c8ce9a0d34a1aa3ad4ebf4616471acb88392d75ca90fb64|
|.local-test-evidence/2026-09-07/context-null-wire/nonstrict-r1/command.log|f69b41b567fe2583118441e8e8390afa756eecb8625fa01f5e10bcff2c1cef10|
|.local-test-evidence/2026-09-07/context-null-wire/nonstrict-r1/resource.json|b6cdd9caebdaeb9894bea95a9b4bdaae8b036c569626973f7fdc9df470b00c28|
