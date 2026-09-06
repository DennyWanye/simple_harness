最后更新：2026-09-07。C01-06/11正式session分派独立源码叶（base61f474e5）：06接真实job→同ID公开REVISE→关闭fixture→main重开，11沿已合可信Context时钟解除旧block。本叶NOT_RUN/待主完整候选验证，不沿用helper绿冒称正式评分通过；C08 partial/C05未接门保持。[契约](../plans/2026-09-07-corpus-complete-dispatch/CONTRACT.md)。

最后更新：2026-09-07。主H0710/M619完整来源组合新增3唯一控制分批通过：C01同ID修订/公开选新版、可信日期冻结与跨日；C07 actualmain真实recent fixture终态后独立scoring Run/统计，评分HTTP受控。r1两绿+C07错误oracle红，r2只红1PASS7.28s，PG80368/80594皆清空；无WeMM实际加载，非真实模型质量。06/11正式评分适配仍待接，服务model_not_found独立阻塞。[结果](../plans/2026-09-07-corpus-c01-scoring/MAIN-PHASE-CLOCK.md)。

最后更新：2026-09-07。C08标量准备叶7022e8e0/287176d0在H0710/M619/S0313首批13PASS5.55s，PG79749自然清空。12事实真实APPLIED+ACCEPTED/抑制前非空→公开EVIDENCE suppression→冷重开隐藏且S1保留；其中01/02/04/09显式partial、另8case派生源未支持，不称12完整setup或模型质量。正式评分接线仍待。[结果与未完边界](../plans/2026-09-07-corpus-c08-prepare/RESULTS.md)。

最后更新：2026-09-07。计分叶ebd81721修缺response时exact预测指标误零，改为null并保留lower_bound；失败denominator/credit不变，旧r4不覆写。仅affected真实SDK failed单Run控制1PASS0.91s，PG78605自然清空；非模型质量。[指标与结果](../plans/2026-09-07-corpus-c01-scoring/MISSING-RESPONSE-METRICS.md)。

最后更新：2026-09-07。独立单POST诊断收到HTTP400/model_not_found，param=model，message unknown provider for model gpt-5.5；1post/0工具，PG77944正常退出无残留。只证明该次拒绝，不追认原r4同因、不称nullable线上通过。主另报告/models列该模型，清单不等于POST可用，暂停进一步请求并等待模型取舍。[受限结论与审核证据](../plans/2026-09-07-corpus-c01-scoring/HTTP-REJECTION.md)。

最后更新：2026-09-07。C07独立准备叶（业务ade43237/测试修41296300）在原H079/M619载体分批6个唯一控制通过：20原setup编译边界、3种真实非空seed/job/public冷回读、06/14真实最近组→下一确定性请求。首批同因字段5红保留；PG77451正常退出无残留。只证明helper/Context准备，不是20条实际评分READY；正式06/14评分Provider相位、标量actualmain组合及模型质量仍未验，不改S3完成度。Dirac限定终审已接受并接入隔离主候选。[结果与边界](../plans/2026-09-07-corpus-c07-prepare/RESULTS.md)。

最后更新：2026-09-07。H0710/M619/S0313实际main安装组合1PASS6.38s，PG76882自然退出清空。新候选eaa72b51显式复验C01-20仅1请求HTTP400、无模型响应或工具、EXECUTION_FAILED；原因旧日志不可恢复，后继有界诊断已接入，不猜原因。PG76962自然退出9.136s且清空。240历史3个不同case/0通过，缺响应不算零extra的质量成功；原生仍待，防熄屏持续。[安装态](../plans/2026-09-07-corpus-c01-scoring/INSTALLED-0710619.md)／[真实复验](../plans/2026-09-07-corpus-c01-scoring/REAL-R4.md)。

最后更新：2026-09-07。Host HTTP拒绝诊断叶0be92572/60e6ea88：r4原400未保存body/private_cause，原因不可回溯。借原client.post在SDK拒绝前记录白名单有界脱敏字段/bytes/hash，不改状态分类、nullable或重试；新增本地HTTP组合1PASS0.01s，PG77306正常清空。仅已注入secret脱敏，非未知凭据检测；尚无真实服务拒绝原因，缺response不能把extra0当观测零。原FAIL保留。[事实与结果](../plans/2026-09-07-corpus-c01-scoring/HTTP-REJECTION.md)。

