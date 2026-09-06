# C06 个人事实与 Procedure 来源准备

最后更新：2026-09-07。

H0710/M619/S0313 installed，原源码32eb61f7及4266c4b3、测试身份修正9f7569f9（main abf1b43c）：11个唯一控制分批通过。r1 首条 C06-02 的真实 job 已 APPLIED/ACCEPTED、两种来源可见，但测试假定 plan 操作顺序为 S/P，实际 canonical 顺序为 P/S，**1 FAIL / 0.70s**，-x 停止剩余。修正以真实 operation_id 核类型和 P.steps，不改或重排 SDK plan。

r2 仅原红、尚未执行项及新增七项，**11 PASS / 4.88s**，无既有绿项重复。覆盖02/03/04/06/08/09/11/12/15/16共10条语义与 Procedure 来源，加一编译控制。

真实 Host S1→公开 ingest→durable job→APPLIED，精确 payload/type/active/source quote/ref/hash 回读；同字节不同 S1 不能满足旧节点来源，foreign owner 不见本人节点，冷重开读回相同状态且不再次 analysis。没有伪造 foreground Run/history/terminal；ACTIVE Procedure 是原 setup 的旧程序夹具，不是当前执行成功或新授权。

边界：10/20 仅来源准备控制。跨 Task、非 SELF 输入、最终物理召回、模型质量和原生链路仍需独立完成，不能折算240条通过数。后继六条条件描述源码尚未在本记录验证。

命令：当前 installed target 优先 PYTHONPATH，共享 `run_resource_bounded.py --rss-mib 2048 --seconds 180`，两批均 `pytest -q -x backend/tests/quality/test_corpus_c06_preparation.py`；r2 源码显式保留旧3 selector 并另列7新参数。

r1 PG87793 exit1/1.549s/peak155088KiB/minDisk4199MiB；r2 PG88125 exit0/5.804s/peak167072KiB/minDisk4153MiB。均 remaining=[]、stop=null、cleanup=null。原始失败保留，不是资源拒绝；防熄屏保持。

| 本机证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c06-prepare/r1/command.log` | `b86d07068de7c17e17dc8329b6795c930b8a405708fc0b6c45c0d658d9147724` |
| `.local-test-evidence/2026-09-07/corpus-c06-prepare/r1/resource.json` | `a224763170f9f63dd45cf935eba96ec295c620f148c2983cea785813699c379e` |
| `.local-test-evidence/2026-09-07/corpus-c06-prepare/r2/command.log` | `762454c6fca64707793948571ba24c903bf1b237aff6d2cbcc4080b116f618be` |
| `.local-test-evidence/2026-09-07/corpus-c06-prepare/r2/resource.json` | `5424c0b9c35998974609bfd99638b86b22b37013ab7e91d9f95fb3c0a3bfef48` |
