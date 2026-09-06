# C01 首批真实评分接线

2026-09-07。基线 Host `b2da14da`，自有树 `simple_harness-corpus-clock`，分支 `feat/corpus-c01-scoring`。源码99d17c11的必要无网络控制已完成，详见[实际结果与首例命令](RESULTS.md)；Provider与评分仍 **NOT_RUN**。不改 r4 成员、旧 compiler、gold、spec、metric 或模型 prompt。不是新的240通过结论。

## 第一批与接口

先选 **C01-10**，随后资源与原始失败审阅允许时选 **C01-13、C01-20**；同一ID不自动重试。每例独立子进程、userdata、Host/Memory/SDK库。C01-11 的 Provider 可信日期尚未接入，保持未就绪；C01-06 的公开修订已另验，但此 runner 尚未接其不同的 fixture job 收尾，不能直接套 CREATE。其余类别不混入本批。

`python -m deskpet.quality.corpus_scoring`：必填 `--corpus-root`（原successor目录）、`--compiler-root`（Memory/scripts）、`--host-root`、`--installed-target`、新 ignored `--output`，逐个 `--case C01-10`。默认只准备文件，显式 `--execute` 才启动真实评分。由主默认共享资源入口包住整个命令，所有child继承同PG；无内部抢锁、BUSY轮询、新进程组或Provider探针。

目标安装：`primary-candidate/.local-test-evidence/2026-09-07/memory619-artifact/installed`；解释器：`primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python`。后续主整合代码后，使用该实际生产Host根目录以满足已安装wheel的原点校验，不在本树伪造direct_url或重装旧包。

## 实际初始化与退出

worker 在进程内 import 实际 main，调用 `dispatch_startup_epoch`、真实 SessionDB 初始化、`LLMProviderRegistry.add_ephemeral_provider`、`build_workflow_service(activate=False)`、真实搜索gateway、`_initialize_capability_runtime`、`_initialize_growth_authority`。随后调用 `_activate_product_sdk_runtime(clock=fixture_clock)` → 原 `_build_product_sdk_runtime_stack`，以及 `_activate_human_memory_host_ports`。主模块只增加一个默认仍为真实时间的clock透传参数；生产 composition 只增加一个委托public SDK reader的观测方法。没有调用 lifespan、main服务器、Tauri、测试fixture前台builder或假Provider。

初始化依赖是真实 Session/Workflow UoW、能力平台/授权store、Skill安装/快照resolver、Host writer/schema、Provider registry、Context页store与Memory公共authorities。缺件保留 `SETUP_NOT_READY`，不以 object()/Mock补槽。配置保留源文件的模型/window/output参数，实际SDK原请求也会保存；只替换已授权的endpoint并注册单一进程Provider。main的ignored `.env`仅worker读取APIKEY/BASEURL，key只进内存与进程环境；TOML去密钥，不写keychain，日志在出进程前脱敏。

setup复用 `compile_setup`、`admit_setup_source` 和 `prepare_runtime_seed`：真实S1、public ingest、fixture本地executor、实际APPLIED、公开图回读labels，再关闭fixture manager；评分重新用生产manager/authority打开。不会把setup当用户历史。启动后关闭本批不需要的后台analysis lane，只保留真实USER ingestion worker以取得完整终态组；不让另一次分析Provider混入评分成本。未启动本地embedding模型，不能外推短期VECTOR能力。

退出顺序：前台authority、terminal audit、analysis lane、companion runtime、SDK stack、Memory、Session、Workflow UoW、search gateway；ingress先停止接入。初始化中途失败同样进入finally；逐项记cleanup错误。资源入口最终确认PG没有残留，不能仅看worker exit0。此生命周期尚待第一项必要控制验证。

## 输入、证据与原oracle

父进程用**原冻结compiler**验证r4并拆字段，保存原完整文档/成员manifest、case位置、input/setup/oracle分文件。worker只读取 `input.json` 和 `setup.json`；不接收gold、类别标签、正确ref或期望类型，不拼新用户问句。C01初始history必须为空，正式请求只由原生产context/runtime和原USER生成；随后允许真实工具的合格来源内容进入下一物理请求。

非时间首批只使用原fixture业务clock传Memory/SDK，物理资源及租约不随fixture停表。Provider端可信日期未投影，不称三端日期链已完成。SELF采用现生产默认身份/披露配置；本批不声称C12 input/policy门已被覆盖。

终态后读Host精确turn→SDK Run身份、public SDK terminal、所有Provider投影（含失败）、原request/response/usage、operation audit、原始context_route模型提议、Host route记录及hash、public effect和原transcript。Provider投影必须与audit的provider heads相符；缺少投影不能当无查询。未完成Run、setup拒绝、缺观测分别保留，不丢ID、不以成功工具条数推算最终交付。原真实数据均ignored。