最后更新：2026-09-07。Harness0.7.10已从审定031fdc6不可变源离线构建一次并从vendor安装新H0710/M619/S0313 target，174/92/116成员逐字节一致。Host nullable叶与生产pin/lock/manifest同批接入；锁检查通过。4个源控制分批通过，当前installed功能组合/失败case复验及原生仍待。旧H079制品与三原FAIL保留。[制品与边界](../plans/2026-09-07-corpus-c01-scoring/INSTALLED-0710619.md)。

最后更新：2026-09-07。nullable后继Host2d64e6e5/fad81ebb配SDK031fdc6/0.7.10 source新增4唯一控制通过；仅两workspace/source字段允许JSON null，3reuse判断一致，非适用hash拒绝，rawhash与exact绑定不归一。原夹具红保留，PG76045清空；需主统一新wheel/installed组合后使用（旧H079不支持），未称main/模型质量通过，原3case FAIL保留。[契约与结果](../plans/2026-09-07-corpus-c01-scoring/NULLABLE.md)。

最后更新：2026-09-07。Manual组合原生UI固定e1e714d2已一次完整TypeScript/Vite/Rust/app构建通过；独立bundle端口18120，PG72450正常退出清空132.283s。尚未启动；先待SDK nullable继任/主组合及失败链复验，再用本UI验收。仅后端变化不重复同UI构建，防熄屏持续。[构建](../plans/2026-09-07-manual-workspace-binding/BUILD.md)。

最后更新：2026-09-07。C01-20固定2c02be03首次真实评分仍FAIL：4Provider/3路由拒绝，无A/B；明确nonstrict与omit指引未解决实际环境。PG71822自然退出51.88s且清空。240历史3个不同case尝试/0通过，暂停扩跑同故障；推进SDK可选null支持，修后显式新候选复验失败链，旧FAIL保留。防熄屏持续。[真实结果](../plans/2026-09-07-corpus-c01-scoring/REAL-R3.md)。

最后更新：2026-09-07。Host9073b965显式发送function.strict=false，保留原optional参数/精确workspace reuse校验，并给memory_standalone省略字段的公开失败指导。Dirac源窄审后唯一fakeHTTP→SDK参数→Host handler/ledger组合1PASS0.19s，PG71603正常退出无残留；空recall/合成tool context只证明协议路由，不代表真实relay/main或质量。C01-10/13原FAIL均保留（2尝试0通过），新真实case另验。[合同及结果](../plans/2026-09-07-corpus-c01-scoring/NONSTRICT.md)。

最后更新：2026-09-07。Manual workspace UI产品ef0ed7bf/夹具修48169ae8/结果7324a740已独审接受并合候选；真实Host授权链与View父卸载恢复7backend＋4UI分批通过，原红保留，PG69778清空。包含工具发现说明的事实修正，尚不宣称解决模型反复搜索；组合构建/native、App进程冷启动自动发现仍待验。[结果](../plans/2026-09-07-manual-workspace-binding/RESULTS.md)。

最后更新：2026-09-07。新C01-13真实324aa613首次评分FAIL：15物理请求/14次路由因无关workspace参数拒绝，未取得A；原提议四类型extra3保留。176.565s自然退出且PG69877清空，退出修复真实生效。240已尝试2/通过0；暂停同故障路径扩跑，修参数无值契约与核预算跨恢复计数。全阶段防熄屏保持。[真实结果](../plans/2026-09-07-corpus-c01-scoring/REAL-R2.md)。

最后更新：2026-09-07。Procedure prompt/v5.1叶342e2722/20f58862已独审：6限定控制通过；2次真实分类与public strict mutation提交通过（未采用流程→DRAFT+Episode，一次性任务→仅Episode），零重试，PG69158正常退出。旧v3/v4/v5持久请求保留；这只是Provider适配器/编译/公开写入，durable分析job与原生完整链仍待验，原r24FAIL保留。[真实分类](../plans/2026-09-07-procedure-draft-classification/MODEL-RESULTS.md)。

