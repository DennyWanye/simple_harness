# N1 同机共享模型容量接纳

最后更新：2026-09-15 05:53 CST。SDK完整回归2302PASS/32条件SKIP（669.22秒），620源/测试hash不变。原生v55发现前台None输出参数被误拒绝，0物理调用；修复后Host12定向PASS/0.13秒，新原生复验与真实多进程待验。N1–N8整体仍OPEN，Flash0。

同一个规范化local profile文件的相邻capacity-v1.sqlite3为共同账本，物理endpoint作为池身份（模型别名不另开容量），上限2槽/393216在途tokens。配置冲突拒绝，重复同配置绑定幂等，冲突嵌套绑定在出站前拒绝。主对话保守预留整个262144服务窗口，编排/实验使用已绑定tokenizer的最终wire输入加输出上限。两个执行器内部2槽不再等于两份独立总容量。

SDK ProviderInvocationCoordinator在记录handoff之前等待共享容量；BaseAgent恢复tool_calls之后计数。CapacityProvider和MeteredProvider的同task/同请求嵌套共用一次grant，评测等待取消计为0物理调用，既有Task/Mission预算身份及冻结旧请求不改。排队被标为非计费slot wait，保留原有stall与取消纪律。

SQLite FIFO/短事务保存WAITING、RESERVED、HANDED_OFF、UNKNOWN和终态；随机owner epoch防陈旧回调。进程终止或PID复用只释放前置状态，出站状态转UNKNOWN继续占槽与tokens，绝不靠TTL放行。PID+OS进程创建时间核验依赖SDK新local-capacity可选extra psutil（本机7.2.2，uv.lock已锁定）；身份不可读时保持保守，旧缺创建时间条目也不猜测释放。

可信恢复根据同一SDK数据库namespace、invocation和handoff ordinal核对：存在完整terminal usage、明确CONFIRMED_NOT_STARTED，或容量标记后SDK handoff未提交的CLAIMED记录才可清理；对账proof只存opaque identity/version。复制库到另一目录不能释放原库容量。该对账接口不是模型工具。

范围：只约束采用同一profile/账本的同机客户端；直接HTTP、另一个profile文件或其他电脑不受它约束。当前不改DGX服务配置、不声称跨机器全局限流。源码UI仍由完整launcher管理唯一Vite/backend，不打包。

|定向检查|结果|实测时间|
|---|---|---|
|SDK原语/出站/Orchestrator/计量/恢复/历史结果合同|40PASS；含进程杀死、PID复用、取消、缺usage未知保持、同DB可信对账、错DB拒绝、实际runtime嵌套一次占用|2.59秒|
|Host本地profile/主对话与编排共池/绑定幂等及原LAN请求|15PASS，配置不符拒绝、原provider身份保持|0.25秒|
|静态检查|5个SDK源mypy通过；Ruff通过|工程未独立计时|

保留初始失败：父级把Agent.run_id(str)当RunId.value导致真实runtime用例失败，已修正；Host测试夹具空messages先于输出cap校验失败，已补真实消息。独立审查发现重复绑定自锁和PID复用陈旧队列，已新增回归与修复，未通过放宽断言隐藏。

真实协议预声明：Qwen256K；三个160K输入进程（最多2并行），两个210K输入进程（加权后串行），每请求输出上限4096、超时600秒；先查服务idle，未知用量停止后继接纳。具体任务文本、源hash和实际server usage将在运行前后存本机ignored证据，不把预声明当已执行。

前台真实缺口：ProviderRequest.max_output_tokens=None表示下游绑定adapter管理输出，并非没有服务总窗口。容量层仅在前台整窗262144预留分支接受None；精确tokenizer分支仍必须有明确正整数输出上限。原生v55拒绝前handoff=0、provider_calls=0保留；新反例先FAIL，再12PASS。

## 实机发现的校时问题

最后更新：2026-09-15 06:01 CST。N1真实三进程v1为FAIL：两次物理调用321104tokens、0新增抢占，第三路前置排队被错误释放（已知0出站）。系统校时影响psutil.create_time造成身份误判，旧源3反例FAIL；改为psutil稳定process hash（>=7.2.2），新25PASS/1.85秒，完整及实机后继待验。N1–N8仍OPEN，Flash0。

