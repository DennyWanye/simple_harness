# Procedure Scope 新控结果与独审入口

当前补充（2026-09-06）：来源边界四项及恢复十二项新控现已分批通过；5e513eda/db7ca22原三Scope独审已限定接受，新恢复叶待独审。见[恢复结果](RECOVERY-RESULTS.md)。

2026-09-06。业务固定 Host `356cbdc3`（`5971cd9d`→`9059416b`→`f301c8ed`→`356cbdc3`），Memory `f82c2b8`（`5f3c06d`→`9b26234`→`e84e334`→`f82c2b8`）。这是源码组合证据，不是后继 installed、真实模型或 native。

## 实际结果

| 批次 | 结果 | 真实边界 |
| --- | --- | --- |
| SDK sdk-r1 | 6 PASS / 0.87s | prepare/read target/operation observation 新公共控制；在 e84e334 执行。f82c2b8 不重复旧绿。 |
| Host host-r1 | 3 PASS、1 FAIL / 3.83s | 非空52→53回滚/重开及cursor续写、已prepared timer实际消费、public wrapper→Host audit 通过；三Scope先被稀疏树缺H078 manifest阻断。 |
| Host host-r2 | 1 FAIL / 3.55s | 真实工具schema拒绝任意嵌套参数对象；改显式 arguments_json，严格JSON解码，不改变实际SDK Tool schema规则。 |
| Host host-r3 | 1 FAIL / 3.53s | staged默认确认需求被混成manifest危险；指纹分别保留 requires_confirmation / manifest危险，原effect授权不变。 |
| Host host-r4 | 1 FAIL / 3.75s | 两文件真实写入，工具注册Scope却沿用foreground初始None。 |
| Host host-r5 | 1 FAIL / 4.03s | v3已正确绑定工具Scope；SDK旧span JOIN不认识实际source-only admission。 |
| Host host-r6 | 1 PASS / 9.53s | 三真实独立Scope、同工具/实际workspace指纹、每Scope两次file effect、完整group公共注册→prepare→持久Host authority→public record，依次DRAFT/ELIGIBLE/ACTIVE，成功数1/2/3；同组重放不加计数，三个operation的Host审计均captured_bound。 |

最后一批PG11070 exit0，10.017s，峰219184KiB，remaining=[]、cleanup_error=null。此前批次也均正常清理，无残留。本批是确定性Provider选择驱动真实组件，不是真实模型分类；全部raw留本地ignored。主接管共享槽跑native后不再测试。临时两vendor symlink已恢复到Git原文件，没有入提交。F01发布来源明确延期，不实现。

## 生产修正与审阅重点

新 schema53 终态 `primary-message-v3` 逐条绑定真实Host tool invocation的ingest receipt、reservation、event hash、owner/Run/call/effect/state；Scope来自历史执行事实，不能取当前route head。非ToolInvocationFact没有Scope证明。原SDK causal source原样保留，新Host事实单列tool_scope_sources，并由终态envelope/child/source/registration hash承诺；读取时同DB快照再次核真实source。旧已持久v1/v2绝不回填，原v2默认编码路径/值不变。源码：execution/primary_history.py，memory/primary_message_{v2,v3,evidence}.py，conversation_registration.py。

SDK仅Procedure校验传allow_source_only=True。先复用 `_read_ingested_record` 核真实envelope/items/full或source receipt及单一admission模式，再核span、owner、实际Scope注册、terminal receipt。普通mutation仍要求full ingestion。没有改DDL、旧receipt/hash、M618 wheel或把tool消息变成分析任务。源码：backends/sqlite_v5.py。

新增四项边界控已写但未执行：Host历史v2组升级53后exact注册不变、scope source错owner/Run/call/state拒绝；SDK source-only公共消费/不产生full ingestion、伪receipt/foreign Scope/普通mutation门拒绝。对应 test_procedure_scope_sources.py 与 test_procedure_source_admission.py。这四项不得并入当前PASS数。SDK原fixture仅增加默认关闭的source-only样例参数，旧用例不改语义。

未完成：执行前工具/环境漂移物理0控制、同Scope再次运行拒绝、高risk Host链、未消费authority过期恢复、并发旧revision、首次UNBOUND草稿产品发现与失败归因。旧S3 SDK绿不代替这些Host边界。TC-HM04整项未完成，不能因正控通过默认合入正在运行的Host。

Hegel已固定独立SDK e500556、Host c0fbe30a并报告限定ACCEPT；不互相cherry，不各自分配版本或build。主合并manager/port/root/sqlite各自小段后统一后继包。我的新Scope source delta待主转Dirac，未声称独审接受。

## 复现命令与本地索引

既有解释器：`/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0614/venv/bin/python`。载体将Memory源码、Host backend和原H078/M618/S0313 target加入明确路径；不改installed。载体 `Host/.local-test-evidence/2026-09-06/procedure-scope/run_host.py`。

```text
<python> /Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir <新的ignored批次> --rss-mib 2048 --seconds 180 -- <python> .local-test-evidence/2026-09-06/procedure-scope/run_host.py <新批次绝对路径> tests/memory/test_procedure_scope_runtime.py
```

