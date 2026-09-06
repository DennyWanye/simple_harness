# 非SELF本轮输入：来源、SDK消费和物理交接结果

最后更新：2026-09-06。Host产品0cf37197，严格kind窄修4405a2ac；契约3d73e606。来源片已限定ACCEPT；最新Host c2fd8fcd + Memory a8c8c38的9新增源/实际组件结果也获Dirac最终限定ACCEPT，无新增P0/P1。

- r1：5FAIL/1.27s，全在fixture误用intended_audience=external_party被真实枚举拒绝，未达来源逻辑。原红保留，58247111只改fixture为external。
- r2：5PASS/1.94s。真实签名控制→S1/turn同TX→exact事实回读、政策换代后input拒绝/origin不变、原文本/声明重放冲突、无声明/无live认证/错hash/pointer拒绝、旧S1不升级、after_evidence_insert故障原子回滚。
- r3：2PASS/0.90s，5旧绿deselected。新增实际重新签名connection后原receipt/fact/admission lease保持，旧connection失效；kind=list稳定拒绝。合计7唯一，不将首次连接重读称进程重启。

命令：primary-m0615/venv/bin/python -I -B primary/scripts/run_resource_bounded.py --evidence-dir <本树>/.local-test-evidence/2026-09-06/nonself-input/rN -- <同Python> -I -B <本树>/.local-test-evidence/2026-09-06/nonself-input/run_source.py <rN>/cases；r3追加 -k 'resigned_reconnect or nonstring'。载体路径前缀见该ignored脚本，复用primary-078618/installed的H078/M618/S0313，Host从本树源码加载；没有新venv/install/模型/native或全成员扫描。

r1 PG6812 exit1/remaining[]；r2 PG6904 exit0/remaining[]、elapsed2.63s/peak186848KiB；r3 PG7027 exit0/remaining[]、elapsed1.527s/peak168896KiB。全部cleanup_error=null；默认锁释放。原log/resource/cases仅ignored。

## SDK消费者前一批（8个唯一，绿色复用）

7fa2c96 起新增 SDK item-level 输入 API；31e2/2e6 修 owner及慢读后原claim复核，793a916/68a6e065再绑定完整 configured principal。consumer-r1为4PASS/4FAIL3.30s，4红均为真实异步RecordingSink尚未drain就读到0条；3ea233fd只在测试使用公共manager.close排空sink，consumer-r2只重跑4红：4PASS/1.68s，4旧绿deselected。实际允许两kind、原history拒绝、source suppress/reopen、foreignRun、伪item、取消/读取错误、owner拒绝、真实stop期间origin变化已覆盖。该8项本轮不重跑，旧sink失败保留；未当作physical证据。

## 本轮新增9个唯一（分批，不累加重复执行）

源码：Memory a8c8c38（批读995eacd、有序request hash4d76bb9），Host生产430f0e4c（含d8f37cde physical），测试c2fd8fcd。仅源overlay，不是新wheel installed身份。

| 批次 | 实际结果 | 范围/失败原因 | PG / exit / 清理 |
|---|---|---|---|
| new-r1 | 5PASS / 2.20s | public ingest placeholder拒绝后合法owner允许；当前+旧S1+unknown recall/short批读隔离及顺序hash；实际调用审计/缺能力/reopen；真实task.cancel；settle写失败pending | 10680 / 0 / remaining[]、cleanup null |
| physical-r1 | 4FAIL / 2.70s | fixture误用pytest.setitem，真实ServiceContext.get没有default参数；未达业务 | 10822 / 1 / remaining[]、cleanup null |
| physical-r2 | 2PASS、2FAIL / 3.45s | 实际正常1次MockTransport send和actual request字节篡改0send/确切mismatch通过；另外两条内层policy已经拒绝0send，oracle期望的内部cause被原policy包装 | 11015 / 1 / remaining[]、cleanup null |
| physical-r3 | 2PASS / 2.42s，2旧绿deselected | c2fd8fcd将late动作置于完整真实policy read返回之后，最终原token/claim分别报binding_stale / primary_input_claim_changed_during_check，均0send | 11195 / 0 / remaining[]、cleanup null |

最后组elapsed3.22s、peak377488KiB、minDisk5896MiB。资源均使用主默认OS共享锁；已释放给主native，不再占槽。

真实生产链为 signed控制→原子S1/turn→实际ForegroundRuntime/SDK durable reservation→main checker→公共Memory批读→Host sidecar捕获→ProductProviderAdapter构造器注入原guard→MockTransport；负控不替换生产guard或可见性结果。输出共同政策与当前text确实到达物理请求。不是网络Provider/模型质量/native结果。

新控制中的旧S1为真实Host admitted源；recall/short两tuple是明确unknown引用负控，不冒充已物化typed/short隔离。Scope/旧健康家庭真实记忆及240双项联测、最终输出内容是否遵守用途尚未证明。

## 复跑命令与剩余范围

所有新raw位于本树 `.local-test-evidence/2026-09-06/nonself-input/`，只保留原log/resource/cases。公共载体借用主 primary-078618/installed的H078/S0313；Memory从独立 current-input/src加载，Host从本树backend加载；无新env/build/install、无旧全成员扫描。

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python -I -B /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --evidence-dir <新ignored批目录> -- /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python -I -B /Users/denny/projects/simple_harness-typed-recall-context-use-full/.local-test-evidence/2026-09-06/nonself-input/run_new.py <新批目录>/cases <test selectors>
```

new-r1 selectors为 `tests/memory/test_current_input_consumer.py::test_ingested_placeholder_cannot_claim_current_input_namespace`、`tests/memory/test_current_input_consumer.py::test_input_batch_exact_exception_does_not_spread_to_old_sources_or_unowned_refs`、`tests/memory/test_current_input_audit.py`。physical-r2为`tests/memory/test_current_input_physical.py`；r3追加`-k 'policy or claim'`。已绿不需例行重跑。

已实现SDK实际用途观察、调用审计持久接收和实际physical最终claim/request检查；Dirac最终限定审已ACCEPT；待Singer小源码组合与主后继版本/installed/native验证。本片仍仅一个完整当前USER或显式public材料item，240独立USER+材料双项未接；并非最终受众正文授权、全操作审计或program完成。没有修改冻结fixture/数值阈值/旧来源hash/pins/轮子/主工作树。