稳定身份使用psutil公开Process hash；macOS/Linux由内核单调创建身份组成，Windows使用创建身份。unsupported平台/旧缺identity记录保守保持，只在PID确定不存在时按原状态恢复；新列process_identity与旧process_started分开，禁止跨算法比较。散列碰撞只会保守保留，不会误释放。真实v1原始结果不改写；第三路无usage是出站前拒绝，不是物理调用未知，修正审计另存parent-audit.json。


## 当前真实验收与后继（2026-09-15）

真实五调用 v2 已通过：三个约160K输入进程按FIFO最多双路，两个约210K输入按加权容量串行；5物理调用904061 tokens，模型阶段428.455秒（含准备外层499.215秒），峰值2槽/328184 tokens，服务抢占增量0，全部结算，最终服务与容量空闲。源码SDK e80cfa8、Host a7518d19；后继SDK修改未重跑该五调用，不能错标为最新源码测得。

原生v57暴露本地长响应超过180秒被误当executor_stalled；保留取消FAIL，9次物理调用56000 tokens全结算。SDK8b5cbc1为实际provider await增加有界600秒响应等待，并以真实在途状态标记billable blocker，不伪造progress、不改变全局stall规则；超时仍保持未知占用。新27定向PASS，旧Bridge反例FAIL。完整编排＋共享容量/出站专项2308PASS/32条件SKIP，668.54秒，620输入hash不变（不是全SDK测试）。

原生v58（同原题/预算/成功条件）没有再次停滞：Task COMPLETED、code_test真实83PASS，三产物VERIFIED；Mission FAILED/mission_criteria_unmet。7次Mission＋1次前台物理调用50661 tokens，全部已知；另一个最终Critic前置拒绝0出站。阶段463.944秒；共享峰值2槽/271581 tokens，0抢占、最终归零。观察器all_grants_settled=false来自该合法RELEASED前置拒绝，不是未知用量。累计本轮续作285本地调用、6762328已知tokens下限、早先1未知保持，Flash0。

最终Critic未运行的根因：预留6000不足实际9894请求，扩展时误进入Task保护尾额度路径、要求不存在的Attempt。SDK f122b8c限定合法Mission judge和同Mission账户后回到普通额度增长；不改变总预算、不放开Task或外来账户。旧源1FAIL、新关联47PASS/5.05秒、ruff/mypy通过。完整v4及同题源码原生v59正在运行，未计完成。

测试快照重复源码采用APFS克隆去重，逐文件SHA256核对不变，没有删除原始证据；空间从2601754624恢复8203677696 bytes，8.725秒。新版快照复用不变Host、单独SDK和Python环境；UI由源码launcher启动，持续防熄屏。

|当前部分|状态|实测时间|
|---|---|---|
|跨进程容量五调用v2|PASS；2路与大输入串行，0抢占|428.455秒模型阶段|
|前台None输出修复|真实回复PASS；计量使用SDK账本|12定向测试0.13秒|
|校时导致错误进程身份|旧3反例FAIL、新25PASS；真实v2PASS|定向1.85秒|
|长响应误停滞|旧原生FAIL保留、v58无停滞|27定向2.58秒；原生463.944秒|
|最终Mission评审额度|新关联47PASS；新原生待|5.05秒；实现未独立计时|
|完整编排＋容量/出站专项v3|2308PASS、32条件SKIP；新v4待|668.54秒|

所有测试时长与工程时长分列，重叠运行不相加。额外旧SDK dispatch专项78PASS/3.58秒；2个Memory互操作文件因缺兼容依赖未收集，不计PASS，artifact wheel项按不打包范围排除。

|本轮增量|结果|时间|
|---|---|---|
|共享容量与实机问题修复|四个实机暴露点分别保留失败和后继测试|工程未独立计时|
|独立子代理25–27|均已收回；审查结果/返工见私有模型报告|598.49/208.60/418.58秒，存在重叠|
|当前UI同题复验|v57取消、v58评审前拒绝，v59待终态|v58阶段463.944秒|
|源码和证据核对|v3回归620hash不变；原始证据本机保存|未独立计时|

