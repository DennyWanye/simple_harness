# Procedure v5 schema与真实分类

更新：2026-09-06。固定源码85a19260，源码及三条真实结果获Dirac限定ACCEPT；H078/M618现有installed target，未构建新SDK。

## 真实问题与修改

v4真实gpt-5.5首个请求 `req_a00ec8a6126e266126cfad60` 正确识别adoption并复制步骤，但原superset工具schema允许同时填入episode/semantic/prospective正文。v4严格编译拒绝operation_fields_invalid，0操作、no_mutation。因此语义识别正确也不能实际产出可用Procedure。

v5使用按memory_type分支的anyOf schema，仅允许匹配类型正文；Host编译器同样拒绝跨类型字段，未放宽来源、引文或授权检查。旧v3/v4完整协议和请求版本选择保留；v4只抽取复用编译函数，v5传入原始请求，不通过改写版本或request_hash伪装旧请求。默认新worker使用v5，已保存请求仍使用自身协议。

## 已执行验证

新源码3项控制3PASS/0.91s：schema/旧协议选择、实际来源检查与跨类型正文拒绝、真实已保存v4响应在v5配置下跨失败恢复且零额外Provider。PG4915正常退出，remaining=[]、cleanup_error=null。

真实模型三条独立请求，沿生产v5 system prompt、工具schema及编译器；分别提供“本人明确采用”“过去步骤叙述”“未决定采用”的原文，不把期望状态放入模型输入。

| 场景 | Provider request ID | 时间 | 实际编译 |
|---|---|---:|---|
| 明确采用 | req_827b30d8440a5c3e31aec9fe | 11.339秒 | Procedure ACTIVE，adoption，拒绝项0 |
| 过去步骤 | req_db817d54584c03ff519d659b | 10.814秒 | Procedure DRAFT，reported_steps，拒绝项0 |
| 尚未决定 | req_630500c5fe5ef862e2b89e76 | 12.811秒 | Procedure DRAFT，uncertain，拒绝项0 |

PG4986正常退出，35.863秒、峰129200KiB、最低磁盘4718MiB，remaining=[]、cleanup_error=null，共享锁释放。修复后的adoption是针对真实失败的必要复验；其余两条首次调用，没有自动重试。

这是独立真实模型分类和编译结果，使用SDK真实HTTP adapter；未通过Host持久executor、未写实际Memory库，不代替Runtime审计、原生UI、Scope观察/自动激活或240条批次质量评测。仅3条不能代表分类总体准确率。

原r1 BUSY75无子进程。r2第一模型响应已保存，但测试载体序列化RejectedOperation失败而exit1，PG4516已清空；后续只离线重编译该已保存响应为JSON，没有重发v4请求。两个未发送的v4输入改在修复后的v5验证。原始失败和响应均保留。

## 本地证据索引

根 `.local-test-evidence/2026-09-06/procedure-real-classification/`，原始文件不进入Git。

| 文件 | SHA-256 |
|---|---|
| run.py | 5a2041879fbbc1402ea0b6e6fb418e0d58c8d00cb38b7ea0d99b96b409aad6a2 |
| source-r2.json | 2f62befbf9a8bf306de3dbd3a09964a69ab03787c98dd0804f1abdd33938a210 |
| r2/1-input.json | 378a415e1f3000cdce400b27d61571bcca6a4687c29a525eeac5042cef1e6da7 |
| r2/1-response.json | 5d8c7ff75f3f04412d064a6b51f879eb738193f0d92d037e87a73913f845d7e4 |
| r2/resource.json | 30f2f1b38738045d29c11f4f7eb2f3f51fbb9adf2ef9246b5db5dd43f6d2eea3 |
| run_v5_controls.py | 6a011dba45d7e610337ecd8b6b56943f652fcd34a1aed03e3e1174bd058c79e7 |
| v5-controls-r1/command.log | 7e8cc7c130c0dbcf7082590c5a435ec73188f210f9fc63f1c948b5acec21818e |
| v5-controls-r1/resource.json | c1a0030f338071b691efe9f6d8f6ffd756619299b4ee93de058aec69904a7f10 |
| v5-controls-r1/original-response-compile.json | 5cc9ffe0b1cad72f990fabef520b80e65645fc4355fbf54a7af5b04db23dd1e6 |
| run_v5.py | 056f5d6f6d544d6d59982dd30a3d816c11a173eef3e3a8d007d12d1ab51d30f9 |
| v5-real-r1/summary.json | a694cb402aeb13568d15daa616effb60f349a32f0cd389443870349319041cc7 |
| v5-real-r1/resource.json | 471f9ae411171f20ace40f26785d8421083b363bf567e446da6c129a7b03b163 |
| v5-real-r1/1-input.json | cdb1d77b5e6dc4291552f1196cc6b9352beed18dd5d97b12ff13d2de7e7c65da |
| v5-real-r1/1-response.json | 7cf5b8fbdd59c59e21e674158110b426311b5b939c33c90cf5f115ce2a745aa8 |
| v5-real-r1/2-input.json | 89935d854922b8efa2adb71494ab09542dcc326df9437ad0e01a28fbf23000d8 |
| v5-real-r1/2-response.json | 770df6c3d8e44cf910fc7ca01f7d46ce24e68a95c34e24d1ff9b3dbbdda2e392 |
| v5-real-r1/3-input.json | 29674e864cd4ce11cb2aa2aa0b271985475669659c604e2db8f01b007a9aceff |
| v5-real-r1/3-response.json | 62c8c16e9e474ad67591d72d9bc66f1cbd00b665466ef0347fb0586bae268755 |