最后更新：2026-09-07。评分自然退出叶ab36b6a5：WorkflowRunner独立UoW owner原未释放，补public runner/service close与main/carrier统一收尾；bootstrap明确服务拥有共享端口UoW，runner不关借用端口。唯一独立child实际main执行自然SystemExit控制1PASS17.01s，PG69388清空，无pytest全局lane清理代替。原C01-10语义FAIL及deadline保留，下一新case质量另验。[定位与结果](../plans/2026-09-07-corpus-c01-scoring/PROCESS-EXIT.md)。

最后更新：2026-09-07。首真实C01-10固定30b07393/H079/M619：1物理请求、0工具，排序正确但未取得已存A，原gold FAIL（主审+独审）；240已尝试1/通过0。业务COMPLETED后worker线程退场挂起，180s外部deadline退出125并清空PG67059，非内存/磁盘门。修复退出与通用记忆来源指导继续，均未称通过。全阶段防熄屏保持。[真实结果](../plans/2026-09-07-corpus-c01-scoring/REAL-R1.md)。

最后更新：2026-09-07。C01真实交互补丁222346d3/ac76e16a：可选精确公开审批仅允许memory_standalone，未知等待/非白名单BLOCKED；接原main ingress打开barrier。实际main/SDK新增组合1PASS16.83s（只HTTP delegate固定，权限/handler/终态真实），原ingressclosed失败保留；PG66376清空，最低磁盘599MiB，首真实C01须恢复默认准入再执行。本地WeMM实际加载，非真实LLM评分。[结果](../plans/2026-09-07-corpus-c01-scoring/APPROVAL-RESULTS.md)。

最后更新：2026-09-07。C01评分叶99d17c11：真实main Memory初始化/关闭与gold隔离、真实未终态attempt保存红2修后通过，连同先前FAILED参数共3唯一无网络控制；PG65098/65129清空，旧失败保留。原coroutine diagnostics警告单列，不扩改。首C01-10已合候选，实际评分另验，当前模型评分0，不是质量PASS。[控制与准确运行命令](../plans/2026-09-07-corpus-c01-scoring/RESULTS.md)。
最后更新：2026-09-07。新构建原生r25固定d86e4805/H079/M619冷恢复与两次实际授权可用；首查询错把taskactive当流程状态，澄清后实际Procedure发现返回0且模型如实答无。正向草稿/完整Procedure仍未验收，240质量不计。PG62018正常退出清空，退出后仅清可再生构建缓存，防熄屏继续。[结果](../plans/2026-09-06-typed-use-primary/NATIVE-R25.md)。

最后更新：2026-09-07。主d86e4805（产品0e146792）与H079/M619/S0313安装组合仅Auto原root新Scope写入/alreadyBound拒绝2PASS4.05s；205已加载SDK模块属指定target，无重复全成员核验。PG61943 exit0/remaining[]已交native槽。原7unique不重复累计，Manual UI与原生仍待，原失败保留。[组合事实](../plans/2026-09-06-completed-scope-continuation/COMBINATION-619.md)。

最后更新：2026-09-07。已完成项目续改独立叶：新Run公开search取得旧complete Scope/source，create_new经真实权限将新active Scope绑定原root，再实际工具写原文件；旧Scope不重开。同Run已绑定时在创建前及route同TX拒绝，下一物理请求给明确新Run指导。H079/M618确定性栈7个唯一控制分批PASS，最终源1a8e1dd6/Dirac限定ACCEPT；本次Auto/Manual两绿+alreadyBound双层hash修正单绿，PG57258 exit0/remaining[]已交槽，原业务/fixture/oracle失败全保留。仅AUTO配置root及公开Manual service路径；Manual UI、主组合和原生仍待，非program完成。已独审合入隔离主候选，用户主checkout未切换。[契约与结果](../plans/2026-09-06-completed-scope-continuation/RESULTS.md)。

最后更新：2026-09-07。原生r24固定b2da14da/H079/M619，待定流程记录可见；第二轮界面等待授权但停止后补出成功context_route及4次tool_search，Procedure发现/使用和文件核验未完成。PG50771正常退出且清空，非内存/预算阻塞。全测试阶段防熄屏保持。[现场与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R24.md)。

<!-- 最后更新：2026-09-07 -->