整体八包及剩余时间沿用[后续路线](testPhase1-remaining-roadmap-2026-09-14.md)和[两轮执行](testPhase1-two-wave-execution-2026-09-14.md)。N1–N8尚无整个工作包关闭，A96/B96未开始；本次容量功能局部成功不等于正式评测完成。

### 证据索引（均相对本机项目根）

- `.local-test-evidence/2026-09-15/shared-capacity/real-process-v2/summary.json` — SHA256 `a007f6992b0079a6ddd487fa9ecedaaa6b9702da75561506cceb2d6e7f18c7d8`
- `.local-test-evidence/2026-09-15/shared-capacity/full-v3/command.log` — SHA256 `a8bdd5a526d29099446a3bf9d8d20b002d5ff7f2108d99687eb7ceb1068a904f`
- `.local-test-evidence/2026-09-15/shared-capacity/source-after-full-v3.json` — SHA256 `7f9281fa7ed1405b01f4c15b40236e341a432f8fbba45868edd97fcdf3d40bd4`
- `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v57/failed-mixed-audit.json` — SHA256 `5b78274db4b11b1e0efc3567afde9f34b3a7ca3702f0e5a4186abdc0a318a989`
- `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v58/failed-judge-audit.json` — SHA256 `f4674f008b4247e81c12079fc8bd6513fd95f32edf467b7f418dd1a97b0ca8d6`
- `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v58/mission-criteria-failed.png` — SHA256 `0fce3b4b9bdbba53b24d81b61f3d47a0ce8f8a930439af43e3fd301ca45be0a3`
- `.local-test-evidence/2026-09-15/shared-capacity/judge-growth-old.log` — SHA256 `d0fd64c7e3f0c43fddbe7af65dc7135530bf69abe8e444273b0c42312b9276d9`
- `.local-test-evidence/2026-09-15/shared-capacity/judge-growth-v2.log` — SHA256 `5452822f184c9827778c0d3bab091730b2fb6f6cbb1a07cfb5b325d69534c0d3`
- `.local-test-evidence/2026-09-15/shared-capacity/derived-dedupe.json` — SHA256 `75f4cceab3e5bfb52d50a65812a4e9acf636aa1805d8d0ec7a29f450b0850c5e`


### N2 v4 现有记录只读复核（2026-09-15，无新增模型调用）

精确额度链已补齐：Planner为三Task分配1.2M/1.5M/1.3M。Task1实际903889 tokens；Worker当前预留910877、首个Critic受保护预留261120，所以当时可扩额度=1200000−910877−261120=28003。下一请求上界69977需要增量62989，被原Task额度合法拒绝；终态总Mission结算912645，预留归零。不是简单“实际消耗未达1.2M却误拒绝”，也没有提高总预算的理由。

SDK实际工具：38次appworld_execute、workspace_list和workspace_write_file各1；知识表0条，没有独立knowledge_list/read调用，Python调用AST也没有知识读写。Task2/3未启动，因此尚无可复用知识或消费收益证据。发现多次交易/联系人探索，但仅凭调用次数不能认定重复调用都无必要。暂无已证实产品接线缺陷；保留模型/规划失败，不为获得PASS盲目重跑或放宽题目/预算。下一有价值门槛仍是可验证的知识产生与跨Task消费，A96/B96未开始。

本地只读审计0.013秒（非工程时间），不执行保存的Python代码、无网络/模型调用。Host证据`.local-test-evidence/2026-09-15/shared-capacity/n2-v4-retrospective/summary.json`，SHA256 `d9d67e96c5e6732cad763c02f4a6760529626e95b52327c1e5e4a6efb1e9573d`。


### 原生在途状态展示修复

v59实机仍有旧Host投影缺陷：SDK真实provider_response_wait（billable=true,bounded=true）被通用blocked分支显示为“进程退出/UNKNOWN”，但模型持续返回、进程存活。这是展示误判，未触发接管或重试。Host仅排除SDK明确的有界在途等待及原排队等待；真实未知、缺bounded和未识别blocker仍显示UNKNOWN。原定向1FAIL/2PASS，新Host投影36PASS/8.39秒，ruff通过；新Host源码实机复验待。旧证据v59/live-wait-projection-before.png和ui-wait-old.log保留。


### 原生同题 v59 正式交付（保留 UI 展示缺陷）