不覆盖默认锁；不复跑已固定绿。将来四项新控只选择各自新测试文件。SDK根 `/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources`，Host根 `/Users/denny/projects/simple_harness-corpus-clock`；下表批次均在对应树 `.local-test-evidence/2026-09-06/procedure-scope/`。

| 证据索引 | SHA-256 |
| --- | --- |
| SDK/sdk-r1/command.log | `b73d935be7a6cc24134823b43cc7e863cc1111d5c5c9b0e22fcf809a0dda241f` |
| SDK/sdk-r1/resource.json | `e3de34dbdcbd0fbc72be71c045bdd7de716041dc6136c7bfedf662b45ac56ec9` |
| Host/host-r1/command.log | `a67a3d72c5c62dc727dbaf48575539a7a02b22b6114580cf017383793594abf2` |
| Host/host-r1/resource.json | `d240518488b06667db822589d8784c890cf26c891257e309a656bb9fe558a3df` |
| Host/host-r2/command.log | `a6daa4624b5faf94434af595aa3e2812b5772ab93480c8c60928fcfbe8d2f8fd` |
| Host/host-r2/resource.json | `e7e70c9648eba8c1a0d801b74ecc9f1b976944446dfb3c3646aa7a9536d89c90` |
| Host/host-r3/command.log | `f3d0b73a9aae1d4210994ca5b33875a746c8e75a66288774727c297db71e4a5a` |
| Host/host-r3/resource.json | `9065c958832aa33940b3fa7c8f24874c0fe8b8cda4e9f7e7cf18070dc3e13f46` |
| Host/host-r4/command.log | `107608d397c9a7a41d86a165923a4192d4f29bca1504ae4c6f0c7a0c98bd8308` |
| Host/host-r4/resource.json | `3de28a0c5bccaf2dab3744c681de9b9b8fda517c5e39f92398e669614d70089e` |
| Host/host-r5/command.log | `1127debac907bee3d83067e809b864411b6607c4516f8925f970fd9781b4e044` |
| Host/host-r5/resource.json | `09a4419c2b232ca04ef0099c40c275e6df2e53408df3cb591d083769911050c6` |
| Host/host-r6/command.log | `1e725cac59b380c4fa3b76fcb9e83143c19e5aea7f7331385d168d034ed337f0` |
| Host/host-r6/resource.json | `e0ea5b511eb92341b9d4a0a11918881bc27b139eb94559ea7a0165419b8d4052` |

## 来源边界四项后续结果（2026-09-06）
四个唯一新控已经分别通过，不合并为同一源码全量测试。r1载体缺basetemp父目录四setup错误；r2 SDK主体fixture提前source-only注册被拒，两项Host通过。r2 fixture异常曾留下连接线程，约65秒向本批SDK child18495发SIGINT后退出，不能称该SDK批正常成功。r3用真实初始mutation先建立owner并保证异常close：一通过、一错误消息断言失败；r4只重跑后者通过0.29秒。产品拒绝未放宽，普通mutation仍要求full ingestion。
- boundaries-r1：PG18442 exit1，0.867s，remaining=[]，cleanup=None。
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r1/command.log` SHA256 `e0d7c01c9ea891ac1091ac1d4dd121b1edd51ac7d5947658dcfeebdd5b335757`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r1/resource.json` SHA256 `81c6e3688d9cfae407d697377fbe1be192d97637eea9873943d316826b31f1f8`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r1/source-state.json` SHA256 `aed0bb0e70ba2bb95c1d05ed3ccfb766a48970da17502a5ee7828b61e95aa438`
- boundaries-r2：PG18487 exit1，66.89s，remaining=[]，cleanup=None。
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r2/command.log` SHA256 `d9e04720f02bb988fddceb3f40827f9521592aeb4907633576936d83dbdf0216`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r2/resource.json` SHA256 `033745f0c3dc783c80bcb87a423f8d53cad384f0298401ce6a231b1b3f668328`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r2/source-state.json` SHA256 `aed0bb0e70ba2bb95c1d05ed3ccfb766a48970da17502a5ee7828b61e95aa438`
- boundaries-r3：PG19273 exit1，0.861s，remaining=[]，cleanup=None。
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r3/command.log` SHA256 `91bac627a5f3e9bb9055218f97dd08c0fff4cb48d3f9e8a64d5acb45a2bdc6c3`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r3/resource.json` SHA256 `dd860f60621cb4aa84ed46d47eaccb0e8b6c07ac2f4ff384819fe6926611b585`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r3/source-state.json` SHA256 `377061c1a313a98eca737f05d359567553140d77d911b6768d53f312db92a5d9`
- boundaries-r4：PG19716 exit0，0.663s，remaining=[]，cleanup=None。
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r4/command.log` SHA256 `52277947e96ab119604e108eb04d6532830234545f473d42d01ab753182e1f8b`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r4/resource.json` SHA256 `66eabfd8819b115a27ef9a0e0578b45d62323f0dd43a9fbfafe2cbc0e36e8309`
  - `.local-test-evidence/2026-09-06/procedure-scope/boundaries-r4/source-state.json` SHA256 `b240171b44ce37bdce8be185fe705fad4be51906d71f90632a3224616e0f807a`

source-state保留实际WIP成员hash；不能把同期尚未执行的恢复路径算作通过。新恢复范围见[RECOVERY.md](RECOVERY.md)。