C05 固定 f78004ef 在 H079/M619 installed 的原5红定向复验5PASS/12.15s，3绿未重跑。
04/09/14真实material marker→closure→来源绑定字段、20归档/prefix、真实评分分页与late suppression精确USER-only通过。
仅确定性fixture/public runtime，不计模型质量或C05全部20准备；最终physical outbound race及其他case接线仍待完成。
PG80017 exit0/remaining=[]，原两批红保留、WIP隔离，已释放资源。
[来源、命令及历史结果](../plans/2026-09-06-corpus-public-seed/C05-RESULTS.md)。

<!-- 最后更新：2026-09-07 -->

2026-09-07 主0abdf048/H079/M619/S0313独立installed组合仅C04-12新增1PASS/1.03s，PG57440清空；与原H078/M618叶证据分开，非质量执行。

C04 原20公开setup分批19+1通过；实际same-timestamp晚append暴露Host游标漏注册，
d3a580be复用原journal修复当前timestamp边界、分离scan高水位与消费CAS，无DDL/旧receipt改写。
新增5控分批通过（测试helper d25fe2f6），原绿未重跑；晚到更早timestamp仍不保证。
实际C04-12注册/失效/改期ACK链通过；terminal故障控是实际SDKreceipt+显式scripted Host高位cursor。
H078/M618 installed，非H079/native/Provider/240质量；Dirac对d3a580be/d25fe2f6/6b1f886a最终限定ACCEPT；非完整consumer not_required遍历。
[结果与边界](../plans/2026-09-06-corpus-public-seed/C04-CURSOR-RESULTS.md)。

最后更新：2026-09-07。共同Memory0.6.19 clean源e27003c已离线只构建一次，H079/M619/S0313安装新组合1PASS0.86s、174/92/116成员和184加载模块精确来自target；版本3控通过。Host vendor/pin/lock/生产identity固定新wheel，初次origin校验失败后通过真实vendor安装纠正，不手改metadata/不重build；PG50135清空。旧M618不改，当前候选可供M619原生验证，完整native/240质量待验。[制品与实际结果](../plans/2026-09-07-current-input-procedure/INSTALLED-079619.md)。

最后更新：2026-09-07。Host80764c13/共同Memorya15c7be源组合1PASS0.82s并独审接受：真实签名当前输入与独立Procedure draft同批前均可见，公开遗忘后只draft拒绝，当前项不受误伤；Host审计请求/快照精确绑定。PG49417清空，原属性oracle红保留。Memory新0.6.19制品/installed/native另验。[结果](../plans/2026-09-07-current-input-procedure/RESULTS.md)。

最后更新：2026-09-07。Procedure恢复/发现固定5ca45216已独审合入隔离候选源码：旧恢复13项限定通过；新发现链有效6项为首批有效4+实际遗忘负控2，旧时钟异常误绿已撤回。原signal lane、context page reader与current-input接线均保留，依赖聚合含v3 draft。共同Memory新制品/当前安装组合和native完整TC04仍待验，旧M618不能启动此候选。[新发现结果](../plans/2026-09-06-procedure-adoption/DISCOVERY-RESULTS.md)／[恢复结果](../plans/2026-09-06-procedure-adoption/RECOVERY-RESULTS.md)。

最后更新：2026-09-06。r19收尾指导产品a189的实际运行链2个唯一控制已独审接受并合入：真实原任务目标/未回读债务保留，完成Scope的两次拒绝与公开tool proposal/下一物理输入精确关联、无文件写入。原测试oracle两红保留、修后只复跑红1；最终PG47277清空。不是模型/native质量通过，旧root新activeScope续改仍独立实现。[控制与边界](../plans/2026-09-06-completed-scope-guidance/RESULTS.md)。

最后更新：2026-09-06。已审非SELF本轮输入消费者c0fbe30a接入隔离候选源码，保留既有提醒signal authority；组合需Memory后继的新current-input公开API，当前旧M618 pin不能作为此源码可启动证明。在共同Memory源码1df01d1审查/新制品及安装组合完成前暂停该候选原生启动，用户主checkout未变。旧9项源验不重跑。[来源与边界](../plans/2026-09-06-nonself-input/RESULTS.md)。

