<!-- Updated 2026-09-06 -->

2026-09-06 原生r12（Host33809aae/H077/M617）：同实例启动load+prime完成后，新进程首次short查询真实成功，无手动重试；新工具三条recall refs和模型青竹九月/无糖茉莉茶回答均可见。本场景PASS，工具总耗时1516.972ms不等SDK检索或p95；原预算未增，广泛性能/质量另验。PG96027正常退出/组清空，磁盘5219MiB；原r10/r11失败保留。[首查结果、Run与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R12.md)。

2026-09-06：WeMM startup warmup后继含一次无用户数据priming，同一实例／共享任务／encode队列，向量丢弃，无Memory查询或业务写入；新增warmup_state/is_primed及load/prime耗时区分。三项新控制3PASS，取消复用／成功幂等／失败显式重试有证据；SDK、1s预算、来源门未改，真实冷首次query待主验证。[详情](../plans/2026-09-06-short-terminal-source/PRIMING.md)。


2026-09-06：非空resume来源独立叶（baseff2f2009、产品0a52085e）以实际SDK tool effect或同TX后台response S1证明最新字段，依赖前缀按真实effect序号固定，并经当前Memory可见性和原最终Host fence。仅显式增加两种真实S1 policy；凭据/subject/hash校验不变。5个不同场景分批通过，Dirac源码与结果限定ACCEPT：tool/fallback后继Run物理MockTransport closure、同Run晚route不改冻结proof、来源遗忘撤下字段、写故障无半提交。H077/M616安装载体+Host源码，已合primary候选/未native；legacy、独立篡改、goal和ACK恢复专门控仍缺，不标完整Closure/compaction/program。PG95551清空并释放槽。[结果和保留边界](../plans/2026-09-06-closure-resume-source/RESULTS.md)。

2026-09-06 原生r11（Host464b86ee/H077/M617）：既有startup hook实际完成WeMM预加载，但新进程唯一首query的encode1.44s仍超1s预算；UI明确查询失败，未重试，不以r10暖成功替代首查。PG93935正常退出且组清空；仅清可再生Rust链接对象恢复磁盘4.15GiB，native二进制哈希/模型/证据/用户库不变。继续同实例编码预热。[本次失败与资源证据](../plans/2026-09-06-typed-use-primary/NATIVE-R11.md)。

2026-09-06：旧 short generation 复用不会加载当前进程编码器；Host WeMM 新公开 warmup 仅委托已有 shared shield load，原 startup hook 可调用，不重建实例或向量。两项新公共控制2PASS（取消／并发／零预热encode／失败显式重试）；未运行真实模型或验证main/native首次查询，SDK及预算未改。[结果](../plans/2026-09-06-short-terminal-source/WARMUP.md)。


2026-09-06 原生r10（Host0bedaa87/H077/M617）：FTS+VECTOR修复后的真实暖态短期查询成功，UI工具有三条recall refs，模型正确回答青竹九月/无糖茉莉茶。冷态首查仍timeout，单独保留失败并继续预热定位；不称完整short/性能/program通过。两轮均空闲，正常退出PG89400 exit0/remaining[]。已合closure九场景修复的原生Scope旅程另验。[实际结果与证据](../plans/2026-09-06-typed-use-primary/NATIVE-R10.md)。

2026-09-06：Host 显式短期 RecallContext／Plan 请求 FTS＋VECTOR，使 SDK 已检索的小 vector-only 组可参与统一预算选择；long-only 仍 FTS，预算及公开来源／privacy 检查不改。实际 installed H077/M617 新控 1PASS，旧 FTS-only 同源计划空结果反例与 Host fragments 来源核验均通过；未改 SDK／installed，真实 WeMM/native 由主后继验收。[范围与证据](../plans/2026-09-06-short-terminal-source/VECTOR-MODE.md)。

2026-09-06：隔离 `feat/closure-physical-request-guard` 自666b475b，main closure 默认专用物理守卫已接；以真实SDK终态/来源和当前Host披露准备完整attempt-input S1，与原reservation同TX保存，实际出站复核完整请求、来源与原head。9唯一场景分批通过并获Dirac限定ACCEPT：原8包含真实send/no_mutation和拒绝/UNKNOWN/取消，新实际CREATE_NEW→RESUME_EXISTING闭合复现读TX递归access写自锁后，4a86ecb0释放外层读TX修复，原访问审计保留。H077/M616 installed目标+Host源码，非native；已合隔离primary候选。非空scope.resume/无来源修改字段仍pending，完整Closure/compaction/长旅程未完成。PG88793 exit0/remaining[]，未重跑旧绿。[分批结果、失败与限制](../plans/2026-09-06-closure-physical-guard/RESULTS.md)。

2026-09-06 原生r9（Host fa7580b0/H077/M617）：生产Provider清理错误本次未再观察到；short祖先补齐和后台generation修复已经独审合入，实际WeMM生成active索引。真实查询首次超时，模型同Run重试后SDK审计used/FTS1/vector3，但UI最终仍答无片段，短期端到端未通过，返回链路待定位。现场保存后正常退出，资源exit0/remaining[]/cleanup_error=null。此前r8各场景证据与失败历史保留。[最新原生结果](../plans/2026-09-06-typed-use-primary/NATIVE-077617.md)。

2026-09-06：后台generation补充共享冷加载跨两次timeout恢复控通过，默认5s及WeMM不改；小公共embedder只证明worker串行、pending/维护时间及恢复语义。PG83348已清空，真实WeMM/native由主验证。[证据](../plans/2026-09-06-short-terminal-source/GENERATION.md)。

2026-09-06：正常 MemoryAnalysisLane 的 short worker 在 projection 后调用 SDK 公共 generation；cache 只在生成成功后确认，维护失败保留 pending，原 SDK 幂等负责同 lineage/manifest 复用。四个小 embedder 公共接线控分批通过；未改 SDK 或 WeMM，真实 native 仍待主验证。[接线事实](../plans/2026-09-06-short-terminal-source/GENERATION.md)。

2026-09-06：Host short indexing 补齐真实 terminal S1 祖先，仍由 M617 原 suppression 校验；不增加 conversation item、分析 job 或 grant。r8 最终副本公共 reconcile/reopen 恢复 3 chunks，旧注册/遗忘/认知/job 行保持，新增三个来源/遗忘/重开控分批绿。仅 Host 接线局部验证，Dirac限定ACCEPT并已合候选，真实 generation 和 native 召回未验；Memory 制品不变。[详情](../plans/2026-09-06-short-terminal-source/RESULTS.md)。

2026-09-06：隔离 Provider cold cleanup 窄修（产品 f0f72650、测试79508593），仅空 binding/authority 注册跳过终态后清理。真实 production resolver/port 的受影响冷恢复单控 1PASS/4.32s（H077/M616），核实 FAILED 持久终态、零 Provider、effect gate 释放、已有 binding 内部 KeyError 传播。旧测试使用 no-op ProviderPort，未覆盖这个生产回调，不能由其旧绿推断修复已验证。本叶已合候选、Dirac限定终审ACCEPT，native复验未完成；H077制品及原库未修改。PG81519清空。[分批结果](../plans/2026-09-06-provider-cold-cleanup/RESULTS.md)。

2026-09-06 原生r8：H077/M617/Host2c8c57c6在原userdata真实完成新偏好写入、长期召回命中、UI遗忘后同条件零命中，Cytoscape两节点/筛选一节点可见。旧任务已FAILED但后置Provider清理仍报KeyError；第11完整组后短期投影MemoryCorruptionError，窗口外短召回未通过。正常CmdQ后runner回收残留，资源125/最终组清空。[原生范围、Run与失败证据](../plans/2026-09-06-typed-use-primary/NATIVE-077617.md)。


2026-09-06：候选固定 H077/M617/S0313，授权过期与冷启动修复8cec2353已合；主vendor小target离线安装和3项受影响身份/锁校验通过。旧功能测试按原组合复用，新组合native尚未验收，用户主树不变。[接入与边界](../plans/2026-09-06-typed-use-primary/COMBINED-077617.md)。


## H077 public expiry terminal and cold Host recovery — isolated leaf

From762af1ab, Host reads exact public SDK terminal metadata instead of SDK-private
terminal SQL. Only a bound failed Run with the exact missing-proof error invokes
explicit public eligibility/recovery; ambiguity and other errors remain rejected.
Actual old075 authorization expiry+Stop -> full close -> new077 Host/SDK stack ->
lease reclaim -> real FAILED receipt -> second new-stack exact read passed, with
zero additional Provider sends/context reprepare. Two separate error-dispatch
negatives passed:3 unique new controls total. Missing process-local old tool
registration is skipped only during cleanup after durable verified terminal;
no old grant is reconstructed. Existing-record cleanup errors still propagate.
Pre-recovery Host read and SDK transaction are not cross-store atomic; original
Host final lease/generation fence remains. No original userdata/native/main merge
claim, and077 artifact is unchanged. [Contract](../plans/2026-09-06-expiry-terminal-public-host/CONTRACT.md),
[results](../plans/2026-09-06-expiry-terminal-public-host/RESULTS.md).

# Memory SDK 边界与 Host 接口契约

最后更新：2026-09-06。Host默认Memory builder已接7.3公开升级链；实际installed M616旧库→M617升级/重开保留属主与升级回执，新控1项及空库/未知库2邻居分批通过。原生userdata未升级，完整consumer/native仍未通过。[升级边界与证据](../plans/2026-09-06-prospective-source-audit/HOST-617-UPGRADE.md)。

最后更新：2026-09-06。Host明确接入M617 V2/settle观察，H076/M617实际installed组合4新+4受影响检查共8PASS/2.91s，无源码overlay/模型/native；终局真正消费、跨库恢复及完整scheduler仍单独验收。PG71205清空。[边界与证据](../plans/2026-09-06-prospective-source-audit/SUCCESSOR-617.md)。


最后更新：2026-09-06。独立Host52/H076/M617 consumer新6控分批通过；修复dependency嵌套immutable payload canonical转换与52 timer cursor误读旧表。真实public Manager旧catalog7.2→公开7.3升级、r2 not_required恢复/观察绑定已验；原红保留，无Memory SQL业务读取，非installed616生成旧库。最后PG74424exit0无残留，Dirac限定复审ACCEPT；H077组合/native/event/presentation未验。[结果](../plans/2026-09-06-prospective-consumer-m617/RESULTS.md)。

