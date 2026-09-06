# C07-06/14 正式评分相位接线

2026-09-07。继承9810236f已接受的6个唯一helper控制；本增量源码待独审，**只新增1项actualmain组合控制，NOT_RUN**。原H079/M619保持，不换主新H0710，不重测6绿。不改Carver C05 history_reader，不改主review_packet修复。

## 实际链与身份

评分进程仍只读input.json/setup.json。编译端验证原recent_messages ordinal/role/content；setup Provider构造只接原两条角色内容和实际配置模型ID，不收问题/gold/干扰库内容。它在进程内127.0.0.1临时端口返回一次固定的原assistant消息，**不是语言模型**。实际main的Provider resolver、价格预算、ProductProviderAdapter/physical guard、同一SDK stack/Host runtime、原terminal sink均保留；没有测试helper假终态或模型自行签权限。

1. 只登记process-only `corpus-recent-fixture`（priority2），此时实际远端Provider尚未登记。原长期干扰seed准备照旧。
2. 真实enqueue原recent USER；通过实际main runtime、完整公开group校验核原assistant来源。原USER/assistant各自角色独立，不拼接成当前消息。
3. 用同一个public trace collector记录该setup Run到 `setup-recent/`。必须完整审计、TERMINAL、唯一succeeded Provider invocation、handoff_attempt1/rehandoff0，且真实start binding指向fixture Provider、相同Run/model。HTTPresponse的真实provider_request_id保留实际wire-request SHA，与公开SDKresponse再对照；本地counter不能代替SDK证据。
4. 关闭并join本地server/连接任务，确认resolver无active Provider，再通过原process-only registry API登记 `corpus-real-provider`（priority1）并reconcile。旧fixture binding留作历史审计身份，不改TOML/keychain/旧Run；下一Run公开freeze选择真实Provider。
5. 当前USER原样另排一Turn；同库原Context读取最近组。实际评分Run绑定必须为真实Provider，与setup Run不同。顶层trace/metrics仅来自评分Run，setup统计另存 `setup_phase`，不把本地fixture调用计作真实模型请求。

两个ID对应不同冻结provider binding；不靠删除archive隔离计分。失败时精确按delivery_key寻找对应Run，避免两Turn导致旧单Turn失败采集逻辑失效。setup失败/不完整时无评分Provider登记/调用，已有source/SDK事件保留，单独phase.json明确NOT_CONFIRMED。进程当前仍fresh-only评分流程，不宣称中途crash的跨进程相位恢复或自动重发。

本地fixture HTTP仅收一个受限POST，2MiB body/5秒请求读写上限，非生产模型预算。key为显式无权限fixture占位值，headers不落盘。0tokens只表示本地固定响应未运行模型；不当作远端计费价格。关闭由finally监督，不起额外进程/线程。

## 唯一新组合控制（待跑）

`tests/quality/test_corpus_c07_phase.py::test_actual_main_recent_phase_then_separate_scoring_run`：选择C07-06，复用官方compiler拆分输入与实际main factory，真实loopback HTTP只生成原recent assistant；评分端HTTP在测试载体固定，禁止其它外网与WeMM加载。校验：

- 起跑即校验本树public candidate identity的版本/wheel SHA与实际安装H079，Memory M619；不借旧H078硬编码Runtime helper。
- setup公开终态/trace已确认且本地server停止之后，评分端才收到请求。
- 评分实际HTTP消息恰含原USER、原assistant、当前USER三条非system角色，干扰source没有外泄；原input.json字节不改、oracle文件不读。
- 两个Host/SDK Run、两条Provider invocation分离；顶层评分trace只有评分invocation，setup独立一条；所有owner/server清理完成。

这是一条确定性实际组合，不评回答质量。14共享同一分支/合同，其原recent helper已绿，此次不另跑旧14控制。官方20评分可执行状态须按组合与后续真实trace分别判，不因去掉原BLOCKED分支就报告20-ready。

待跑仍用原CONTRACT的解释器/target/default resource runner，新ignored evidence目录 `.../corpus-c07-prepare/phase-r1/`，pytest仅选择上述一个node。**主/Carver交槽且Dirac源码接受后才运行**；本次文件写入未启动任何资源进程。