最后更新：2026-09-06。合入prepare叶后的Host9cace208，当前H079/M618/S0313新增C02完整prepare跨进程lostACK组合1PASS4.48s，174/84/116成员精确、188加载SDK来自target；PG46613清空。旧H078套件不重跑，C03新组合/240质量不外推。[组合结果](../plans/2026-09-06-corpus-public-seed/H079-PREPARE.md)。

最后更新：2026-09-06。C02-19/C03-20完整fixture prepare与跨进程恢复叶e0e7d68c（产品182a5aa6）已独审合入候选：public seed后实际drain，finalize前保存原候选、重开经SDK确认；2新控制PASS7.30s、PG34647清空，旧绿未重跑。限定H078/M618源运行证据，当前H079完整prepare组合待验，240质量仍0。[准备与恢复](../plans/2026-09-06-corpus-public-seed/INFERENCE-PREPARE-RECOVERY.md)。

最后更新：2026-09-06。固定ff35fb82/H079/M618正确18120新构建，r22真实新松柏提醒ACK后独立“提醒”正文可见；r23冷启动保留同一历史回执/提醒，后续普通问题只答44无新增提醒，两项限定通过。PG42213/45599正常退出且清空。前置r20 carrier异常原因未定、r21编译端口错误已纠正；原r18FAIL保留，完整旅程/240质量仍未完成。[原生与资源证据](../plans/2026-09-06-typed-use-primary/NATIVE-R20-R23.md)。

2026-09-06：提醒独立正文notice叶26c19b5e已独审合候选，产品1355c5b7，新7backend/2UI分批通过。新ACK投影独立reminder，不改模型原答或旧ACK，合法改期撤旧notice，保留原r18FAIL；真实原生正文/新构建仍待验。[源码与控制](../plans/2026-09-06-prospective-ack-notice/RESULTS.md)。

2026-09-06 原生r19独立长旅程仅前5轮：真实任务/docx创建但漏readback；原任务被模型收尾为complete，后续resume路由成功但编辑被生命周期门拒绝，第4轮FAIL并原生停止；随后43正常。完整两组旅程未完成，PG29074正常退出清空，非内存阻塞。[现场与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R19.md)。

2026-09-06：固定3d83ac81的C03两来源收尾在当前H079/M618安装组合新增1PASS/2.25s，PG28861清空；189加载SDK模块来自target，原H078其余绿不重跑。不计质量语料，C02/自动prepare/跨进程proof另验。[组合证据](../plans/2026-09-06-corpus-public-seed/H079-COMPOSITION.md)。

2026-09-06 C03与推断准备收尾已审叶bfd56d99合入候选：C03全部20条setup分批通过，C01–C03共60条准备验证；240真实质量仍0。C03-20两来源实际SDK job的合法无修改收尾、非法分析虽APPLIED但拒绝确认、取消后的原application恢复共3个新控制分批通过。仅H078/M618独立叶证据，C02接线、prepare自动收尾、跨进程proof及当前H079组合仍另验；不是全部评分运行就绪。[C03准备](../plans/2026-09-06-corpus-public-seed/C03-PREPARE.md)／[收尾结果](../plans/2026-09-06-corpus-public-seed/INFERENCE-DRAIN-RESULTS.md)。

最后更新：2026-09-06。r16 mandatory-context 后继独立源：Host 将 no_recall 决策移至 SDK 真实响应 checkpoint 之后，反馈纳入新 snapshot/hash；repair-bearing 每次拟终态（包括已route）仍核真实ACK/pending，最多两次且继承原预算。Host 新3控首批PASS6.63s，SDK新11控分批PASS；含真实首零tool→ACK、route无ACK有限FAILED、续接前publicforget零新增发送，公开操作审计核repair identity。最后PG21416exit0/remaining[]，无模型/native/构建。Dirac固定源/14unique限定ACCEPT；H079待主统一制品与原生，H078/M618/原r16失败记录未改；非完整program完成。[精确结果](../plans/2026-09-06-prospective-mandatory-repair/RESULTS.md)。