Host a7518d19 / SDK f122b8c，题目/成功条件/预算/工具与v58完全相同。Mission mission-6165b70b4c813fd2正式COMPLETED，Task Critic PASS、实际code_test 65PASS；Planner另选human_review，父级读源码/测试/报告后在UI填写“助手测试复核，非用户本人审阅”并通过，三个产物VERIFIED。83事件回放差异0/未知事件0/缺口0，预留0。此轮最终Mission复用了Task Critic，不能据此声称独立Mission judge增长路径已实机验证。

本地SDK和HTTP均14次真实调用、112753已知tokens（Mission109099，前台3654）；无新未知、无AttemptTimedOut。模型跨度988.051秒，包含助手审批等待后终态1095.251秒；观察器1190.643秒不算模型时长。共池14grants全部SETTLED，峰值2槽/271582、抢占增量0。服务全局计数+15，比本地SDK/HTTP多1，无法归属的那次不编造tokens，也不声称服务独占。

累计本轮续作299个本地可归属调用、6875081已知tokens下限，1早先未知保留；另记服务全局未归属增量1。Flash0。新Host a93073ce更新后v60冷复制129文件逐个hash相同，源码启动中；独立的针对性case先固定于v60-focused-protocol.json，不替换同题结果。

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v59/final-audit.json` SHA256 `641de67192a38cecdb70d518b70cdbedde4df3cd7811752781493a8e602139f3`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v59/delivered.png` SHA256 `c68abe420a1fc16429d211211bec56ef75f14ce26715664e7a75962cc3fba183`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v59/replay.png` SHA256 `8689222a7a668e6e81797dd0a32e9321caf0e3387e0e14aba0122e4a0d34c5ac`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v59/mixed-capacity/summary.json` SHA256 `56ab65e86598e05a8a5434b74cdc99ba948c7e2d90be921f304892c7aa05c0cf`

- Host `.local-test-evidence/2026-09-15/shared-capacity/v60-focused-protocol.json` SHA256 `4f66e574d6b7102e79b5d2104d43d4d251d85cc66821972adada7781ba037b4e`


新Host a93073ce / SDK f122b8c原生v60已验证实际在途等待：Mission mission-ad21508d89234c57，真实Heartbeat为provider_response_wait/billable=true/bounded=true，父级当场读取原生页面为“运行”，无错误进程退出/UNKNOWN接管警报。新Task确为rule_check/code_test，未配置Task Critic；最终独立Mission评审仍待。四个旧Mission冷恢复JSON完全相同，旧正式交付状态与三个VERIFIED产物已实点确认。

本机证据 `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v60/bounded-response-ui-audit.json` SHA256 `da9ed638e63bb4590b27e2bdec2d84b6ee8cb894e82aba2d0c74417540dc027c`。


### Mission 最终验收状态词

原生v60独立Judge实际运行，但旧Host只有终态Task时投影为“排队”。新增仅针对ACTIVE Mission且非空Task状态全部COMPLETED的“待验证”映射；待人、UNKNOWN、终态优先级不变，尚有READY的任务不提前认为在验收。列表和详情共同应用，37关联PASS/8.47秒、ruff通过。隔离加载a93073ce旧Host时新回归决定性FAIL（ui-judge-state-old-v2.log）；第一次未隔离conftest的旧源重跑实际加载当前源而PASS，保留日志但不作为旧源证据。最新Host原生分支待验。


### 本地独立最终评审 v60 已交付（2026-09-15）

Host a93073ce / SDK f122b8c，Mission `mission-ad21508d89234c57`，与v59不同的预声明专项。Task只rule_check/code_test，实跑60PASS、三产物VERIFIED且文件哈希复核相同；最终成功条件两条均由 `source=independent` Critic 判定通过，Mission COMPLETED/verification_passed。第一次独立评审输出达到8192上限而失败，第二次通过；该输出上限不是256K上下文容量。两次初始6000的独立Mission额度分别实际增长到19961/31678，结算19961/28722；没有Task账户的额度增长路径已真实验证。

共24次可归属调用、255431已知tokens，模型跨度1713.610秒；0新增未知、0AttemptTimedOut、最终预留0。共享容量24个新grant全部SETTLED，服务全局请求增量24与本机一致，抢占增量0，最终服务空闲。连续观察器在1500.573秒先结束，尾段只有终态快照，不能声称全程连续采样。真实UI查看正式交付及回放：104事件、0差异/未覆盖/未知类型/证据缺口。旧Host最终Judge期间仍显示排队，后继374aa70a专项待验。

