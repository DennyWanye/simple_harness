# 原生r12：新进程首次短期查询通过

最后更新：2026-09-06。Host `33809aae9277e77644a7520ca39a72c392d32f3d`，固定H077/M617，既有18ec7194原生bundle，实际WeMM与gpt-5.5。包含同实例startup加载+编码预热、FTS+VECTOR、已审closure/resume来源修复。

**本场景PASS**：只发送新进程第一条查询，没有手动试探查询或失败后重试。UI核对并允许short=true/types=[]/无糖茉莉茶。工具 `call_odvvmOswIju9KF6S3dVDeZxw` 成功，receipt `90818989-6343-5477-96e8-8f1d60f026c9`，effect `effect-91a07a4b34d8854fc15ae174b15b1b514cd3e835c0135906440d6b5d3b83feae`；三个 `recall-item:61b2d6748671bc3bd39737ea:1/2/3` 实际可见。模型最终回答“最初的测试名称是青竹九月，口味是无糖茉莉茶”。

关联Host Run `395d23b0-91ac-5e57-bfef-a26f5736aa70`、SDK Run `product-sdk-9532db97e22b0b08df703a0530e578e155260ddcd95e6b5584b5e56cfba4fa87`。成功截图同时显示本次工具及最终回答；原历史已有上次暖态答案，因此不单凭答案文本证明查询，必须结合本次新工具回执/片段。此为修复后原生首查场景，不算独立质量轮或240分母。

## 性能和资源边界

启动load阶段8401.991ms、prime阶段1543.575ms（含队列等待），10:21:33.078 `p4_embedder_ready`，早于首次Run10:22:06.218。固定预热文本不含用户数据，向量丢弃、不入Memory。工具attempt从10:22:53.922711到10:22:55.439683，共1516.972ms；它包含Host核验和记账，不是SDK检索耗时，更不声称整个工具在1s内或p95达标。原1s检索预算没有扩大。广泛输入、数据规模与p95基准仍待原计划验收。

场景结束后UI空闲/排队0，无Scope的closure为clean/provider_calls0；这不替代新的Scope closure原生旅程。正常CmdQ后PG96027/native96032，runner exit0/parent0，159.382秒，峰1328864KiB，remaining=[]、cleanup_error=null；最低磁盘4195MiB，退出后5219MiB。没有重复旧长期/遗忘/暖态绿色场景。r10/r11失败记录保留。

| 本机证据（相对候选根） | SHA-256 |
|---|---|
| .local-test-evidence/2026-09-06/native077617/primary-ui-sj_q48g6/launch.json | fa3dbea749d032bcabaeae274c98967278d3a67b50511d529ff7bc84b7faa503 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-sj_q48g6/native.log | 5659271d7b1411ec941aa3bf28194c4ea440339cf7e762da46d8e3aa7746a6a5 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-sj_q48g6/first-query-success.ax.txt | 3608e9002d46851fd45a4cb0779e72cfe96291dd5835eef50f8c1267060571ec |
| .local-test-evidence/2026-09-06/native077617/primary-ui-sj_q48g6/first-query-success.png | fda517b2a8da4e7e84d0f2b9610f34374c97fb2b67a1f5eeedae8031d762794d |
| .local-test-evidence/2026-09-06/native077617/launch-r5/resource.json | c9a1c4b0d3b954bd6b70cc09ae685230ae7ff10e53ec2683f0f3082c9d551d5e |