2026-09-06 原生r17/r18（Host55eb273d/H079/M618）：旧提醒真实ACK后正文送达、下一轮去重及冷启动去重通过；新银杏提醒到期虽ACK成功，最终回复却未展示提醒正文，**完整提醒交付仍FAIL**。两组正常退出且无残留，不是内存/锁屏阻塞。新增缺陷继续修复，旧r14/r16失败保留；240质量仍0。[实际结果与证据](../plans/2026-09-06-typed-use-primary/NATIVE-R17-R18.md)。

2026-09-06 H079/M618候选：新SDK单次离线制品已固定，main factory/真实零tool恢复至ACK/身份3项安装组合PASS4.37s，174/84/116包成员与202加载模块精确核对；PG21846正常退出并清空。源14绿不重跑，原生r17仍待验、r16失败保留。[安装结果与边界](../plans/2026-09-06-typed-use-primary/COMBINED-079618.md)。


2026-09-06：时间提醒生产lane独立源（base7844cf67，产品ec99fa60/7c627fbc）默认注入已有prospective signal authority，并由MemoryAnalysisLane统一拥有独立登记/timer轻量任务及关闭join，避免慢analysis阻止到期；无新schema/SDK制品。4新增控制首批PASS7.15s：真实main activation/publicManager登记到期、重开唯一、已提交丢ACK跨expiry exactreplay、suppression/显式restart、父重复cancel清理（该项受控生命周期fixture）。PG16947正常退出remaining[]/cleanupnull、锁释放；H078M618既有installed+Hostsource，已合候选，r16原userdata实际登记触发成功但前台pending/no_recall仍FAIL；F01事件发布/OS通知未增加。[结果与命令](../plans/2026-09-06-prospective-runtime/RESULTS.md)。

2026-09-06 C02-19原setup关联补强：完整原始S1/receipt与实际group USER精确比较，新增真实同文异Run负控1PASS；已有正向/19绿未重跑，PG19055清空。仅setup来源，runtime隔离/240质量不计完成。[结果](../plans/2026-09-06-corpus-public-seed/C02-BATCH.md)。


2026-09-06 C02全部20条setup已分批通过（18首批、C20及C19失败修复后各1）；C19用真实完成Host/SDK assistant来源保留llm_inference/unverified，C20不补造颜色或通用预算。C01+C02共40条准备验证，240真实质量仍0，运行来源隔离继续。所有测试组已清空。[准备结果与失败历史](../plans/2026-09-06-corpus-public-seed/C02-BATCH.md)。


2026-09-06 原生r16：时间调度修复已在r14原userdata实际恢复并触发1条；普通问题却被SDK pending occurrence/no_recall检查拦截，UI无本轮回答/提醒，端到端仍FAIL。不自动ACK或放宽检查；PG17276正常退出并清空。[原生结果与卡点](../plans/2026-09-06-typed-use-primary/NATIVE-R16.md)。


<!-- 最后更新：2026-09-06 -->

2026-09-06 原生r15：公开SDK准备的2节点/1条APPLIES_TO在真实Cytoscape画布显示、点击边打开正确有向详情；筛选为1节点0边时隐藏详情，清空后恢复原选择。限定图谱UI通过，不计模型抽取/240质量/完整旅程；PG14481正常退出并清空，峰1,327,584KiB。[原生结果](../plans/2026-09-06-typed-use-primary/NATIVE-R15.md)。


2026-09-06 原生r14：一次性提醒后台实际创建且UI记忆可见，前台却否认；到期后真实普通下一轮仅答43，未展示提醒，Host登记/计时/occurrence/presented均0。判时间提醒原生FAIL，正在补生产调度生命周期；不以两Run COMPLETED或旧组件绿替代。PG11237正常退出并清空。[Run与原生证据](../plans/2026-09-06-typed-use-primary/NATIVE-R14.md)。


2026-09-06：大结果边界增量：8k小参数调用的1MiB精确分页通过（最大物理请求19,219字节）；4k预算拒绝后的真实ClosureFallback收尾/冷重开零重发负控通过，保留FAILED与Scope pending，不报4k分页成功。大型assistant参数原4k/8k超限失败保留，未提高预算或复跑32k/8k绿；进程组均清空。[结果与失败边界](../plans/2026-09-06-primary-context-compaction/MEGABYTE.md)。


