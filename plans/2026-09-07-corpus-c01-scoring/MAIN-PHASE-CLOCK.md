# 主候选：修订、可信日期与最近对话评分接线

2026-09-07。H0710/M619/S0313、完整主候选及其真实vendor安装来源，source `0aa8e029`（C01叶2c59dcbf、C07叶c73feaff/4dd75040）；后继`61f474e5`只纠正C07测试的“未尝试加载”错误断言。

分批3个唯一控制通过：

- C01-06：真实SDK job APPLIED/ACCEPTED创建旧称呼，再经原action authority同ID REVISE；独立重开读双receipt，public typed recall只选revision2新称呼。
- C01-11：真实SDK/Host冻结Context接同一业务clock，原问题不改；第二轮跨UTC日期边界时可信today正确，用户引用日期不能改变Host clock。旧snapshot不重写，Memory公开evaluated_at同源。
- C07-06：actualmain用真实loopback fixture Provider完成原recent USER/assistant组，SDK终态及binding确认后关闭fixture，再选择独立scoring Provider/新Run。实际出站只含原recent与当前USER，无干扰记忆；两个Run、请求和统计独立，setup调用不混入评分数。评分响应受HTTP控制，无真实模型质量证明。当前main的真实出站system也观察到scenario clock的today=2026-09-06。

r1仅上述三项：**2 PASS，1 FAIL / 8.77s**。C07业务及精确输入已通过，测试末尾`assert not loads`因两次可选embedding尝试被guard拦下而失败；这不是模型已加载。r2只复原C07红，guard仍禁止原loader，改核每个实例_model为空，保留其它全部断言：**1 PASS / 7.28s**。C01两绿未复跑；原C07分支先前缺资产/安装来源不匹配及本次oracle失败保留，不拼成一次全绿运行。

r1 PG80368 exit1 / 9.881s / peak438176KiB / minDisk4601MiB；r2 PG80594 exit0 / 8.467s / peak452624KiB / minDisk4594MiB。两组remaining[]、cleanupnull、stopnull，默认共享锁2GiB/180s。原diagnostics_snapshot coroutine警告保留；可选embedding降级不计WeMM加载成功。防熄屏持续。

C01-06/11评分session仍须解除原block并接专用prepare；C07正式相位限定通过，不是240质量/原生或全计划完成。真实服务的gpt-5.5 model_not_found仍是独立卡点。

| 本机原始证据相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-main-phase-clock/r1/command.log` | `c2c84cc5ee54424c641aa6df01d659307046b3af325aa42503f0e3dfff54f441` |
| `.local-test-evidence/2026-09-07/corpus-main-phase-clock/r1/resource.json` | `76792101b3d6f33ba1cafea334c0a6ba99bd06ca873bd18d52ae4fa91b2f5eb1` |
| `.local-test-evidence/2026-09-07/corpus-main-phase-clock/r2/command.log` | `fcf605a12d64a63637061c539a7f7758bd6394e7d38897bece5bed25282aaf15` |
| `.local-test-evidence/2026-09-07/corpus-main-phase-clock/r2/resource.json` | `87e397c233b383ebedad2595246782e81e46761a817741ec788f5b27f7496046` |