**父进程仅在worker退出后才打开oracle。** 自动部分只提取原类型计分分量，按实际invocation/call去重并保留原非法提议；不从Host默认types倒推模型选择。自然语言gold的实际A/B取得、事实/格式、禁止执行等要求，由主对终态review packet逐项审阅并引用trace；未审阅是 `PENDING_POST_TERMINAL_REVIEW`，不是PASS。失败的required-type credit为0，缺失extra-type观测保持unknown；不发布部分C01的240阈值结果。原两独立root/micro及六公式门保留。

## 静态审查与必要新控

Dirac先核：生产初始化没有假authorities；参数未换预算；worker不读oracle；public trace覆盖失败；退出所有owner；结果不能把准备成功或类型匹配升为整题通过。

待slot仅验证新接线：一次无Provider的真实main初始化/关闭与原USER隔离；一次失败投影及gold替换不改评分输入；随后主分配C01首次实际评分。现成20setup与旧SDK绿色不重跑。不新增judge模型，不build/新venv。

## 原Procedure剩余项

wholeFAILED 的精确失败来源/归因仍是原需求未完成项，**没有获得延期**。最小后继须绑定实际失败tool/Scope及public因果receipt，区分系统/Provider失败和可归因Procedure失败；不能把wholeFAILED伪装COMPLETED来注册完整对话，也不能凭失败计success。本批按用户优先级先推进真实C01，保留该followup。

## Dirac证据保存P1修复（待控）

初版 `aa7675bd` 静态审查拒绝：trace取得后若transcript失败，整个返回值丢失；无terminal亦无法导出已有attempt。后继改为身份先持久、public trace先独立保存，再分开读取transcript/route/effects/queue；各项失败有独立状态，不擦除前项。public terminal允许明确NONTERMINAL，保留真实attempt/audit，绝不因此算COMPLETED；任一观测缺口保持OBSERVATION_FAILED/未知计分。

新增仅两参数控制：真实SDK已handoff而未终态，以及真实SDK失败终态，各注入后续transcript故障，要求前面的实际Provider trace已先写盘且不丢失。使用本地adapter故障，不调用网络Provider；不是main factory初始化证明。两项当前NOT_RUN；main初始化/退出仍须单独必要控制，不付费探初始化。

Dirac追加计数问题同批修正：Provider/audit读取失败不能把默认空列表计成确定0。完整总数仅在公开Provider/audit观测完整时给出，否则为null，并单独保留已见handoff下界及unknown样例数；同一真实失败Run上注入reader拒绝验证，不新增Provider请求。已核验的投影会保留，后续页故障不擦除已见下界。

无网络main初始化控制使用 `--initialize-only` 同一实际组装/清理路径；不读主.env，用不可用本地endpoint和明确无效测试key，网络connect/http send额外断言0。原C01-10经冻结compiler准备后，故意替换**测试产物内**oracle副本并禁止worker读取，原MD不改；必须实际APPLIED、生产authority重新初始化、空前台历史及关闭无错误。此模式从未enqueue评分Turn，不是质量执行。复用已安装M619，临时vendor链接结束恢复，不改site-packages/原wheel，不重扫制品全成员。

## 首跑实际结果与两红修复（后继未跑）

- `trace-r1`：1PASS/1FAIL，PG61549、2.180秒、峰177760KiB、remaining=[]。FAILED参数（包括不完整观测unknown计数）通过，不重跑。NONTERMINAL失败不是未等admission：已等到真实本地adapter进入invoke；SDK提交handoff在前，但projection receipt在settlement才产生。后继改用既有公开 `list_incomplete_provider_invocations`，逐项按run精确公共回读，并与audit核对；不SQL、不猜ID、不sleep。
- `init-r1`：FAIL，PG61656、4.770秒、峰426112KiB、remaining=[]。实际factory报 `SDK Runtime requires the Memory SDK manager`；fixture已真实APPLIED，但缺原main拥有的旧Memory manager，不能判初始化成功。原日志完整保留。

一次对照main原启动/关闭链后，后继将原lifespan的Memory创建块提取为 `_initialize_product_memory`，原lifespan与runner共用同一入口及全部原参数；SessionDB唯一拥有并关闭该manager，不重复close。预开库之前先public fresh epoch；真实provider readiness、完整Host factory（context_route延迟从registry取）、capability/growth/context/foreground与SDK slots均注册。停止借用方后关闭Session、capability center/platform及Workflow UoW。WeMM只构造同一惰性实例，控制要求实际状态cold/not_started，不warmup/encode。

后继仅准备重跑NONTERMINAL和main初始化红2；本段源码修复本身不是绿，首C01真实评分仍0。raw索引为本树 `.local-test-evidence/2026-09-07/corpus-c01-controls/{trace-r1,init-r1}/`；临时vendor链接已还原。