2026-09-06：当前运行新增1MiB边界控制1PASS/6.32s，两个实际文件结果均超过1MiB，8次物理请求最大28,209字节，精确尾页及重开依赖通过。仅32k窗口/fixture producer/MockTransport，不代表4k8k或原生；PG7739清空。[结果](../plans/2026-09-06-primary-context-compaction/MEGABYTE.md)。


Corpus source/runtime 限定叶三项新控制已分批通过：C01 CREATE真实SDK job物化；
graph backoff重开IDLE拒当成功（Dirac P1闭合）；source实际USER ingestion、原S1
中断导入/重试、scoring新请求无setup/旧assistant历史及自身exact终态group。
最后source1PASS/1.71s，PG19566清空；C03/C04不混入，不称typed/short跨库隐私
全链或240质量，质量执行仍0。源码006a67dc已独审限定接受并合入候选。
[最新限定结果](../plans/2026-09-06-corpus-public-seed/SOURCE-RUNTIME-WIP.md)。


C01-06 history遗忘因果oracle已补：同binding/disclosure在MEMORY-only suppression
前visible、后不可见，定向1PASS，PG8072清空；不新增unique语料计数。
[结果](../plans/2026-09-06-corpus-public-seed/C01-BATCH.md)。

<!-- 最后更新：2026-09-06 -->

C01公共seed后继：18个新case完整记录atomic创建/actualID与内容hash回读通过，
C01-06真实REVISE同ID1→2/持久fixtureauthority/reopen/过期已消费replay及
MEMORY-only suppression→graph/history不可见通过。旧C01-10/graph未重跑。
20个C01都有实现路径；不称240质量/模型或runtime已完成，运行前seed/history与
额外analysis隔离仍待接。无SDK改动。[批次与限制](../plans/2026-09-06-corpus-public-seed/C01-BATCH.md)。

<!-- 最后更新：2026-09-06 -->

公共seed隔离叶：真实Host S1→Memory public ingestion/mutation/receipt链实现
C01-10单记录幂等/重开；独立fixture以同atomic plan建claim+Procedure+applies_to，
公开graph回读2nodes1edge且relation不作node。3unique控制分批绿，原入口红保留。
非LLM提取、非240质量/真实runtime/native通过，其他样例仍NOT_RUN。
[契约](../plans/2026-09-06-corpus-public-seed/CONTRACT.md) ·
[结果](../plans/2026-09-06-corpus-public-seed/RESULTS.md)。

<!-- Updated 2026-09-06 -->

2026-09-06：用户明确将“发布成功后提醒”缺失的实际发布来源接入及对应端到端验收延期为F01。本次不继续推进、不计为通过，其余当前交付继续；已有事件协议层证据不替代真实发布。[后续待办](../plans/2026-09-06-typed-use-primary/FOLLOWUPS.md)。


Updated 2026-09-06: revoked-source/non-success semantic fallback preserves pending debt and genuine FAILED terminal, without constructing a source-bearing model observation. Original main fallback already settled the unclosed-scope flow; earlier current-r3 lacked that component and is not main deadlock evidence. Two new actual-stack controls passed7.75s, including Host terminal.before_commit crash, cold same-receipt reuse/no retransmission, pending replay status and independent next input without withdrawn USER text. Productc6af1ac4; H077/M616 plus Host source/MockTransport, not native. PG4841 empty/lock released; no schema/hash/SDK changes. [Results and baseline calibration](../plans/2026-09-06-revoked-scope-terminal/RESULTS.md).


2026-09-06：真实v4提案混入多种正文被编译拒绝，后继v5按memory_type分支schema并保持旧协议恢复。固定85a19260新3控通过；真实gpt5.5三意图分别产出ACTIVE/DRAFT/DRAFT，无编译拒绝。仅模型分类+编译，非Host持久链/原生/240质量；PG4986正常退出并清空。[实际失败、修复和三条结果](../plans/2026-09-06-procedure-adoption/V5-CLASSIFICATION.md)。


