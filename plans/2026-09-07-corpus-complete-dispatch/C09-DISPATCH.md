# C09 shared dispatcher：原历史退役/修订

2026-09-07，源码 NOT_RUN；只接共享入口，不改 SDK/fixture builder，不运行资源。

依赖主已审 `corpus_c09.py` / `corpus_c09_prepare.py`（含 bb0c 修正）及主 `3ea7e214` labels/新 child。它们没有复制进本旧 base；本提交由主完整 candidate 消费。labels 为 `old-i / successor-i / unchanged-i`，全部来自真实 old/new mutation receipt.operation identity，不是模型目标或 gold。

- `prepare_batch` 对 C09 仅开放 `set(CHANGES)`；13缺 Procedure 来源仍拒绝，不能用 scalar替代。
- worker 在 main 初始化前 `compile_c09_setup` 核原 setup/hash/clock；scalar/current 输入与原 no-recall 流程不变。
- 真 primary 初始化后 `open_c09_fixture` 在同 Memory store 建原 CREATE job和公开 SUPERSEDE/REVISE；AsyncExitStack 退出关闭临时 fixture authority/manager，再进入原 main production manager/foreground。
- setup_receipt 保留原S1、labels、ingestion/application/request、initial_plan/plan、old/new receipt/ref；graph不出站。所有这些都是设置证据，不复制到评分 USER/SYSTEM。
- 评分仍原真实 no-recall path，不强迫模型 tool-call，fixture执行次数不混评分物理HTTP；`review_packet` 标记 `NOT_EVALUATED_PARTIAL_C09_BATCH`，原gold仅父进程事后计分。

主已有唯一新增两组：`backend/tests/quality/test_corpus_c09_phase.py::test_actual_dispatcher_superseded_setup_then_isolated_request[C09-02]` 与 `[C09-20]`，分别纯退役与同一plan原子双修订；由主统一执行。本叶不重写该child、不复跑原20准备控制、不假称正式自然模型通过。