这些是范围固定的UI/运行机制验收，不是任意输入完备性或正式benchmark成绩。累计323个本地可归属调用、7130512已知tokens下限；1早先未知以及v59服务全局额外1次未归属分别保留，Flash0。

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v60/final-audit.json` SHA256 `c1f476a50f0c85bb90d5e6717234921e4e5c48c08f287ec7ce0d6eb82d8ea13f`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v60/delivered.png` SHA256 `a47a0e9d0a4d752274a4a0bdd0b75b7123ebeee4396a466cc44ac48d9ef99975`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v60/replay.png` SHA256 `79b66ccd2d3cc291b45b1cb1a43c73a2d3b6f47a28500414937b1f091fae8104`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v60/terminal-capacity.json` SHA256 `d272a46f6b5b7cd45cb337e86cfcab2739f5874bbfaeae2a4a318be67b5692a9`


### 最新源码 v61 原生验收完成（2026-09-15）

Host生产374aa70a / SDK生产f122b8c；源码快照v6 Host与v4 SDK/Python。153个数据文件冷复制逐hash一致，5个旧Mission JSON完全相同；实际打开恢复后的正式交付任务、VERIFIED产物。没有打包。

两个相同预声明小专项分别为 mission-0942dac3b3d7ceb0、mission-2c10b179ee2c316c。首例6调用14138tokens/96.742秒，正式交付但未及时捕获短暂待验证画面；为补采而重复的第二例6调用13333tokens/144.183秒，额外开销单列。第二例在所有Task完成、最终独立Critic运行时亲自打开任务，列表及详情均显示“待验证”，随后正式交付；截图保存时间位于TaskCompleted与MissionCompleted事件之间。没有改变题目、预算或输出/上下文配置。两例Task均只rule_check，无Task Critic/human/code_test；最终条件由独立Mission Critic确认。NOTES.md恰为5字节READY，VERIFIED且文件hash与界面相同。第二例真实回放41事件、未覆盖/差异/未知类型/缺口均0，预留0。12个新共享grant全部SETTLED，最终服务空闲、占用0。这里没有连续压力观察，不以此宣称两路压力通过。

累计本轮后续335个本地可归属调用、7157983已知tokens下限；1早先未知和v59全局额外1次未归属保留。Flash0。主会话模型未更改；开发子代理累计28个均关闭，本次补采未新增子代理。最新SDK广回归仍如实记录2304PASS/32条件SKIP/6缺tiktoken环境失败，两个关联文件在兼容环境49PASS；不是一次全绿广回归。源/测试621hash不变。Host最终评审投影旧源反例FAIL，新37PASS/8.47秒；实际UI证据现已补齐。

N1此次共享容量、稳定进程身份、长响应等待、独立Judge额度与两个UI状态缺陷已完成各自限定验收；N1整包的长时恢复和B512K仍OPEN，N1–N8与正式A96/B96没有被本次关闭。最终评审在途额度显示按事件更新，短时间可能落后于数据库增长，不能拿中间UI数字当即时供应商账单。当前保留原生v61运行及防熄屏。

#### 整体计划当前状态与已记录时间

|工作包|已完成切片 / 未闭合门槛|已记录实际时间（非累计工程时间）|
|---|---|---|
|N1 运行与容量|共享2槽/加权接纳、独立Judge、长响应与当前原生UI通过；长时恢复/B512K仍待|5调用压力428.455秒；v60 1713.610秒；v61两例96.742/144.183秒|
|N2 观察与知识复用|可信观察接线/负控完成；困难AppWorld仍FAIL，跨Task消费收益未证实|v4 1557.359秒；本次只读审计0.013秒|
|N3 检索/结果契约|两道原失败题后继交付；完整困难矩阵仍待|后继290.623秒|
|N4 配对正式矩阵|通用runner已实现；A96/B96均未开始|正式运行0|
|N5 AgentDojo|正常/攻击各一例官方utility通过；完整黑板传播待|209.980/1046.198秒|
|N6 ARE/Gaia2|自定义ARE硬判通过；完整Gaia2/独立judge待|275.301秒|
|N7 效果归因|预声明与区间统计接线；配对机制收益待|工程未独立计时，不能用其他测试时间代替|
|N8 正式评测/交接|本次源码UI、证据和主分支交付；完整正式split仍待|工程未独立计时；与上述测试重叠|