2026-09-06：当前运行分页合并A7的构造器和调用均保留双方参数；固定320a419e在H078/M618实际main factory及current page allow两项2PASS/5.34s。PG3663清空；未closed写Scope撤回后终态pending仍单独修复，原生未开始。[组合结果](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。


2026-09-06：历史分页合并A7后，固定c9e1aebf在H078/M618运行1条必要交互检查，1PASS/4.71s，实际首/续/尾页、后续物理请求与重开依赖通过；PG3328正常退出并清空。当前运行分页及原生长旅程仍待验。[组合增量](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。


2026-09-06：固定1491309f的H078/M618组合5PASS/4.82s，覆盖实际main factory、A7直接路由ACK终态、Procedure旧v3响应跨配置恢复及Memory身份/锁。173/84/116包成员与vendor一致、201模块全部来自新小target；PG2168正常退出并清空。原生/质量及随后历史分页代码不在此批范围。[当前组合结果](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。
Updated 2026-09-06: current primary large generic tool pages now use public settled effect/actual parent request authority, exact page read and final current-source guard. Two new actual-stack controls passed in separate retries (4.06s/3.81s), including reused raw ID, pending exclusion, tail/reopen and post-page forget with a pre-closed real write scope. Original unclosed-scope/withdrawal terminal-pending failure remains. Product0c1b4b38, H077/M616 plus Host source/MockTransport; main H078/M618/native and typed-consumed cross-SDK use are separate. [Results and limits](../plans/2026-09-06-primary-context-compaction/CURRENT-RESULTS.md).


Updated 2026-09-06: isolated primary history tool pages passed 4 unique controls across batches, preserving original failures: real terminal S1/public SDK transcript, Run-admitted summary, exact public first/next/tail pages, actual MockTransport, forget blocking subsequent sends, and full-stack dependency reopen. Existing H077/M616 installed targets; not native/external Provider. Current Run page:causal continues in this leaf; full S5/program remains incomplete. [Results](../plans/2026-09-06-primary-context-compaction/RESULTS.md).

2026-09-06：Procedure提案按新v4区分明确采用/步骤叙述/不确定，Host核真实USER来源与有序引文；明确采用ACTIVE，其余合法分类DRAFT且观察成功数0，ACTIVE不授予执行权限。v3完整协议保留，普通失败跨配置重试P1由M618固定完整输入/cohort恢复；原Host反例零新Provider并应用旧v3语义已实际通过。源码/独立安装验收不代表Scope观察、适用性或真实分类质量；H078/M618组合另验。[来源与范围](../plans/2026-09-06-procedure-adoption/SOURCE.md)。

2026-09-06：A7展示/ACK与来源继承已独审合入候选（固定cd594b8f）。真实五路由ACK终态、三轮未ACK保留pending/唯一overdue、第四轮ACK、终态故障恢复、异主体拒绝及跨轮派生历史遗忘分别通过；slow-source等待期间Host换代真实红例已修复并验证零外发。原no_recall规则不放宽，snapshot注入不当作用户已见。生产默认登记协调器/ACK并由组件升级52；H078组合和原生A7另验，事件触发来源继续。[原红、结果和范围](../plans/2026-09-06-prospective-presentation-ack/RESULTS.md)。

2026-09-06原生r13（Hostb3680732/H078/M617）：新普通对话真实回答45/idle，默认后台审计45/45公开DTO enumerated，SDK明确verified_current_intervals与coverage_gaps[]。只关闭本场景驱动核验，旧r12 unverified不追认，完整工具/Service/Memory覆盖另验。正常退出PG99878、组清空。[Run、截图及审计](../plans/2026-09-06-typed-use-primary/NATIVE-R13.md)。

2026-09-06：H078/M617/S0313接入候选。SDK正式按持久start_mode选择实际driver，保留Host控制校验，避免普通主对话因不透明wrapper失去审计核验；源4项、安装3项、Host新组合4项分别通过。旧r12实际98/98条审计已读取但coverage仍unverified，不追认旧区间；新native/fullcoverage另验。所属进程清空。[组合及真实缺口](../plans/2026-09-06-typed-use-primary/COMBINED-078617.md)。

2026-09-06：提醒状态库50/51/52已接入应用启动及通用初始化的逐版完整校验；新增负控发现并修复bootstrap缺失时绕过human校验的问题。9个唯一新增场景分批通过（非空重开/损坏拒绝/未知版本/fresh49），资源组均清空；完整A7与原生schema52重启仍待验，未默认安装半成品。[结果与边界](../plans/2026-09-06-typed-use-primary/STARTUP-52.md)。

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