最后更新：2026-09-06。原生r7包含已审租约修复，冷重建仍在Host读取实际SDK终态时因事件歧义拒绝，STOP_REQUESTED未闭合；没有放宽/篡改终态。遗忘后重启列表仍为空。原生现场采集后正常退出PG69808清空。完整native仍FAIL/未完成。[r7证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

最后更新：2026-09-06 14:38。原生r6真实Provider已完成后台分析并生成长期认知记忆；独立UI遗忘后列表及相关当前历史不再展示。semantic召回因授权等待后foreground_lease_expired失败，UI停止未收敛；Cytoscape画布有记忆仍空白。上述缺陷修复中，窗口外short/遗忘后召回未验，完整native/program未通过。完成现场采集后正常退出，PG60384清空。[r6证据与范围](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

## 2026-09-06 后台 analysis 物理出站 guard

最后更新：2026-09-06。产品 `bd5b1180` 将真实 post-turn attempt 与完整输入/来源权限快照
在原事务内绑定；专属 guard 经生产 resolver 校验实际请求、当前披露、来源和候选后才出站。
默认 foreground guard 保留，无新 schema/SDK 制品。14个独立定向控分批通过；最后3项
补强已提交变化断言后通过，进程全部退出。实际 HTTP MockTransport/公开 Memory 物化与
恢复已验；source terminal 是确定性 fixture，无真实 Provider/native 结论。
主已报告合入 bd5 并在 a1fe 接工厂，r6由主独立验证；跨库最终检查非原子撤权事务，
closure/compaction 与 program 剩余项未由本叶完成。
[结果与原失败索引](../plans/2026-09-06-analysis-physical-guard/RESULTS.md)。
## 2026-09-06 前台授权等待租约与Stop收敛独立修复

最后更新：2026-09-06。base8b7c05cb上的独立Host叶，产品9d48465f/e3345565：
Runtime持有keeper跨WAITING续租，同owner过期走原store reclaim(gen+1)，准确恢复原SDKRun，
不重prepare旧history或重start，不扩大默认300秒TTL/权限。真实SDKterminal决定FAILED或
STOPPED；keeper失败/取消与最终读失败均join清理。新10独立控制分批通过，另1受影响mock
邻居单列；Dirac产品/业务测试限定ACCEPT，进程全部退出、共享锁释放。
范围仅同Runtime实例恢复；已合隔离primary候选，重建stack/native尚待主验证，graphblank另列，
不标完整program完成。[结果、原失败与命令](../plans/2026-09-06-foreground-permission-lease/RESULTS.md)。


最后更新：2026-09-06。H075/M616原生r5已实际完成中文Provider响应、WeMM编码、对话写入和审计UI；结束本轮后清空PG54846。后台analysis误用foreground guard已定位，正在修复；短期当前4组处于SDK最近10组排除窗口，尚无窗口外召回证据。完整native/program未闭合。[本轮证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

最后更新：2026-09-06。原生启动暴露的服务登记槽与中断空库初始化已修复；新增两项实际 runtime 检查通过，原生主对话恢复可输入。真实 Provider 已返回，但中文输入用例和随后模型加载异常仍未闭合，完整 native 未通过。[本次结论与证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。
最后更新：2026-09-06。独立Host source-read账本`0e983edc`已用真实M616 Manager完成14项必要验证/Dirac限定ACCEPT。默认registration实际读外层保存payload-free观察，复用memory_call_attempts/findings，无新schema/权限；原host_persistence_unverified保留，Host settlement独立。未调用/能力缺失不归因SDKfinding，写失败不重读、取消join清理后传播。已合隔离primary，v2 union、scheduler与全操作coverage仍独立待办。[接口及证据](../plans/2026-09-06-prospective-source-audit/RESULTS.md)。
最后更新：2026-09-06。隔离 Host schema52 新增 typed cursor/独立终局表，保持50/51旧DDL及恢复注册身份、旧游标值/hash，封闭旧writer；正常注册接新版游标，5项新增迁移/故障/拒绝检查通过。not_required 公共回执消费及完整scheduler尚未接完，默认组合未切换。[范围与证据](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-52.md)。

最后更新：2026-09-06。[隔离schema51时间事件日志](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-51.md)完成新4项及并发发布1项控制；只数据库扩展，完整scheduler和默认接线仍未完成。旧50SQL/默认49不在本叶变更。

2026-09-06 合并复核更正：M616 uv.lock wheel hash 已从误留的M615值修正，新增锁文件一致性检查1项通过；Host恢复与提醒来源均已获得限定独审，详见下方组合记录。

最后更新：2026-09-06。当前隔离候选已组合 H075/M616/S0313，SDK 官方执行库schema9、Host默认49。实际 short使用与空assistant工具组交叉通过；主vendor安装来源检查随后定向通过。未重跑完整旧集合，完整scheduler和质量/native仍待完成。[组合状态](../plans/2026-09-06-typed-use-primary/COMBINED-075616.md)。

## 2026-09-06 Host typed-use 生产接线独立叶

最后更新：2026-09-06。独立typed-use叶现闭合H075 short及no-recall必要恢复范围。
原short伪revision保持拒绝、actualNone经H075公开page/grant→真实physicalguard正常外发；
独立Host来源遗忘仍拒绝。新4场景分别证明sink前/后进程丢失恢复、response_reserved恢复
同receipt不重发、真实pending拒绝同时保留Provider成功事实。发现并修复本叶启动时序P1：
使用SDK原terminal verifier返回的实际publicview，避免查询尚未发布的Hoststack；原校验不减。
两新批分别2PASS后1FAIL、修复后只重试余下2PASS，进程全部清空；未重跑旧long/clock/short。
冻结H075制品独审ACCEPT、旧074614环境/用户库不变；主H075/M616组合和native另验，
不标401/program完成。[固定结果与全部失败保留](../plans/2026-09-06-typed-use-primary/RESULTS.md)。

2026-09-06：Timer新增late-invalidation/lease接管/observation篡改三控分批通过（先1PASS2FAIL，修复仅2红后2PASS）；产品修复2ce1dff1规范SQLite REAL lease签名字节，旧绿未重跑。schema52未合，presentation/ack/native未验。[风险控制结果](../plans/2026-09-06-prospective-scheduler-time/RESULTS.md)。

## 2026-09-06 Timer必要installed H076/M616组合

Host d3f9720a真实pending/rescheduled两路径2PASS1.71s：到期Memory提交丢ACK、过期重开same-ref重放、inbox唯一。原失败保留，旧控制不重跑；尚缺独立竞争控制与presentation/ack/native，未称完整scheduler。进程退出槽释放。[局部结果](../plans/2026-09-06-prospective-scheduler-time/RESULTS.md)。

最后更新：2026-09-06。[隔离schema51时间事件日志](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-51.md)完成新4项及并发发布1项控制；只数据库扩展，完整scheduler和默认接线仍未完成。旧50SQL/默认49不在本叶变更。

## 2026-09-06 提醒注册公开来源

最后更新：2026-09-06。新增 Host 来源解析经 Memory 公开接口绑定真实历史目标及 outbox，授权与 cursor 原子保存，失效复用真实 ACK。安装 H075/M616 下新增7项已有通过结果（首批5绿，两项 fixture 修正后定向2绿），进程清空。唯一 scheduler、signal 派生来源、完整审计接收仍待接线，默认49未改变。[生产边界与证据](../plans/2026-09-05-human-memory-s5c-preparation/PUBLIC-SOURCE.md)。

## 2026-09-06 M0615 installed tool groups

Updated2026-09-06:935d3e12 H073/M0615/S0313 own installs verified169/76/121 members. Original empty-assistant failure is fixed in this successor;22 tests+2 subtests passed13.05s, owned processes cleared. Text tool source chain only; nontext/native/H074/240 remain. [Chinese result and evidence](../plans/2026-09-06-tool-causality/INSTALLED-0615.md).
## 2026-09-06 S5c schema50 successor

Updated 2026-09-06: Primary49 to isolated50; 43 tests passed, real old S5c47/48 rejected without DB byte changes. Default remains49; scheduler/presentation/ACK not wired. Fixedf8e59f31 passed independent scoped review and is merged in the primary candidate. Default remains49; not active scheduler. [Mapping and evidence](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-50.md).

## 2026-09-05 S5c schema48 isolated successor

Primary source index now owns global47; deferred S5c is explicitly remapped47->48
without changing tables/authority/thresholds. Actual source/store/public consumer43
passed; a real old unpublished S5c47 database is rejected unchanged. No production
activation or scheduler/presentation/ack/native completion. Historical47 statements
below remain historical, superseded only by [A11-schema48/v1](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-48.md).


## 2026-09-06 工具组与可信披露组合接入

最后更新：2026-09-06。工具v2非空组源码及证据、可信披露f3675064的两个P1修复均已由主审和独立代理审查。当前合入同一隔离Host候选1268e884，必要交叉检查21项通过14.55秒，测试组已清空（[证据](../plans/2026-09-06-host-trusted-disclosure/COMBINED.md)）；下列开发记录中的未合并/待独审状态为此前阶段。空assistant仍有M0614真实失败，SDK后继修复中；非SELF、真实Provider/native及240质量尚未完成。

## 2026-09-06 工具多消息v2生产接线，仍有SDK空文本阻塞

最后更新2026-09-06。新实际工具组的terminal/逐消息来源原子提交，Host实际结算attestation、完整6item公开注册/short非空/重开/遗忘通过；写中断无半组、两个来源篡改与五个旧v1邻居通过。新空assistant真实完整组被M0614 short non_blank校验拒绝，保持原红并继续修SDK，不丢消息/填placeholder。故本片未完成；源码独审待续，无真实Provider/native/用户主树切换。[各批范围与未完成项](../plans/2026-09-06-tool-causality/PRODUCER.md)。


## 2026-09-06 工具多消息公开因果读取局部验证

最后更新：2026-09-06。新增内部reader通过实际Host effect index和SDK公开投影/bounded审计/结果读取绑定每个工具与父Provider消息，正确区分跨轮重复raw call ID；真实dynamic Host+SDK一个集成测试（含5个篡改及1个截断控制）后继通过，重复读取不新增audit查看缓存，峰165MiB，进程清空。仅来源投影，未接入terminal producer/短期整组索引，不签工具terminal receipt；原始失败保留、独审待续。[实现边界与证据](../plans/2026-09-06-tool-causality/RESULTS.md)。

## 2026-09-06 Dirac披露并发两P1局部修复

最后更新：2026-09-06。自有feat/host-trusted-disclosure/base955a19cd，整片未合主、待主/Dirac复核。真实双控制连接先复现FIFO陈旧A阻塞B与慢checker换代后仍物理send两红；新增schema49 Host入场拒绝记录（无Run/Memory伪receipt）让A拒绝后B继续，出站checker后新连接复核原token。历史source不改。最终新增及必要邻居94项通过/41.51秒/峰222944KiB；PGID40284及全部本轮组已清空，测试槽释放。非SELF/完整外发原子撤权与240质量仍未完成，未跑真实模型/native。
[两P1修复、接口、schema、原红和指纹](../plans/2026-09-06-host-trusted-disclosure/Dirac两P1修复.md)。

## 2026-09-06 披露绑定与历史来源跨层回归已修复

最后更新：2026-09-06。自有feat/host-trusted-disclosure保留eefc8762并在6df952fc合入主30f6b2d4/M614。主指出新turn绑定字段不被旧history source精确形状接受；真实11个foreground/outbox后的short来源登记先红，后继精确token/持久配置校验修复。历史来源仅查绑定时配置，当前使用另核head；换head不改来源receipt。修复后29项通过（10.95秒、峰192368KiB），进程组无残留。源码待主/Dirac终审，未合入主组合；非SELF门、输入permit及完整外发并发撤权仍待后继。
[跨层证据、消费者扫描、失败历史与源码指纹](../plans/2026-09-06-host-trusted-disclosure/验收与跨层修复.md)。

## 2026-09-06 Host可信披露绑定源码候选

最后更新：2026-09-06。`feat/host-trusted-disclosure` / base `cbf99364`新增authenticated control配置、schema48持久policy及queue幂等绑定；Context/scope/出站依赖检查按turn/run回读。原SELF组合保留；非SELF配置目前在生产enqueue拒绝，不作为SDK许可。源码及8个契约测试函数已写，未测试、未合并；当前输入许可与完整外发并发撤权仍未接通。
[接口、schema、13源指纹及待验收范围](../plans/2026-09-06-host-trusted-disclosure/固定源码交接.md)。

## 2026-09-06 Memory 0.6.14隔离Host组合

最后更新：2026-09-06。固定ec046e84接入受众绑定候选，独立6.3MiB环境H073/M0614/S0313全部SDK成员与vendor一致；必要组合32项及2个subtests通过，峰399MiB/22.247秒，进程清空。旧M0613环境保留。SELF与不同最终受众默认拒绝；协作者语义配对不构成外部原始历史授权。该结果不代表实际Provider/native或401/240完成。用户主树未切换，原计划继续。
[安装身份、失败保留、命令和证据](../plans/2026-09-06-disclosure-audience/COMBINED.md)。


2026-09-06主复核：clock固定e32a2542纳入cbf99364，7个源码/证据hash一致；受影响实际memory job/semantic correction/history组合11项通过、进程已清理。原6项clock独立保留；[组合复核及限制](../plans/2026-09-06-corpus-clock/主代理复核.md)。以下待整合表述保留为当时历史。

## 2026-09-06 Host业务clock局部透传

最后更新：2026-09-06。独立 `feat/corpus-runtime-clock` / base `b34b32c3`：composition可信clock经HumanMemoryV7Runtime传至公开SDK builder和默认typed recall；默认仍为time.time，进程内analysis lease采用monotonic。真实公开SDK空库6项契约通过，包括独立history.checked_at、原C04两个时点、重开及内部now隔离；峰值90MiB，进程组已清空。未运行有效seed时间筛选、Provider/native或240质量评估，受众用途接线未完成，待主复核整合。
[接口、命令及原始证据指纹](../plans/2026-09-06-corpus-clock/验收结果.md)。

## 2026-09-06 记忆提议失败审计与连接取消清理

最后更新：2026-09-06。后继c39b2569默认在Host调用账本记录成功/拒绝的安全记忆类型与short选择；召回执行器取消在提交路由前记录取消原因并传播CancelledError。非法输入及异常原文不进入该审计投影。取消写入不等待SQLite写锁，连接建立/PRAGMA初始化失败或取消由内部等待并关闭自有连接；2秒仅为取消请求deadline，不冒称物理硬限额。实际后继32项必要检查通过（含12项故障/取消检查），峰约97MiB、进程已清理；源码独审限定ACCEPT。原36项批次独立保留。仍不覆盖强杀、写盘失败的完整持久性、route决策/审计两事务原子性、Service全部操作或真实模型/native。
[实现、真实故障边界与本机证据](../plans/2026-09-06-model-recall-selection/FAILURE-AUDIT.md)。

## 2026-09-06 模型短期召回及测试资源管理已组合

最后更新：2026-09-06。独审cb743007、145baed3依次fast-forward接入组合：H073/M0613/S0313再次核对169/75/121 installed成员与本树vendor一致；实际候选/短期worker22PASS/9.90秒，峰值265MiB、进程已退出。新模型长短期请求单typed预算、完整来源和最终出站再检查；资源入口默认串行锁/RSS/时间限制及父退出后组清理，三个实际故障点均原红→修复绿并独审通过。
叶子53项及补充混合/认知测试各自证据保留，未冒称整体重跑。资源采样非硬限额/全系统监控；用户原main未切换，仍无真实模型/native新组合或401/240全量，原程序继续执行。
[组合身份、命令和待办](../plans/2026-09-06-model-short-recall/COMBINED.md)。


## 2026-09-06 模型短期统一召回已通过安装候选测试

最后更新：2026-09-06。隔离feat/model-short-recall/base a0764047，H073/M0613/S0313逐文件匹配本树wheel。显式模型长期/短期选择共用一次typed计划和预算；真正选中的short绑定公开四元组及当前完整Host因果来源，缺证据或晚遗忘阻止物理出站。成功选择进入既有调用记录，默认工具启用。
首批53PASS/31.31秒/峰值261MiB；另两项认知出站邻居通过，新增非空长短期混合从fixture两次红修复至1PASS。原始失败及范围见下链；非真实模型/native或全量重跑，全部进程退出。独审待固定提交；完整失败attempt观测、工具多消息、401/240和原程序仍未完成，未切换用户main。
[契约、批次结果与本机证据索引](../plans/2026-09-06-model-short-recall/RESULTS.md)。


## 2026-09-06 WeMM按需加载与内存引用修复已接入组合

最后更新：2026-09-06。独审b70ccda5以fast-forward接入；构造/元数据/状态不加载权重，首次真实embedding共享加载；取消下异步排队和物理线程互斥，失败完成任务丢弃实例引用，防异常保留模型。WeMM2048/L2/本地模型及SDK pin不变。设置页四状态真实WebKit组件检查和刷新通过，浏览器峰值433MiB、进程已退出；相关叶子线程/公开空库/IPC/React/类型检查见证据。
旧库补向量仍可能启动加载；未实测真实权重/GPU内存释放、自动卸载或新组合native，不作整体program完成声明。用户主checkout未切换。
[组合验证及后续内存管理](../plans/2026-09-06-wemm-lazy/COMBINED.md)。

## 2026-09-06 WeMM Host lazy loading leaf

2026-09-06 follow-up：加载完成回调仅清理同一done task引用，避免失败traceback
长期持有维度拒绝模型；不改waiter异常、不清traceback、不自动重试。fake weakref
原红→绿，含必要邻居5PASS0.20s；pending/新task不会被旧回调清掉。ready措辞收紧
为已加载，非完整搜索质量保证。物理线程/权重分配器释放仍不作推断。

最后更新：2026-09-06。WeMM构造、dim/lineage和P4 status仅访问元数据；首次embed
才import/加载本地模型，加载维度必须2048，保留L2及原fingerprint。共享shield load
task与owned异步encode队列保持实际线程互斥，取消排队请求不提交executor线程。
cold/loading/ready/failed不触发加载，卡片显示真实名称与按需状态。线程取消不等于
物理终止或释放权重；没有新增warmup/close/unload。SDK0612 public空库builder以
fake模型证明0构造，旧库ensure缺向量仍可启动加载，未改冻结SDK/catchup契约。
backend唯一13例、React2例、tsc0；没有真实权重/native。独立worktree尚未合main。
[固定叶子验证与边界](../plans/2026-09-06-wemm-lazy/RESULTS.md)。


## 2026-09-06 短期索引及 Service0313 已组合验证

最后更新：2026-09-06。唯一MemoryAnalysisLane默认增加完整两消息组short登记/公开projection，保留低序号迟到重扫、ACK后确认、关闭清理和实际分析；工具多消息仍拒绝。主组合安装H073/M0612/S0313，受影响六模块62PASS/25.45秒、峰值290MiB，全部子进程已退出。三个wheel及installed成员逐字节一致。
Service工具审计新增发送attempt/UNKNOWN/真实ACK/后继响应，仍非持久sink或完整Run绑定；全操作落盘、增量projection、多消息producer、模型short协议及原program未闭合。未切换用户main/runtime，无新模型/native。
[命令、身份、结果与边界](../plans/2026-09-06-short-index-worker/COMBINED.md)。

## 2026-09-06 Short index worker 隔离叶子

最后更新：2026-09-06。`feat/short-index-worker`/basefe006f59，源426db3bb获Dirac限定ACCEPT。
默认复用唯一MemoryAnalysisLane：outbox→whole-group short step→analysis；固定upper的
turn keyset环绕，晚delivered旧组重查、坏组不阻合法后组；仅实际registration+projection ACK
进入有限内存缓存，manager reopen清空。主退出在borrowers停止后关闭v7 owner。
分批8PASS/6.20s、3PASS/4.81s、相邻6PASS/9.30s；非相加质量分。无模型/native或SDK制品改动。
SDK0612 projection仍全subject扫描，Host16不等于总成本/P99界；多消息tool来源、新模型short协议
仍未交付，S3/S6/program仍未完成。[契约和实际证据](../plans/2026-09-06-short-index-worker/RESULTS.md)。

## 2026-09-06 短期选中来源已合成

最后更新：2026-09-06。独审2d98e083合入7fafe03a，同时保留审计authority；实际factory每hit完整来源、裁减/遗忘不互相污染、显式长期零short与HUMAN审计WS组合42项通过（21.12秒、峰值238MiB）。
仅已有内部短请求来源路径闭合；自动生产索引worker、多消息完整producer、新模型short协议及原program仍未完成。无新模型/native运行。
[组合结果与边界](../plans/2026-09-06-selected-short-runtime/COMBINED.md)。

## 2026-09-06 Selected short runtime 隔离叶子

最后更新：2026-09-06。`feat/selected-short-runtime` 从49249dbd接入实际factory共享
conversation authority；默认已有short请求逐actual triple携带完整group来源，每次group
读取同只读事务验证primary/epoch。显式长期选择不附带short；不使用all-indexed roots。
真实11组/两hit裁减后遗忘/reopen分批18PASS/7.04s、3PASS/8.03s；相邻24PASS/19.99s
独立列示（含1必要重验，不累计作质量分）。最后门为实际PrimaryHistoryPolicy直接fresh复查，
不是新physical outbound/native。借用组合venv，无独立安装身份。源2d98e083获Dirac限定ACCEPT，未合主树。
生产索引worker/增量登记、多消息tool完整producer、新模型short协议仍未完成；不改S3/program完成度。
见[契约及实际结果](../plans/2026-09-06-selected-short-runtime/RESULTS.md)。

## 2026-09-06 审计查看入口组合验证

最后更新：2026-09-06。独审1097b272合入c53caff2：记忆面板显式打开用途绑定的HUMAN元数据审计，分页/持久ACK重放、关闭与身份失效拒绝；保留原图谱viewport及遗忘ACK修复。组合独审限定ACCEPT。
后端54项通过，新增真实/ws/control审计往返2项通过，前端44通过/1个可选API-fixture未配置跳过，TypeScript通过。单进程有界执行；没有真实Provider、native或全操作覆盖。初始snapshot成本及原生验收仍待续。
[组合证据、命令与范围](../plans/2026-09-05-agent-operation-audit/human-access-leaf/COMBINED.md)。

## 2026-09-06 模型召回类型选择局部完成

最后更新：2026-09-06。memory_standalone工具显式类型经Host校验传入已安装Memory0612公共计划，保留Host身份/权限/预算；显式长期选择不偷偷附带短期查询。成功类型枚举写既有Host审计记录，原proposal仅hash，非公共SDK完整参数回读。
独立叶子最终77项通过（50.57秒、峰值191MiB），包括实际选中来源/最终出站/任务披露链；没有真实Provider或native。固定49249dbd已独审限定ACCEPT并fast-forward主组合，完整类型质量、短期与调度、审计UI及原program仍未完成。
[契约、命令、历史红与证据边界](../plans/2026-09-06-model-recall-selection/RESULTS.md)。

## 2026-09-05 Memory0612 installed credential successor

Independent artifact/exact-installed review ACCEPT, including captured actual Host
history first/reopen. Native click acceptance remains separate and blocked by lock screen.

Last updated: 2026-09-05. Exact0612 fixes public tool-name credential false positives;
64PASS8.76s startup/composition/Memory audit/preparation rejection. Installed SDK bytes match wheels/source, no source overlay.
Native graph recovery and full operation coverage are not yet claimed.
See [installed identity and checks](../plans/2026-09-05-s6-primary-preparation/SDK-0612-INSTALLED.md).

## 2026-09-05 Host Memory attempt and pre-SDK rejection audit leaf

Coordinator combined06348031 with installed0611: audit/preparation/graph producer
combination49PASS16.43s, no source overlay. Independent leaf ACCEPT retained;
production sealed issuer and full operation coverage remain unfinished.

Last updated: 2026-09-05. Isolated feat/host-memory-operation-audit from7cf2a39c
wires durable Host started/settled around actual foreground typed recall and semantic
correction candidate recall. The default audit composition additionally discovers
verified Host preparation rejections with no SDKRun. Original errors/cancellation
and business results are preserved; recovery never repeats a business effect.
Installed Memory0611 exact-wheel/Host combination32 tests pass, including public
sealed snapshot pages and concurrent reader recovery. Trusted receipt-supplied OA1
reader is implemented, but Host production grant issuance/authorized external
readthrough and all other inventory boundaries remain unfinished. No usage/cost
aggregation or all-operation/native completion claim. Fixed source3ba25c42 has Dirac independent scoped ACCEPT, no remaining P0/P1.
This acceptance applies only to the leaf above.
See [scope and handoff](../plans/2026-09-05-agent-operation-audit/host-memory-leaf/HANDOFF.md).

## 2026-09-05 Memory0611 installed audit and retry successor

Last updated: 2026-09-05. Main-owned candidate pins exact0611, combining reviewed
privacy, bounded SDK audit and current-attempt reclaim repair. Installed55 startup/
composition/graph/runtime checks pass; no source overlay. Main running environment
and isolated native graph0610 remain unchanged. Full Host operation coverage and
Harness successor are still pending. See
[installed evidence](../plans/2026-09-05-s6-primary-preparation/SDK-0611-INSTALLED.md).


## 2026-09-05 Public TwinGraph HUMAN projection and completion invalidation

Last updated: 2026-09-05. Host reads manager.get_twin_graph_view using actual principal/primary and signed late API boundary. Node and edge output is bounded with closed endpoints; SDK collection remains a full scan. Main shares content-free completion invalidation between actual suppress and MemoryAnalysisLane APPLIED (including no_mutation). Server generation is checked after the final identity await; no SDK global epoch or Agent graph input is introduced.
See [scoped results](../plans/2026-09-05-s6-cytoscape-display/RESULTS.md) and [contract](../plans/2026-09-05-s6-cytoscape-display/CONTRACT.md).

## 2026-09-05 Selected short source modules combined on0610

Last updated: 2026-09-05. Reviewed selected source reader and source-only conversation
registration/indexing modules are now integrated into the main-owned candidate.
Actual installedMemory0610 ingestion/selected-reader34 tests pass; no source overlay.
Production indexing scheduler and final selected-hit wiring remain unfinished,
so full short-horizon availability is not claimed. See
[contract](../plans/2026-09-05-selected-short-sources/CONTRACT.md).


## 2026-09-05 Original native duplicate-forget regression passes on0610

Last updated: 2026-09-05. Actual043c722a backend with installed0610 reopened the
original native data. The exact prior failing drink query now visibly answers
“不知道”; canonical physical Provider request contains neither original nor revised
drink text. One foreground call, separate analysis call; app closes normally.
Original failure/action preserved. This is scoped native regression evidence,
not a new complete memory loop or full program PASS.
See [native evidence](../plans/2026-09-05-semantic-correction/NATIVE-0610-REOPEN.md).


## 2026-09-05 Memory0610 installed privacy and queue successor

Last updated: 2026-09-05. Candidate production composition now binds actual Host
source/cut authority to Memory0610 shared disclosure enforcement. An old late-enqueued
USER denied before SDK start becomes an immutable Host preparation rejection:
Run FAILED, turn SETTLED, no fabricated SDK terminal, and subsequent work progresses.
Cross-source rejection reuse is rejected from actual S1 binding; first-action cuts
and legacy unknown boundaries remain distinct. Installed86 tests pass and independent
source/artifact scoped reviews accept. Main and historical native failure remain
unchanged; native successor and full program verification are still outstanding.
See [installed evidence](../plans/2026-09-05-s6-primary-preparation/SDK-0610-INSTALLED.md)
and [queue contract](../plans/2026-09-05-semantic-correction/PREPARATION-REJECTION.md).


## 2026-09-05 Original source and first forget-cut public facts prepared

Last updated: 2026-09-05. Host now captures a v2 forget action's original queue
frontier atomically with its S1, and exposes read-only source/cut facts through
public SDK carriers. Exact retries preserve the first cut; old v1 actions remain
unchanged and explicitly lack a verified cut. Source-overlay fact/API13 pass,
with independent scoped ACCEPT. SDK enforcement/builder/native integration remain
pending; this is not a duplicate-forget PASS.
See [contract and evidence](../plans/2026-09-05-semantic-correction/HOST-SOURCE-CUT.md).

## 2026-09-05 USER source and queue admission now atomic

Last updated: 2026-09-05. The service now persists new USER S1 and its queue row in
one fenced transaction. Only first insertion marks a v2 atomic origin; existing
S1/turns retain legacy format and exact replay. Source/queue/runtime-history checks
pass with independent scoped ACCEPT. This closes the source-before-queue crash
window, while duplicate-source suppression and native retest remain incomplete.
See [implementation and validation](../plans/2026-09-05-semantic-correction/ATOMIC-SOURCE-ADMISSION.md).

## 2026-09-05 Native correction passed; duplicate-source forget remains P1

Last updated: 2026-09-05. Backend c2836c12 with exact Memory0.6.9 successfully
created and revised the same memory in the actual native app. The panel forget
produced a real directive, but an older duplicate USER from a rejected CREATE
remained in the next actual Provider request and the model returned the old value.
The memory loop is **FAIL**. Deterministic coverage is1PASS2FAIL for no duplicate,
pre-forget admission and delayed admission. A fresh same-text USER/replayed-action
control passes only in the no-duplicate case. Required source-order/cut/equivalence
repair is underway; no main cutover or program completion claim.
See [native evidence and exact boundaries](../plans/2026-09-05-semantic-correction/NATIVE-DUPLICATE-FORGET.md).
Earlier entries below describe their own historical checkpoints.

## 2026-09-05 Native CREATE slot failure and v3 source fix

Real069 native startup/history/current Run worked, but remember failed: the model
invented a CREATE target key, Host rejected it, and no preference was materialized.
V3 now explicitly represents CREATE with an empty candidate slot while retaining
all REVISE/intent/target guards. Source tests and independent review pass; native
retest remains required. Assistant "remembered" text does not establish Memory
write success. See [failure and evidence](../plans/2026-09-05-semantic-correction/CREATE-SLOT-V3.md).

## 2026-09-05 Memory069 installed successor verified

Actual startup additionally required updating the four Memory candidate identity
constants; the first App attempt rejected the stale067 pin.32 startup/composition
checks pass after that correction, with the original failure retained.

Combined candidate now pins exact Memory0.6.9; same Harness0.7.2/Service0.3.12.
Independent source/wheel/install comparisons and29 affected tests pass. Production
factory upgrades a copy of actual native0.6.3 data and reopens with the same receipt
and unmodified backup; original data unchanged. No native069 claim yet.
See [installed evidence](../plans/2026-09-05-s6-primary-preparation/SDK-069-INSTALLED.md).

## 2026-09-05 Shared semantic correction production composition

Primary startup now uses one production factory binding the same semantic action
authority to Host analysis and the public Memory builder. The real-store test
harness uses that factory:29 distinct semantic/API/barrier cases pass across an
initial26PASS3test-importFAIL and affected3PASS rerun. Reviewed semantic sources
are integrated; installed067 verification does not cover069 migration or native
model behavior. See [results](../plans/2026-09-05-semantic-correction/RESULTS.md).
User-requested cross-SDK operation recording/audit is now an explicit additional
[implementation scope](../plans/2026-09-05-agent-operation-audit/PLAN.md); current
rotating diagnostics must not be described as complete automatic auditing.

## 2026-09-05 Typed selection forget barrier exercised

Actual public typed selection/Context route now uses the cognitive forget API
before the next production pre-invoke guard. Positive sends2; late forget sends1
and rejects the next stale request. Two controls passed2.18s with deterministic
analysis/MockTransport; no native or paid Provider. This adds the previously missing
typed-source proof to the existing history/reopen coverage. See [API evidence](../plans/2026-09-05-primary-cognitive-controls/API.md).
A further2 typed-only controls pass with no remembered source in chat history;
removing only the recall binding would allow the same request, proving the selected
source barrier itself. Public fixture materialization, no real model in this pair.
SDK069 migration and semantic-action builder hooks are prepared but those new
branches still await their actual successor candidates; see [runtime preparation](../plans/2026-09-05-s6-primary-preparation/RUNTIME-SUCCESSOR.md).


> 本次 leaf 更新：2026-09-05

## Selected short-source reader isolated candidate

新增 SelectedShortSourceReader，消费公开069 selected-source snapshot，经原有
PrimaryConversationAuthority核实完整group/S1，再逐hit复用PrimaryHistoryPolicy递归
USER/terminal祖先。只返回accepted selected来源root+实际shortbinding依赖union，不用
all-indexed roots、不伪typed、不读SDK私SQL。主仍负责registration默认hookup、RecallLanes
接线与最后出站fresh fence；本观察不能替代最终授权，reconcile全扫描cost不变。
新leaf19项source-overlay与定向ruff绿；后继exact069 cf149022 + Harness072 installed
同19项通过，Memory68/Harness151包文件source-wheel-installed一致，168已加载SDK模块来自
独立venv。旧source记录保留不重复计数；未运行native/真实Provider，主组合仍待验。
接口/测试边界见[契约](../plans/2026-09-05-selected-short-sources/CONTRACT.md)。

## 2026-09-05 Primary cognitive panel connected locally

默认主对话入口已接认知记忆面板及真实HUMAN API；current signed owner限制读写，
同owner隐藏/重挂载保留未决动作，换owner清空。匹配forget ACK同步清历史/detail再补读。
父视图组合24项、tsc及定向lint通过；backend已独立限定ACCEPT。尚未native真测，
完整进程重启不保留UI内存动作ID；自然语言纠正/全闭环仍待完成。
见[组合记录](../plans/2026-09-05-cognitive-controls/COMBINED.md)。


## 2026-09-05 Authenticated cognitive read/forget API candidate

Primary HUMAN接口已接实际V7 public graph/suppress与专属动作S1，精确目标选择与原动作重放；
真实连接在慢读后/Host admission后失效分别阻止后续写，SDK ACK丢失后同ID/time可确认。
最终wire9项及并发收敛1项通过；实际API忘记后重启，旧page/detail与下一实际Adapter
出站均不含已忘来源。初始相邻46通过。只证明SQLite/签名连接/确定性transport，
frontend/native及自然语言纠正尚未完成，不能称用户闭环已完成。
见[接口与证据边界](../plans/2026-09-05-primary-cognitive-controls/API.md)。

## 2026-09-05 Explicit semantic correction isolated leaf

Follow-up: fixed800ff419 independently scoped ACCEPT (no blocking P0/P1). The v2
prompt now names new drink preferences `user:self + drink_preference`; no alias or
authority expansion. Five Chinese public-SDK cases passed (5.01s). Native model
compliance and coordinator builder integration remain pending; details in results.

From9ec0ec97, the isolated semantic-correction candidate adds actual public typed
semantic candidates, Host independent full-sentence intent and exact public REVISE
authority. **32 focused tests passed (19.13s)** on installed067; Chinese natural
correction/quoted/negative, ambiguity, original evidence, replay and late-forget deny
covered. v2 prompt/schema/policy/validator; no new schema or display graph input.
Main/Runtime wiring belongs to coordinator; fixed-source review and native complete
loop remain pending. Limited Chinese slot vocabulary and unsupported cases are explicit;
this is not unrestricted natural-language or full program completion.
See [contract](../plans/2026-09-05-semantic-correction/CONTRACT.md) and
[results/boundaries](../plans/2026-09-05-semantic-correction/RESULTS.md).

## 2026-09-05 Source/auth/action combination verified

Fixed e31c6efd source index closes the independent unscoped-search/late-forget P1;
combined with exact SDK decisions, source-aware history, schema47 and action evidence:
**51 passed** on installed067. Only test-fixture signature required merge resolution.
The separate f9cbb7c8 native candidate also passed expanded visible authorization
by real mouse click; this combined tree has not run native. Cognitive UI/SDK suppress
and selected-source indexing remain incomplete; no main cutover or full program PASS.
See [combined evidence and boundaries](../plans/2026-09-05-primary-effect-sources/COMBINED.md).


## 2026-09-05 S5c T3 独立 registration consumer（未接线）

在自己的 store/consumer 模块实现原 prepared registration 的恢复投递与 exact Memory 回签持久化。
仅调用现有公开 outbox/signal API；prepared/cursor 不改写，applied 行记录回签而非 occurrence processed。
新增21项通过（含3项已安装 Memory SDK 实库重开/过期回放），既有66项相关回归通过。
真实 source resolver、due/event、唯一 scheduler lifecycle 与 T4/T5/T6 接线仍未实现；自身独立 review 待主协调。
默认 schema/SDK/pin/主 runtime 未变，不影响主 S3 冻结的0.6.5 candidate，不算 S5c 或 program 完成。
接口、限制与证据：[T3 consumer](../plans/2026-09-05-human-memory-s5c-preparation/T3-CONSUMER.md)。

## 2026-09-05 S5c T1/T2 隔离 Host 基础（未接入生产）

分支 `feat/human-memory-s5c-preparation` 已合入 main `c183fe70` 的 Q1/downgrade 修正，
新增显式 v47 initializer、三张 Prospective 领域表与一张 action journal，复用原迁移事务和 recovery fence。
registration/source/signal/cursor 同事务；只读 authority resolver 校验 exact ref 与 durable source；
action request 不授予权限，claim 不表示呈现或处理。没有 action grant issuer、scheduler、ack 工具、
priority 或 suppression 接线。默认 production schema 仍 v46，默认 initializer 拒绝 v47；
`main.py`、SDK/pin 无本切改动。66 项决定性及相关回归通过，独立 review 尚待主协调。
这只证明隔离基础服务，不改变 S5b gate 状态，不算 S5c/Program 完成。
本切接口与 G6 版本化 schedule 阻塞见
[T1/T2定稿](../plans/2026-09-05-human-memory-s5c-preparation/T1-T2-INTERFACE.md)；
主线程 S3 冻结的 Memory 0.6.5 candidate 不纳入本分支 priority 实现。

> 最后更新：2026-09-05

## 2026-09-05 Cognitive action evidence callback prepared

主协调候选新增显式忘记动作的Host evidence回调，复用既有S1表和首committed_at；
独立source域区分generic primary.append，精确动作重试保持同一时间/SDK请求ID。
真实Host SQLite四项通过，无新Run/分析outbox；尚未接认知面板/SDK suppress/真实UI，
不是已默认可用的忘记能力。接口和边界见[ACTION-EVIDENCE](../plans/2026-09-05-primary-cognitive-controls/ACTION-EVIDENCE.md)。

## 2026-09-05 Combined decisions retain authenticated history context

在067隔离候选中组合精确SDK授权，保留实际HUMAN request_id派生的USER_REVIEW披露上下文；
修复新history state签名与旧decisions读法冲突（真实生产fixture先红），并组合持久WAITING后通知（runtime16 passed）。20项授权验证及53项
受影响API/history/foreground相邻通过；当前USER被public EVIDENCE抑制后，授权参数不披露、
批准被拒、SDK决策仍open且无新Scope/Provider。真实主机模型/布局、MEMORY-only、Scope未route
search来源均不由这些测试覆盖；完整候选仍不可切main。见[组合记录](../plans/2026-09-05-primary-sdk-decisions/COMBINED-HISTORY.md)。

## 2026-09-05 首批固定 history 候选组合

Memory0.6.7 依赖候选与固定 history/helper7dcfce8b 已组合，未带入子代理未提交代码。
实际 runtime/API/遗忘与出站、v2 helper、生产组装、未知调用分类和 CREATE_NEW 自动绑定效果
受影响组合 **42 passed**。预先 scoped 的来源功能 P1、真实授权 UI 和短期登记生产者尚在后继修复，
本组合未起 App、不能称完整产品可用。命令与本地 hash 见
[CANDIDATE-067](../plans/2026-09-05-s6-primary-preparation/CANDIDATE-067.md)。

## 2026-09-05 下一主对话候选依赖固定

隔离 primary-candidate 树固定 Memory0.6.7/既有 Harness0.7.2/Service0.3.12，并将滞后的 uv.lock
对齐既有 pyproject 要求；九项非 SDK 版本变化与已真测主环境一致。三个 SDK 从本树 vendor 装入
独立环境，身份/生产 composition **21 passed**，offline lock check 通过。未 sync 主环境或启动 App，
完整历史/恢复/短登记/授权 UI 仍待合入；见
[CANDIDATE-067](../plans/2026-09-05-s6-primary-preparation/CANDIDATE-067.md)。

## 2026-09-05 Primary 生产目录修复

真实原生普通回复/重启追问通过；新项目请求在 `607acc7d` 暴露三项 Context 控制被
requires_project 默认值过滤，零 Scope/文件。仅三项 Host 注册补 safe，真实生产 composition
红→绿与相邻验证 **34 passed**；修后原生目录已完整，但模型仍沿旧历史要求目录、零工具调用。
已补主对话当前路由指引，原运行回归 **16 passed**；原生 `5da24d6f` 已实际调用 create_new，
但停在 SDK 工具授权等待，Primary 未显示授权卡，仍零 Scope/文件。真实点击停止后 Host STOPPED、
SDK cancelled、待决授权 cancelled，界面回空闲；仅此等待状态的停止通过。原始失败与命令/哈希见
[INTEGRATION](../plans/2026-09-05-s6-primary-preparation/INTEGRATION.md)。

## 2026-09-05 Primary source effect index v47 — reviewed local candidate

8e896472 independent P1 confirmed: unscoped search could escape source checks when
TaskScope reservations were absent. A Host append-only exact SDK effect identity
index now records real handler entry under the captured foreground lease; Provider
preflight reads actual SDK results and preserves search→create dependency prefixes.
No scope grant/watermark, SDK change or old evidence restamp. Default schema is47;
coordinator owns deferred S5c's explicit48 remap (historical47 AC remains historical).
Fixed real late-forget counterexample is green; adjacent search/scope17, page-in1,
startup/create32 and migration11 passed. Independent fixed-counterexample review accepted e31c6efd; no
main/native or full privacy completion claim. Ordinary page-in lacking source proof
rejects; generic page-in source projection and short source-only admission remain open.
Details and raw-log hashes: [source migration contract](../plans/2026-09-05-primary-effect-sources/SOURCE-MIGRATION-CONTRACT.md).

## 2026-09-05 Runtime v2 / new message producer combination

运行层保持v1可读并保真v2 short triple/actual UTF8及Host source roots；缺source proof或伪audit
拒出站，不伪typed。深冻结来源快照修复先红后绿；installed exact067（7dd224…）相邻60 passed。
首次terminal observer同tx追加真实message S1，marked replay只验证不修复，旧无marker不补造；
helper模块已正式入树。Hegel11group登记/selected来源闭合仍另线，不把此60绿称Host short pipeline
或native完成。精确命令、版本和证据见[运行契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

## 2026-09-05 Waiting state invalidation

Foreground在BOUND_WAITING reconciliation提交后调用现有空payload、有界非阻塞刷新。
回归刻意等此前SDKbind通知完成，旧代码1红，修复后runtime/真实primary相邻32 passed16.31s。
这是状态通知证据，真实授权卡/decision UI组合由另一所有者验证。原始日志位于
`.local-test-evidence/2026-09-05/primary-history/waiting-notify-{red,green}.log`（ignored）。

## 2026-09-05 Scoped ordinary projection restoration candidate

初始scoped、动态resume/search共用确定派生manifest，无新ledger。真实CREATE_NEW操作前
依赖快照+actualSDK effect/route绑定保留title/goal；旧/抑制文字明确fieldgap，仅结构+真实
binding+当前USER继续。实际保留字节与原fullview hash分开，start/dependency每次出站重验。
相邻96 passed（含真实MEMORY-only抑制后fileeffect/terminal）、最终start负例2 passed；
初始scoped旧功能红已恢复。未知旧mutation/checkpoint文本仍不声称可恢复，short登记另线，
等待独立review与主组合，不称native/program完成。详见[来源契约](../plans/2026-09-05-primary-resume-sources/CONTRACT.md)。

## 2026-09-05 短期来源的历史读取契约

Primary shared history helper 增加 v2 的独立 short_horizon 三元组，旧 v1 原样兼容；所有来源仍在
同一公开 Memory batch 校验，递归终态与旧 detail 引用不绕过。Memory0.6.7 独立安装环境下，
Host shape/batch/递归 API 及既有历史用例 **53 passed**；正向短项为明确的 policy fixture，另有
真实 Manager 拒绝未选择 audit。该层通过不代表 Host 对话登记/短期索引已有生产数据。
实际短期生产登记、runtime 组合和原生验收继续单列，见
[SHORT-V2](../plans/2026-09-05-primary-history-api/SHORT-V2.md)。

## 2026-09-05 主对话历史跨层遗忘验证

隔离组合 `888efe0c` 上，真实 Host/Harness/Memory SQLite 与 ProductProviderAdapter 的确定性
HTTP transport 完成两轮普通对话，随后只抑制一个真实 memory ID：公共 page/detail 隐去原用户及
两个依赖回复，独立 USER 保留；重启不重发，下一次实际适配器请求不含已忘内容。三项组合 **3 passed**。
旧 scoped observer 缺完整来源时仅保留原 USER；该测试不覆盖生产初始 scoped guard 的现存 P1。
无外部模型/原生 UI/main SDK 切换或全 program 通过声明，见
[跨层验证](../plans/2026-09-05-s6-primary-preparation/HISTORY-CROSS-COMPONENT.md)。

## 2026-09-05 Primary history runtime review slice — not production-ready

隔离history树（base284ea40b）接shared primary visibility policy、真实start/terminal依赖、
actual typed recall四元组与每次physical Provider前fresh check；cold USER/terminal S1无需先ingest。
后继独立review发现no_recall marker被误当effect导致ordinary历史丢失，已按真实origin纠正；
相邻契约合跑41 passed/1 failed（仅初始scoped），exit1，非整体PASS。
原切片94 passed不证明动态历史来源完整；后继动态ResumePackage漏发已先红后绿，聚焦21 passed。
每条实际route交叉验证SDKeffect/receipt；无来源证明的ResumePackage在delegate前拒绝，
FAILED且第二次物理发送为0。真实create_new在逐次guard下完成effect/terminal；已发送unknown分类未改。
**仍有P1：既有预先scoped ResumePackage无完整proof，新guard会拒绝首Provider（真实probe1红）**。
不得以94绿抵消该回归或用scope豁免。共享API ea315525已正式cherry为2645d8b2，移除helper加载层后组合71绿；
独立venv已安装exact Memory0.6.6（其他Host依赖只读借用），窄复验10绿。仍仅隔离组合，
没有生产切换/native隐私完成结论；独立short-horizon carrier仍不支持。原证据不删除。
命令、边界及hash见[运行层契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

## 2026-09-05 primary history visibility API 隔离候选

`feat/human-memory-primary-history-api` 从 `87c42b43` 增加 Host shared
`PrimaryHistoryPolicy.check_evidence_ids/check_dependencies`：真实 S1 subject/primary/hash
与终态身份校验，递归 evidence/recall 依赖合并为一次公开 Memory batch；state/page/detail
在慢读取后 fresh 检查，真实 WS request_id + authenticated subject 构造 USER_REVIEW，
输出前重验现有 connection fence。旧终态无依赖证明只隐藏 generated group，原始 USER
不依赖异步 analysis。未改变归档、SDK、schema、pin、main.py 或 runtime 文件。

本隔离实现聚焦 **50 passed**，使用独立环境 exact Memory0.6.6 / Harness0.7.2：真实
Memory memory-only forget 反向隐藏原始 USER、其 assistant 及跨 Run 继承 assistant，
无关 USER 仍可见；真实 recall 四元绑定正负、reopen 和 Host 证据字节不变通过；
in-process production WS 验真实绑定/request_id，慢 batch 期间重连拒绝旧响应。
这些是 library/API 证据；SDK transcript fixture 不替代 Carver runtime 组合测试。
尚待 Dirac 独立审查、主组合与 native/provider 验证。short-horizon 仍缺本轮可用的 exact
carrier，必须在运行层拒绝复用/出站；不称 S6/S5b/program gate 完成。
见 [接口与验证](../plans/2026-09-05-primary-history-api/CONTRACT.md)、
[验证记录](../plans/2026-09-05-primary-history-api/VALIDATION.md)。

## 2026-09-05 主对话隔离组合验证

运行层、API 与新前端已组合；真实 SDK + SQLite + deterministic Provider 经公共历史 API
验证新普通对话/旧 scoped 终态、重启标识一致与 raw event 错绑拒绝，聚焦 **51 passed**。
原生候选 `87c42b43` 的真实 gpt-5.5 普通对话已回复，未创建 TaskScope；正常退出/重启
看到历史恢复，账本确认前台/分析调用均未重发。解锁后的第二轮追问也通过，实际出站请求包含原用户/助手历史。
完整来源遗忘与新建项目授权仍在修复，未切换 main、未标 S6/program 完成。证据哈希见
[INTEGRATION](../plans/2026-09-05-s6-primary-preparation/INTEGRATION.md)。
> 验收基线：simple_harness `4e797ccd`；Harness `fbb156f` / 0.3.0 / wheel `cf629cee…`；
> Memory `3d4247b` / 0.4.0 / wheel `bfcd2506…`
> 发布标记：Harness `v0.3.0` → `fbb156f`；Memory `v0.4.0` → `3d4247b`；主分支与 tags 已推送；
> 本地冻结 wheel/sdist 已正式发布到对应 GitHub Release，并通过公开稳定 URL 下载回验

本文档是 simple_harness 的 Memory 生产边界事实源。2026-08-22 的官方一等集成已完成代码、自动化门禁
与真实 macOS Computer Use UI 验收；SH-M1～SH-M6、SH-SURFACE 均已在真实 DeepSeek provider 下通过。

## 2026-09-05 Primary API P1 后继修复

`fbc026a0` 的40项局部绿色未覆盖实际 Host authority/raw SDK hash 差异，独立组合 probe
已复现真实完成历史拒读。后继 API 改用 Carver 唯一 terminal_identity helper 验来源链，
再精确核对真实 SDK event；还修正 active source 抑制、slow reader 后整页 source 复查、
SQLite 已 commit 后 wake 失败仍返回 durable ACK（0.5秒通知预算）。没有新增权限或 ledger。
公开 suppression 没有 batch/snapshot/epoch，最终逐来源复查不等于原子隐私快照，
memory/entity lineage 扩展仍后续。该提交必须与 Carver helper 组合；真实 runtime API
集成测试由主维护并待其运行，不能把局部测试算产品闭环。
聚焦 source overlay 验证45 passed（29 API+16既有），尚待主组合测试与独立复核。
详见 [PRIMARY-API](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

## 2026-09-05 Primary API 隔离切片

`feat/human-memory-primary-api`（base `29902ea4`）实现 `primary.state`、
`primary.messages.page/detail` 与 exact targeted `queue.control`。读取真实 Host
primary/turn/source/binding/terminal receipts，并通过注入的 SDK 公开 transcript/binding reader
验 run/event/hash/user anchor；queued user 可读，公开长文本可分块完整取回。
revision 读取既有 append-only 表尾及当前 head，page 最多扫描10 turns，queued policy
最多100项，超限计数明确为下界。无新 schema/计数 ledger/Session。

过滤结论仅覆盖当前 subject、Host 输入及终态观察 evidence；Memory resolver 不展开
memory/entity lineage，不能声称所有 memory-derived 历史的隐私已闭合。缺 policy/reader
拒绝对应读取。Carver 已在其隔离树注入三个 factory kwargs，组合树运行与 UI 验证尚待主协调；
本分支不改 main/runtime/fence/SDK/pin，不代表 S6/program 完成。

聚焦验证：新增24 + 既有16 = **40 passed**（公共 API/真实 Host SQLite 与已安装 Memory
suppression backend；SDK transcript/binding 为注入 fixtures），未跑 provider/UI/全量。
契约、命令与 ignored 证据索引见 [PRIMARY-API](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

## 2026-09-05 S6隔离分支：control复用与无scope admission

`feat/human-memory-s6-primary-preparation`（base `4eb1eb7c`）已实现P1：沿现
`/ws/control` 的signed profile_bind验证本连接owner/epoch/active lease；HUMAN读写
不再只凭全局ready或shared secret，保持原local subject、primary-ID fence及exact effect授权。
没有新增账户、socket、Rust/TS scope或每次读签名。

P2 runtime本树已通过真实SQLite/Harness + deterministic Provider测试：无scope实际完成、
重开/终态事务故障恢复、真实消息投影、unscoped队首后scoped FIFO，以及None→生产
context_route.resume_existing→工具发现/激活→生产write_file→semantic closure/terminal。
项目effect从真实route/binding验证exact root，保留scoped冻结检查；binding head更新负例不落盘。
统一terminal_identity先验Host receipt/binding/observation或ExecutionEvidence链，再比raw
SDK event ID/hash/state；旧式scoped fixture不删除原始证据。helper用于runtime/API共享解释。
轻量state_changed接现control broadcast空payload，独立合并限时500ms；control请求事务前
重验原verified connection/epoch/lease，保留原撤销barrier。
2026-09-05聚焦181 passed（含动态正负、SDK身份替换拒绝、crash/reopen及通知），无App/真实Provider。
main已惰性注入API三reader/resolver，**依赖Dirac API提交及terminal helper接入后继进行组合验证**。
**完整历史Memory/evidence/entity来源suppression留下一提交，尚未闭合**；当前候选不得据此
合main或宣称S6 Task1/2完成。create_new Manual路径也未有新UI验收，不走旧目录卡/external wait。

CREATE_NEW候选62f44631曾发现generation origin P1，后继冻结原Host/SDK Run、owner/gen，
首次选择与写锁内commit前重复核验；真实reclaim/终态后旧context回归及相关套件85 passed。
origin c04912f9已获Dirac独立限定ACCEPT（9 passed）。首tool启动竞态后继用本次启动Event
等待真实Host RUNNING持久化，再按原owner/gen/state授权；不放宽CLAIMED，5秒有界失败。
无Provider等待的真实首tool正例/期间reclaim拒绝及相关套件87 passed；待独立复核及新项目native验收。

CREATE_NEW后继已完成生产backend修复：配置CanonicalWorkspaceRoot取canonical_path后使用稳定task子目录；
service先持久化真实binding proposal；active None Run以实际Run/context+owned目标scope取得原AUTO authority，
不走无Run bootstrap，不改变admission scope。Manual返回可消费真challenge，旧scoped不可跨scope。
相关回归66 passed（含5条新增、真实AUTO落盘/terminal/重开、lease丢失及伪造evidence负例）。
Manual UI/失败结果投影仍未接通，完整Memory-forget history仍未闭合。详见
[CREATE_NEW交付](../plans/2026-09-05-s6-primary-preparation/CREATE-NEW-BINDING.md)。

本记录仅为隔离分支状态，未合main；不表示S6 Task1/2或program验收完成。

实现/命令/原始证据hash与交叉点见
[实施交接](../plans/2026-09-05-s6-primary-preparation/IMPLEMENTATION.md)。

## 2026-09-05 Harness 0.7.2 接入与当前验证

Host `8d57441517836aaaa30ac16a33576f4d68a9d1ad` 已安装 Harness 0.7.2，source
`2b8428465cbd41032ba024a0b7199183161f5ecd`，wheel SHA-256
`53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`；Memory 保持
0.6.3 / `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`。
版本、manifest 和安装身份核验通过；SDK 独立复核 151 个包文件与源码/安装字节一致，
110 个已导入模块均来自 Host venv，安装版定向回归 17 passed。

A14 两个独立真实 gpt-5.5 / queue.enqueue root 均完成 README 1.1.3→1.2.0、TaskScope
语义收口、终态 outbox 与 Memory accepted plan，每个 root 物化 episode+semantic 两个 head。
root 为 `2fa7d7b1-3430-5052-bc10-5cfb77beb32a`、`870babc5-3842-56d7-bae5-05d1477119c0`；
前台调用分别 13/12 次，analysis 各 1 次，无重发。旧 0.7.1 失败证据保留。
独立 AI 质量复核确认原句/spans/实际效果/绑定一致；发现 episode 误用 analysis 时间的 P2 已改用首次 Host committed_at，26 条聚焦回归通过、主执行者复审接受，真实生产复验待执行；
另一个补读 before 来源 P2 已用衍生纠正包闭合并独立接受，原封口 834 文件未改。
不能把两条机械通过当成 S5b 完成，也不将物化计作 typed recall 命中（A15 仍 NOT_MEASURED）。

当前 native UI 的隔离源 backend / gpt-5.5 root `a48a396c440054e299af4094b273f2db`
已收到真实非空回复；只覆盖 chat 冷启动，S6 唯一主对话/queue UI 仍未交付。
Host 全量在上述 HEAD 得到 6535 passed / 6 failed / 47 skipped / 6 deselected；
一个新增失败为 exact candidate 测试的旧 hash 字面量，更新当前 hash 并保留旧 hash 负例后整文件 20 passed。
其余五个失败属于历史七节点子集，其中 downgrade 原因变化仍在独立核验，不能直接豁免；
另外两历史节点已通过。6 deselected 全来自默认 marker，命令的显式 deselect 未匹配；没有测试挂起。
本次还完成 S2 聚焦 49 passed、S3/S4 聚焦 96 passed、S7 聚焦 16 passed；各日志明确测试层级。
完整回归退出码与原失败保留，尚未得到 machine finalize PASS。

本机证据根 `.local-test-evidence/2026-09-05/human-memory-resume/`：
`tools/a14-20260905T093324-p0e5cu3m/`、`independent-review/a14-quality-093324/`、
`tools/derived-correction-recollection-before-20260905T095723/`、
`independent-review/a14-q2-correction/`、`reg-full-host/REPORT.md`、
`host-final-candidate-pin-retest.log`、`tauri-app-harness072-resolved.log`。
原始文件及 SHA 索引均 ignored；未 push/tag/发布。S3 另在隔离分支补公共执行桥，S5c/S6 未交付。

## 2026-09-05 早期 S5b 恢复修复与候选记录

当前 Host 使用 Harness 0.7.1 / Memory 0.6.3 / Service 0.3.12；上方 0.4.0 发布与下方
早期 S4 状态是历史验收记录。Memory source `2f3d73814fe6a884e0458d87567b918c5863033e`，
wheel SHA-256 `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`。
双次构建字节一致，版本/来源/hash 校验通过；未 push/tag/发布。

Memory 按 principal 等待已领取但未物化的 analysis batch；恢复沿用固定结果、plan、base_revision
与 evidence，不通过改写 revision 或追加 Provider 调用挽救旧结果。合法 no_mutation 的可选
closure_reason 被原样持久与恢复，仍零认知写入；不可用响应继续 rejected。两项修复独立复审接受。
公共 API 和 schema v7.1 保持原契约，旧 0.6.2 wheel 保留。

当前精确 wheel 的 Host 集成 51 passed、Memory 恢复/API 16 passed；原始证据在
`.local-test-evidence/2026-09-05/human-memory-resume/`。新 wheel 原生 UI/gpt-5.5 root
`c2af5326a8d05023868f7994f1a4e0be` 已取得非空回复；r5 保留早期失败，S8 FLAKY。
A14 真实 queue.enqueue root `142bdb3b-9026-5264-b244-69e94bf0e388` 写 README 后被 Harness 0.7.1
initial/current route 恢复 P1 阻断：terminal FAILED、closure pending、accepted/head=0。
用户已批准 A17 限定 SDK route 修复和必需 port/回归/新候选接入，其余 SDK 功能继续冻结；
本次只记录事实与证据，不改 Memory plan/base_revision/evidence，不改产品源码/pin。
原始失败与 metadata 位于上述根的 `verification/r5-local/artifacts/s1-route-failure/`；
S5b 机器门及 program 未完成，A15 typed recall 仍 NOT_MEASURED，交 S5c/S6。

## 2026-09-01 Human Memory Program Host evidence、Canonical Archive、Task Home 与 Binding（S4 Task 1–4）

- Host 已新增 opt-in `human-memory-v1` 基础，并以不改写 v35–v37 checksum 的追加 migration 升至 state schema
  v38。该 epoch 只能从空数据库取得 durable
  bootstrap marker 后初始化；普通 state.db 启动仍停在 v34，不会因为新 migration 文件存在而升级。任何既有
  v34/更旧数据库从新的 primary 入口打开时，都在 backup、reset、migration 和业务写入之前稳定拒绝；不迁移、
  不删除，也不展示旧 Session。
- v35 对每个 authenticated subject 以 partial unique constraint 保证唯一 writable
  `primary_conversation_id`，并在同一事务写 immutable init receipt。并发冷初始化、v35 commit 前/后故障和重启
  均收敛到同一个 primary identity。
- 新 `HumanMemoryProgramStore` 只接受具备 S1 `SanitizedEvidenceEnvelope` / `SanitizedEvidenceReceipt`
  冻结结构、独立 canonical hash 校验和 receipt binding 的输入；receipt 先写，随后在同一事务 append
  user/assistant/tool/provider/run evidence。Provider payload 使用 public allowlist；认证字段、credential value
  canary、隐藏 reasoning 和私有扩展在持久化前 fail closed。raw evidence、sanitization receipt、primary identity、init/format marker 均有
  SQLite `BEFORE UPDATE/DELETE` 拒绝 trigger，纠正与遗忘必须由后续 append-only lineage 表达。
- v36 新增 Canonical TaskScope Archive：`task_scopes` identity、Host-native turn/file/test event、LLM mutation
  attempt/decision、step fact、evidence link、canonical revision、immutable checkpoint 及 projection/search outbox
  均在 Host state.db 留下永久、可重放记录。LLM `TaskScopeMutationPlan` 先做冻结结构、对象/JSON、canonical hash、
  disclosure、evidence lineage 和 credential/private-field 校验，再以 `base_revision` CAS 在单事务写 mutation
  decision/event/canonical revision/projection/search outbox；CAS 冲突只追加 attempt 审计，不产生新 revision。
- S1 `ExecutionEvidence` ingress 以 `source_event_id + evidence_hash` 幂等，持久保存 receipt、run cursor 与连续
  durable watermark；乱序 terminal 不可跨过缺失 sequence，只有 durable watermark 到达 terminal source
  sequence 才能生成 immutable terminal gate receipt。这里仅是 Host ingress/gate seam，尚未接入正式 foreground
  composition 或真实 Provider 链。
- TaskScope raw event/evidence link/decision/attempt/canonical revision/checkpoint/outbox 禁止 physical UPDATE/DELETE；
  head、watermark 与 projection cache 是可重算协调状态。projection cache 可删除，并已验证能从 canonical
  revision 逐字节等价重建；这不是 README/STATUS 等用户阅读视图，也未实现 search consumer。
- v37 新增 `reserved → filesystem_ready → committed | failed_retryable` 的 TaskScope task-home provisioning。
  managed 模式只在 configured managed workspace root 的真实子目录创建 `<safe-title>--<scope-digest>`；macOS/Linux
  未配置时使用 `~/SimpleHarnessWorkSpace`。explicit 模式只接受 trusted user selection/project picker 且目标必须
  是已存在、非 symlink 的 exact directory；项目允许管理元数据时 task home 位于
  `.simple-harness/task-scopes/<id>`，否则落 private app-data。稳定 staging+marker+filesystem identity 让每个故障
  边界重启都收敛同一路径，不以第二目录掩盖失败。
- POSIX materialization 持久冻结独立 `materialization_root` identity，并以 `O_DIRECTORY|O_NOFOLLOW` directory fd
  逐级创建/打开父目录；staging marker 与 no-replace publish 全部锚定该 fd。Darwin 使用
  `renameatx_np(RENAME_EXCL)`，Linux 使用 `renameat2(RENAME_NOREPLACE)`；缺少等价原语的平台稳定 fail-closed。
  broken symlink、resolve 后 root rename/replace、receipt commit/reopen 前 root 或 task-home identity/containment 漂移
  均不得生成 committed receipt。
- Provision receipt 只有 final committed 才存在；reserved/filesystem_ready/failed 都不形成 workspace authority。
  `proposed_workspace_root` 仍只是候选。v38 已新增 append-only binding proposal/challenge/decision/grant/set
  revisions：Manual 必须重载 durable authenticated user evidence/interaction，Auto 必须重载 Host-issued current-Run
  snapshot 并在 append/commit 前复核 active Run、context/config revision、时窗与 configured-root filesystem
  identity。task home 与 binding receipts 均不可物理改写。
- 当前 Host Human Memory candidate 精确固定 Harness SDK 0.7.0；Task 1–4 以 strict public DTO、真实 source DTO
  interoperability 与 durable authority restart/fault probes 验证，**不**把 fake DTO 当成生产集成。六阅读视图、
  candidate search/exact open、foreground FIFO 与 S4 Host integration/recovery 尚未实现；S5 才接 main-model
  route/recall/context/tool composition，S6 才切 UI。因此当前能力不作为产品成功声明或默认新入口。
- 决定性回归：提交态新增/迁移/既有 Session 组合 `67 passed`，SDK adapters `253 passed`。受影响后端
  m–r 分片先跑 `1497 passed`，提交态复跑为 `1496 passed, 26 skipped, 1 deselected, 1 failed`；唯一失败是
  已登记的环境型 `test_process_list_with_query` process-name filter 基线红，与本 slice 无调用/文件依赖。真实 S1
  DTO source interop probe PASS；没有 UI 或真实 Provider evidence 声明。原始本地 probe 数据仅保存在 ignored
  `.local-test-evidence/`。
- Task 2 新增 archive/ingress 专项 `8 passed`；与 Task 1 memory/session/SessionDB 组合 `75 passed`，SDK
  adapters `253 passed`，真实 S1 source `ExecutionEvidence` + `TaskScopeMutationPlan` DTO interoperability probe
  PASS。上述均为自动化/源码协议证据，没有 UI、真实 Provider 或 production composition 声明。
- Task 3 provisioning 专项 `18 passed`，Task 1–3 相关组合 `93 passed`，SDK adapters `253 passed`；覆盖全部七个
  operational fault 边界、v37 migration commit 前/后、nonexistent/symlink/untrusted explicit path、permission
  failure/retry、duplicate title、idempotency conflict、project/app-data metadata、broken-link escape、managed/explicit
  root replacement TOCTOU 和 no-binding/no-partial-authority。
- Task 4 binding 专项 `15 passed`；Task 1–4/marker/candidate 与 SDK adapter 组合 `322 passed`。当前明确未实现：
  `task_scope/projections.py`（六 bounded views/checkpoint verifier）、`task_scope/search.py`（permission-first
  candidate search/exact open）、`execution/foreground_queue.py`（单 foreground Run/FIFO）、
  `execution/recovery_fence.py` 及 `backend/main.py` 的 fresh Host service/旧入口 fence/emergency export 接线。

## 1. 当前生产链路

```text
Tauri/React chat/chat_v2
  -> validated local HumanIdentity.identity_namespace_hash
  -> immutable deployment/household/actor/session binding
  -> root 或 continuation 独立 immutable Context source snapshot
  -> Harness ConversationTurnInput / ConversationContinuationInput
  -> SDK durable context claim
  -> SDK 调 read-only product Context provider + MemoryManager recall
  -> frozen Context stage（Memory 始终按 untrusted data）
  -> Provider / Tool / recovery 复用同一 stage
  -> completed terminal committed-turn outbox
  -> MemoryManager.record_committed_turn
```

产品不再调用 `prepare_consumer_conversation_context`，也不再构造 `ConversationMemoryAdapter`、manual recall
query 或 query/sink 双口。Harness 的正式 `AgentMemoryPort` 与 `ConversationContextProviderPort` 是前台唯一
自动 Context/Memory 组合。

## 2. 资源与身份 ownership

| 事实/资源 | owner | 当前边界 |
|---|---|---|
| Session/UI message、delivery、Provider usage、非 Harness outbox | `state.db` / SessionDB | 产品投影事实 |
| Run、Context stage、Provider invocation、committed-turn outbox | Harness execution v4 DB | SDK 执行事实 |
| Messages/Facts/Twin/recall snapshot/write fence | Memory SDK v4 DB | 长期 Memory 事实 |
| Persona/历史/Skill/附件/project/task source | simple_harness content-addressed repository | provider 只读；同 ref 同 bytes |
| MemoryManager 生命周期 | simple_harness process | production builder 构造一次；Runtime `BORROWED`；shutdown 先关 runtime borrowers，再由 SessionDB 有界 drain/关闭 manager 一次 |

身份只来自 `LocalAuthSnapshotProvider.current_snapshot()` 经
`validate_auth_snapshot(..., user_data_dir=...)` 得到的 `HumanIdentity.identity_namespace_hash`。
`deployment_id` 来自 `state_db_identity.instance_id`；首次 actor 获得随机稳定 household；同 session 不可换绑。
模型、payload、Provider 配置、API key 与 legacy `profile_id` 均不能提供或覆盖 actor；身份损坏在 LLM 前
fail closed。

## 3. Context source durability

- root 与每个 continuation 各自生成 content-addressed immutable source ref；continuation 不继承 root ref。
- ingress 原子创建带 lease 的 `PENDING` binding；SDK durable accept 后标 `CLAIMED`；stage/terminal 后进入
  `STAGED` / `CONSUMED`。
- terminal 释放 root 与全部 continuation refcount；共享 hash 不会被单个 binding 误删。
- orphan cleanup 只处理超过 horizon/lease 且 execution claim-inspector 证明无引用的记录；inspector 故障
  保留重试。
- provider 只读 source snapshot，校验 canonical hash、item/byte bounds，不写产品数据库。

## 4. 写入 authority 与工具面

- Harness foreground message 使用 execution committed-turn outbox；`FAILED` / `CANCELLED` 不生成长期 Turn。
- Companion/background/非 Harness message 保留 `product_memory_outbox`，经同一个 MemoryManager 的 explicit
  projection 写入。`memory_authority=harness|product|none` 保证同一消息不进两套 authority。
- ordinary foreground catalog 不再暴露可触发第二次 live recall 的 `memory_recall` / `memory_search`。
- simple_harness 现有显式 remember/read/forget 工具从 resolver 的完整 deployment/household/actor/session
  构造可信 `MemoryPrincipal`；write 调正式 `remember_fact` 并保留 salience/pinned/tier，返回准确 fact ID；
  read 调 `read_fact`，不再按 legacy user 扫描 facts。独立 event key 的同 payload 重试保持同 ID，元数据变化
  conflict，跨 principal 不可读/不可重放。forget 显式传 `source_event_id`，由 SDK canonicalize payload hash；
  首次 action 与重放返回同一结果，后续独立 action 对已删除 fact 稳定返回 false，receipt 跨重启保持且不复活；
  自然语言遗忘仍按安全例外关闭。
- `MemoryManager.share_fact(principal, fact_id)` 是 Memory SDK 正式授权分享接口；本轮不为 simple_harness
  新增 `memory_share` Tool/UI，供后续 K6/AgentOS、NovelTagSystem、AI Phone 消费。

## 5. 恢复、迁移与 DEV fault

- 产品 v4 coordinator 只调用两 SDK 的公开 migrator；先备份两库并写 owner-only journal，任一步失败恢复
  all-old pair，完整 hash 验证后才保留 all-new pair。
- recall timeout 按 SDK policy 降级为空 frozen stage；record transient 不回滚成功响应，由 durable outbox
  重试收敛。
- fault wrapper 仅在 `DESKPET_DEV_MODE=1` 且 user-data 位于仓库 `.local-test-evidence` 时允许装配；其他路径
  fail closed。

## 6. 当前验证状态

- exact wheel SHA/direct-url installed-origin 与 candidate conformance：PASS。
- Harness full：`1379 passed, 2 skipped`；Memory 默认 full：`200 passed, 7 skipped`，正式 candidate gate：
  `205 passed, 2 skipped`。
- 产品最终聚焦：backend `83 passed`；MemoryPanel `18 passed`；TypeScript typecheck PASS。
- 产品 full baseline：15 shards PASS、2 个实施前 known-red（root live fixture、ESLint 171 fingerprint），0 unexpected。
- SH-I01：同 user-data 重启稳定、Provider/API key/model/payload spoof 不影响、跨 user-data 隔离、损坏身份/
  错误 snapshot 在 LLM 前拒绝、legacy profile 排除：PASS。
- 真实 UI：SH-M1～SH-M6、SH-SURFACE 全 PASS。SH-M5 按冻结 exact oracle 跨进程新 Session 召回
  `Max`；`Aurora-R4` 只属于早期隔离 canary。SH-M6 以进程环境 attestation 直接证明 recall timeout
  fixture 已启用且主 Turn 不受阻断；record transient 在未写入时退出后由 startup recovery 唯一收敛，
  新 Session 回答“晚饭后”。SH-SURFACE 已在当前构建真实打开 macOS 附件选择器并以 Esc 安全取消。
- r7 独立审计因附件截图错配、recall fault 缺直接 attestation、S6-A8 状态文字与导入 custody 不完整而
  判 FAIL；这些证据/文档缺口已在继任 Gate 输入前修复，r7 不作为发布 receipt。原始截图、日志、进程
  attestation 和 Gate ledger 仅在 ignored `.local-test-evidence/2026-08-22/`，Git 只保存结论与 hash 索引。
