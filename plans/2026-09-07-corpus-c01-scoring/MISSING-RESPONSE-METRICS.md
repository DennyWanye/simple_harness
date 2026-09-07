# 缺少模型响应时的质量指标

2026-09-07，产品与受影响控制 `ebd81721`，H0710/M619/S0313。r4公开Provider attempt可完整读取，但响应不存在；旧报告用attempt读取完整性决定exact预测指标，误显示extra/count=0。

现改为响应、trace和类型提议全部完整时才给exact predicted_types/count/matches/extra，否则为null；独立lower_bound保留已观测部分。已知handoff次数仍可为1。失败用例仍进入原required denominator，credit=0，不美化失败分数。旧r4 packet不覆写，后继报告应用修正。

仅扩展原真实SDK failed控制，在同一Run内先核“完整attempt+缺response”，再保留原projection读取失败断言；没有新增模型请求或伪造Provider response。Dirac源码窄审无确定P0/P1。唯一affected selector：`tests/quality/test_corpus_scoring_trace.py::test_actual_provider_attempt_survives_missing_transcript[failed]`，**1 PASS / 0.91s**。未重复nonterminal或其它绿套件。

默认共享锁1024MiB/120s，PG78605 exit0 / 1.515s，峰161424KiB、minDisk4752MiB，remaining[]、cleanupnull、stopnull。仅本地运行/计分边界通过，不是模型质量或全计划完成。

| 本机证据相对路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-missing-response-metrics/r1/command.log` | `85c3e34df3337375b023792993d5880ace51098d60f8a6d4b62bb027a039340d` |
| `.local-test-evidence/2026-09-07/corpus-missing-response-metrics/r1/resource.json` | `672ae11666125ff2cad677ba6aaa6031ad47db11f5196d41ad104e97168a82d8` |