#### 当前部分详细完成情况

|事项|结论|实际测试时间|
|---|---|---|
|共享容量真实5调用|2槽和加权单路、0抢占、0残留|428.455秒；外层499.215秒另计|
|最终独立Judge额度增长|定向47PASS，v60真实独立分支交付|5.05秒；v60整例1713.610秒|
|有界物理响应UI|真实在途显示运行，不再误标UNKNOWN|36关联PASS/8.39秒；UI包含v60内|
|最终Mission状态UI|37关联PASS；v61列表/详情待验证→交付|8.47秒；两小例96.742/144.183秒|
|广回归及环境失败复核|2304PASS/32SKIP/6环境FAIL；关联49PASS|672.22秒及0.87秒；不合称一次全绿|
|冷恢复与回放|153文件hash、5旧Mission保持；真实回放0差异|未独立计时；0额外模型调用|

#### 还剩多少工作包与时间估计

|范围|尚未关闭的包数|下一门槛|粗略剩余工程估计；模型/等待另计|
|---|---|---|---|
|N1|1|有限长时恢复、Flash512K容量资格|1–3小时＋真实运行|
|N2/N3|2|困难知识产生/消费、检索与摘要完整矩阵|合计5–11小时＋真实调用|
|N4/N7|2|冻结A96/B96与消融，解释质量/成本/失败|合计4–9小时＋两轮模型运行，尚无可靠总时长|
|N5|1|实际黑板传播攻击链与官方对照|4–10小时＋真实调用|
|N6|1|完整Gaia2、动态协议及独立judge|8–18小时＋judge可用性/费用门槛|
|N8|1|正式split/完整任务组、最终交接|3–6小时＋正式运行及闲时窗口|

仍有8个包未整体关闭，包内功能已经多项完成；以上为粗估而非承诺，部分工作可重叠，不应相加当作确定交期。下一优先做无付费依赖的N2/N3困难机制闭环准备和N5传播扩展；不为追PASS放宽现有失败任务或盲目重跑。Flash512K集中闲时执行，费用上限/独立judge未确认前不启动相应付费块。

#### 本次续作新增完成

|事项|结果|实际时间|
|---|---|---|
|收尾v60真实独立评审|24调用255431tokens正式交付，首次评审输出失败保留|整例1713.610秒；此前已启动，不能全部算成本次工程时间|
|v61最新源码冷恢复/最小任务|首例交付，第二例补齐短暂UI状态；12调用27471tokens|96.742＋144.183秒，两次分别计|
|N2既有失败账本复核|合法额度拒绝，0已接受知识/0下游消费；未新增付费或本地模型重跑|0.013秒只读审计，工程时间未独立计量|
|架构/计划/主分支交付|仅文字结论/索引/hash入Git；原始证据留本机|未独立计时；Git同步结果见最终交付消息|

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v61/first-final-audit.json` SHA256 `dc3df7e6e11673693740d2b6c1616390d13973a41929a04e745a338005e7193f`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v61/final-audit.json` SHA256 `4a1fb69bc69480c5d2e58314564a7d24fe92abf0b46cce457a4968acbb7f8b09`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v61/verifying-detail.png` SHA256 `0695cb7ee026ce6d341b802e74efc990af63922e7521b0bba89d45cd2e01a84e`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v61/verifying-live-audit.json` SHA256 `2a6875a1bd2516157092f6c7672588e6d374a941c6ee62c8fc8133c236f0a25a`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v61/repeat-replay.png` SHA256 `488d218bbb83e84ea398dce0c01aae79a4853d6148907718ef31c891bb4e866c`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v61/terminal-capacity.json` SHA256 `ac77d98a3c05b7a499c40b73c75edef19e5751e234ee2298a77f74eb6720e40f`

- Host `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v61/cold-missions-audit.json` SHA256 `db20a414df5cffd103f27bd894d4064148f6094954641e1deec68c14ee401438`
