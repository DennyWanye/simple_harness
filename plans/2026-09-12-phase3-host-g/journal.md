# P3.3 G Host 接线与原生候选

最后更新：2026-09-12。状态：实施/验收中，未交付。

原计划与46项AC在兄弟SDK仓 plans/2026-09-12-phase3/p33；本目录只记录Host接线、打包与原生结果。用户已授权修复并继续P3.2–P3.5、提交文件；不覆盖P3.6。

## 当前已验证

- Harness0.11.1候选源码a5c8fca659be8b491d4d0f3f3f5536a5e711ce48，wheel SHA-256 49137655a6cb26b933410627b8dc177fa2625a578346b1a02de805a39d1af576；helper、vendor与uv.lock对齐。Service保持原pin0.3.13。本机venv曾为H0.7.2/S0.3.12，安装已对齐，非依赖计划升级Service。
- Host首轮实际wheel组合143 passed/5 failed/17 errors（176.43秒）；5实际失败为误读snapshot.mission.domain_id，真实SDK领域冻结在snapshot.mission_domain。修复共用is_document_snapshot，两处实际入口与正反fixture均改回真实形状。新回归49 passed（33.99秒），包括25条正式Claim、真实SDK接受、分页引用、source审批/冷恢复。脚本Provider，不是真实模型。
- 17错误是主线程把开发中的新launcher测试收进目录，FileNotFound；首轮保留。文件冻结后launcher与平台/真实浏览器组合59 passed（9.86秒），无pytest并发。
- SDK精确wheel独立安装921 passed/3 skipped（34.81秒），306包文件逐字核验，249已加载模块全来自隔离安装无来源违规；源码全编排1302 passed/8未启用真实Provider skip（487.75秒）。不是整仓raw全绿，F既有红集仍保留。
- 前端86组件测试、tsc及生产build通过，不计原生；旧code root arbitration met/unmet已按topic恢复并实际点击测试。
- mac平台浏览器真实控首轮20 passed/1 failed（旧Windows进程filter），修正仅测试平台路径后crash1 passed；后续driver matcher归一化并强化真实Node祖先回收断言，包含在59项。原始失败不删除。

## 生产接线与审查

Control IPC新增原子文档create、source register/supersede/revoke、receipt绑定citation_read。固定principal和既有审批，模型工具无此入口。正式document DTO保存全部claims/assessments/criteria/source历史，Worker正文标分析非结论；长引用字符分页、跨Mission迟到结果防串、原文文本转义。

SDK G1/主判定独立审查ACCEPT；Host后端R2与真实snapshot接缝经Halley独审。主前端审查闭环。mac资源路径、SDK动态钉版与浏览器新契约由Halley实现、主审，实际冻结产物仍需验。launcher由Kepler实现，独立复审待补；不凭自审声称已完成。

## 未完成

冻结PyInstaller/Tauri .app构建与实际包身份；原生最小价值spike与真实deepseek-flash；原46项AC剩余补证及原生N1–N6适用矩阵；最终事实源、审查、commit/push。真实key仅Host ignored .env DEEPSEEKER_APIKEY进入子进程env，不复制/打印/Keychain探测。

## 本机证据索引

下列原始日志/receipt只在SDK ignored目录，无原始证据提交Git。

- SDK `.local-test-evidence/2026-09-12/p33-g/host-orchestrator-installed-v1.log` SHA-256 `5b629631c4c5501950caec97f162442150b9b9b0b6edeb726240ea0c36c92840`
- SDK `.local-test-evidence/2026-09-12/p33-g/host-orchestrator-installed-v1.xml` SHA-256 `ba66c7ec00a6e7fa73fa6086fe88705cb83f6229d6adcff29428356dcf22ec43`
- SDK `.local-test-evidence/2026-09-12/p33-g/host-document-installed-v2.log` SHA-256 `9fb4ae9a91e2ac0295c03827df526c96f1ed4bfb787723102a698d204545c51b`
- SDK `.local-test-evidence/2026-09-12/p33-g/host-document-installed-v2.xml` SHA-256 `80b96673761094ed9b99b35ba076ae7eacbbeea482ce83147735dbdd7648571e`
- SDK `.local-test-evidence/2026-09-12/p33-g/host-native-prerequisites-v2.log` SHA-256 `fc2bfc0bf866bd816358162ee2704deb133b501633361cc77cdd3ee6b4d5c6f8`
- SDK `.local-test-evidence/2026-09-12/p33-g/host-native-prerequisites-v2.xml` SHA-256 `55a4ba149bb8310a0b80e0863a9f1dd058cc715731d182e3414a097163fab44d`
- SDK `.local-test-evidence/2026-09-12/p33-g/g-installed-target-v1.log` SHA-256 `53380eefa27b2abf64cb4dd3bc728f0290f52bfe165dcefa7b645c4abcc0714a`
- SDK `.local-test-evidence/2026-09-12/p33-g/installed-origins.json` SHA-256 `55382fc4eaece31c921e722ee52188066798b661a8cfe3c310c777cf2257c907`


### 冻结前身份与环境核查

主审发现frozen manifest原先可能在包位于仓库下时读取当前Git而非构建身份，正在改为包内构建身份。独立Ohm发现launcher只绑定两个exe而未绑定_internal，Kepler已补完整.app资源树/内部框架链接绑定，复测与复审中。源码能力与原生门仍分开。

本机Host venv另外残留已从仓库移除的Memory SDK0.6.3；已卸载该残留。uv pip check检查228个包全部兼容；uv sync --locked --dry-run未执行，只提出移除开发/构建额外工具，未升级其余生产依赖。


### 冻结前专项复验

修复后launcher25 passed/0.12秒；frozen Host身份与原部署manifest20 passed/2.36秒。spec只从已提交运行输入捕获构建身份，打包末再核输入未漂移；frozen状态只读该资源，禁止从外层Git猜身份。主范围审查通过，launcher独立Ohm复审ACCEPT，P1关闭；没有真实启动。


### 后端冻结构建通过（21:47 CST）

干净源码c3d4e2277c00de200f0310121b805574f7b5c368，PyInstaller成功115.661秒，峰值RSS1868384KiB，无残留进程组。包内身份绑定2227个运行输入、SHA-256 27efd77d86700113cafeb3101d1b2e3d6c239c142c19ebc68fb3b714a4bc2dfa，构建前后重算一致。实际dist的浏览器exe/driver/许可证与平台pin一致；Tauri仅映射这一份backend目录。此刻Tauri编译进行中，原生启动/真实Provider尚未执行。

- `.local-test-evidence/2026-09-12/p33-g/pyinstaller-resource-v1/resource.json` SHA-256 `53cd3f0b4ba3f371d7571512c6b3663a611b29acd8364a97fb8febf40e37ee66`
- `.local-test-evidence/2026-09-12/p33-g/frozen-dist/deskpet-backend/_internal/host-build-identity.json` SHA-256 `61f878d8bec9ef776e43e185647db916ffec9d7855b31c73a2bb5e9381000525`


### 原生N1首次启动失败（21:51 CST）

Tauri release .app构建成功303.540秒（Rust报告4分56秒），PG64545无残留；最终.app browser pin与host身份检查通过。但真实原生窗口显示“Backend exited without printing SHARED_SECRET”，实际Bundled路径日志显示主程序经deskpet.task_scope.workspace_bindings访问SDK public lazy API，缺少simple_harness.runtime.workspace_binding_protocol。这是打包缺陷，不是环境限制，也未发出本次真实Provider请求。静态PYZ存在94个编排模块不能替代public lazy export依赖闭包；补齐收集与决定性测试后重新冻结。

通过原生Cmd+Q退出；紧接getAXState导致CUA自动重新打开App，产生PID68424（不同PG、无原launcher密钥环境）。再次Cmd+Q后只用进程观察确认无simple-harness/backend残留，避免getAXState再次启动。受控launcher资源receipt return0只是应用正常退出，不代表验收成功；N1结果FAIL。

- `.local-test-evidence/2026-09-12/p33-g/native-n1-v1/native-1789221068963181000.log` SHA-256 `0329fda14ea8f14d1a8f9cf6c0505aaa86b0feee37af9e58bedcbb8bc5a0313c`


### SDK延迟导入收集修复（21:57 CST）

两SDK已验证安装包的生产模块由collect_submodules显式收集，排除testing/CLI入口，收集错误立即失败。专项4 passed/0.90秒（runner1.55秒）；基于wheel RECORD覆盖、fresh解释器真实public lazy API链以及删除workspace leaf的决定性反例。主范围审查和Kepler独立限定ACCEPT，Ruff/diff检查及原execution manifest检查通过。下一步将本修复提交后重新构建；原生启动尚未证明修复，首失败保持FAIL。测试wrapper从SDK目录调用Host绝对测试文件，receipt.source_head为SDK身份，Host修复身份取本次随后提交。


### 第二次原生启动与工作流源码修复（22:10 CST）

dfb3c7dc的PyInstaller v2成功178.554秒，Tauri v2成功93.365秒；两轮资源receipt无残留。最终.app的browser pin/Host身份通过，公开延迟导入缺陷已在真实启动越过。随后lifespan的build_product_workflow_registrations因research_stages.normalize_handler无可读源码，触发WorkflowDefinitionError与product_sdk_runtime_build_failed；UI显示Backend supervisor gave up after repeated crashes，N1第二次仍FAIL，未发出本次真实模型请求。模型配置已从process-only provider读取deepseek-flash，配置成功不当作请求成功。后台模型provisioner另有HTTP451 warning，当前启动硬失败是workflow源码缺失，两者分开。

通过实际应用菜单Quit退出，PG72563无残留；资源return0仅表示退出正常，验收仍FAIL。保留v2原日志/包。修复只在spec增加product_workflows和SDK workflows的pyz+py源码收集，真实SDK inspect/getsource/fingerprint规则不变。2产品+3官方registration全部handlers/selectors的源码布局控制3 passed/0.59秒；移除任一必要源码组有真实compile失败，正控manifest与implementation hash同源码基线。主审与Kepler独立限定ACCEPT，Ruff/diff/原execution manifest检查通过；提交后仍必须重建并原生重验。

- `.local-test-evidence/2026-09-12/p33-g/pyinstaller-resource-v2/resource.json` SHA-256 `6bbe143c4259cdc2650dda3c6b8f61a5f2a8655b4772b1a4eaad4e3474c6577b`
- `.local-test-evidence/2026-09-12/p33-g/tauri-build-v2/resource.json` SHA-256 `70ba8af46a06d3d854c1bb4118125c6558cf1c35c45f0dda8848e6b6011569fb`
- `.local-test-evidence/2026-09-12/p33-g/native-n1-resource-v2/resource.json` SHA-256 `13e0e26d9f7104561fde4865674919a311918ad3f2e8582faec17be2068fe4f9`
- `.local-test-evidence/2026-09-12/p33-g/tauri-artifact-check-v2.json` SHA-256 `11acbfaf74b5fcff069b2806ac5082cb6019076f4eeb5ea77aa7dd760bbd2504`
- `.local-test-evidence/2026-09-12/p33-g/native-n1-v2/native-1789221804699242000.log` SHA-256 `7f847cc9e94aecebe7ed40448499566fbb51fd3dc5cafd914c0b83cb606c0cd7`


### 第三次启动失败与启动资源核对（22:28 CST）

c41bfc14的PIv3成功107.754秒、Tauri v3成功36.372秒。最终包包含所需workflow源码，真实lifespan越过前两项缺陷；随后build_explicit_product_tool_catalog缺deskpet/tool_catalog/real_tool_manifest.json而失败，第三次N1仍FAIL。通过原生界面跳过onboarding后仍未连接，实际菜单Quit退出；PG77745无残留。未发出本次真实模型任务请求。

审计发现同级缺失：tool_catalog两JSON、SDK execution五SQL（fresh_descriptor真实读0005_fresh.sql）、可选assembler默认策略/示例及verify claim规则。新增集中资源清单；既有Host SQL/eval/packs/schemas/config/uv.lock/诊断/三execution manifest与SDK/Service数据目的路径保留，Host资源只枚举tracked文件，不采集ignored运行产物。新增platform/mcp manager的pyz+py仅供按需自身源码hash，不把它们说成lifespan必达。未找到capability templates当前生产读路径，不列为启动必需。

14 passed/0.60秒：独立资源全集/3SDK RECORD、真实71Tool目录与迁移及缺失篡改负控、freshSQL真实执行/缺源拒绝、policy禁止synthetic fallback、verify非空规则、原pack语义与篡改拒绝、按需源码模式。主审及Kepler独立限定ACCEPT，Ruff/diff/原execution manifest检查通过；将提交后重建，尚未原生通过。

独立遗留：legacy authority_accepts_handler以源码路径作admission，frozen资源路径未证明其支持；当前explicit SDK catalog不是该强校验的必达调用。不修改或弱化此守卫，不宣称所有legacy工具按需执行已验。

- `.local-test-evidence/2026-09-12/p33-g/pyinstaller-resource-v3/resource.json` SHA-256 `f4f10a4977109294851b38a72240f29a81a130b8517e90d14c81daf8f618237c`
- `.local-test-evidence/2026-09-12/p33-g/tauri-build-v3/resource.json` SHA-256 `096b9066d197493882f6a21016c2d90a47a135da9da1b96247b8ebc6ae604149`
- `.local-test-evidence/2026-09-12/p33-g/native-n1-resource-v3/resource.json` SHA-256 `05cb0c553e73cd86a15e4d90ee8670faf9a78a5c810020113604e9aa4f57db67`
- `.local-test-evidence/2026-09-12/p33-g/native-n1-v3/native-1789222653501786000.log` SHA-256 `a325e3a155fdfd3963e7cc1c7fe8a7dd61a98e3f1407bef5f6006c77c0f544db`


### 用户调整执行方式与源码UI续接（23:05 CST）

用户确认范围P3.1–P3.5：继续完成P3.3、P3.4、P3.5；P3.6不做。当前优先功能，直接源码启动Tauri开发UI真测，暂停冻结安装包及发布包工作。原有安装包AC作为暂缓项保留，不将源码UI说成冻结安装包已验。主线程串行pytest与真实UI，独立代理只读审查/准备后续片。23:01恢复功能执行；22:38–23:01为范围对齐间隔，单列不混为编码时间。防熄屏caffeinate -di，PID83049，pmset确认display/system idle断言生效，未修改永久电源设置。

第四版PI已完成112.858秒，无残留，但不再启动或打包。冻结sys.executable会被SDK探针用作Python -I/-c；新增frozen早退，清空旧executor，保持既有code_execution=off边界。先行控制1 failed/3 passed，修复后4 passed/0.05秒（watchdog0.71秒），Kepler独立限定ACCEPT；代码f14a82ba。未实际执行危险的冻结自启动探针，非真实沙箱PASS。execution manifest检查通过。文档首次写入使用系统python命令遇编码错误，代码提交先于文档，本提交补齐事实源，不把间隙说成任务完成。

证据：SDK .local-test-evidence/2026-09-12/p33-g/host-frozen-sandbox-red-v1.{log,json} 与 host-frozen-sandbox-green-v1.{log,json}。随后运行Tauri dev，由Tauri管理唯一Vite与源码backend；独立userdata/端口，DEEPSEEKER_APIKEY只进入进程环境，deepseek-flash不变。


### 源码启动与日志修复（23:09 CST）

0e19fb48干净复验4 passed/0.07秒（wrapper0.77秒）。源码Tauri dev首次Rust编译1分28秒，实际sourcebackend启动，orchestration_ready=available、startup complete、UI控制连接成功。CUA按bundleID、exe绝对路径及进程名均无法识别未带macOS应用目录的开发进程，故未通过UI验收。随后给刚编译的debug executable临时应用标识供CUA识别，仍使用Vite源码页面和当前Python源码，不做PyInstaller/发布构建。

实际访问日志发现连接query凭据未脱敏；立即停止PG83601，资源wrapper清理无残留（155.515秒、峰值1651920KiB、return125仅表示人工停后收子进程），本地旧log1文件补脱敏。read_key专用API key未落盘；不保留会话认证原文。为共享log helper新增query过滤，决定性测试先1 fail/25 deselected，修后完整26 passed/0.12秒（wrapper0.55秒）。Ruff与diff检查通过。此修复服务当前源码UI日志，不构成恢复打包。


### 源码真实 N1 失败与契约修复中（23:28 CST）

本轮 source-ui-n1-v2 使用 Host 28c94cdc 的源码 backend、当次编译的 Tauri debug UI、原安装 SDK a5c8fca/0.11.1。实际点击创建文档 Mission `mission-ee86ae060f8376e8`，通过文件选择器登记真实 ARCHITECTURE/AGENT_ORCHESTRATION.md 与 Host acceptance.md（逻辑名 HOST_ACCEPTANCE.md），要求比较原生 verify 与冻结安装验证边界，四条逐字依据、完整表格行及冒号限定单元，不降低原 N1 判据。

结果 **FAIL**。9 次真实 deepseek-flash Provider invocation：8 succeeded、1 failed。Worker A 三次把 #L、:行号、?lines= 当作文件路径，工具真实拒绝；输出把引用对象放入 `claim.evidence[]`，真实 parser 以 `claim.evidence[] must be a string` 拒绝。A 已消耗94642/100000 tokens，剩5358不足下一次20000预留，Mission budget_exhausted；不等于整个400000 Mission预算已花完。没有合格正式报告/Claim；HTTP成功与 UI启动不构成价值验收。

源码运行持续978.064秒，峰值组RSS772256KiB；菜单Quit后return0、PG86187 remaining=[]，失败证据保留。此时正常退出只是资源回收成功。SDK正修文档prompt v2/profile5：字符串evidence与结构化citations分离、逐字归属范例、明确文件读取路径、不修改旧v1或doc3/4冻结合同。首轮新控9 fail/1 pass；修后定向65 passed/1.42秒。独立审查指出文档质量goal可能没有Critic，正在补新版提交门；这65项不是该门完成证据，更不是模型改善证明。Host另准备明确标识的SDK源码开发模式，保留正式wheel身份验证，不构建发布包。

本机证据：
- Host `.local-test-evidence/2026-09-12/p33-g/source-ui-n1-v2/native.log` SHA-256 `d2c0fc79d090d158879ea85033ba632d7ff9448d689999723dca802330f3b227`
- Host `.local-test-evidence/2026-09-12/p33-g/source-ui-n1-v2/sources.json` SHA-256 `5485ffee38581c359aa59466b6bbd5312e56f9e7c6551c9edfd776501dc49c76`
- Host `.local-test-evidence/2026-09-12/p33-g/source-ui-resource-v2/resource.json` SHA-256 `4ce1f9fb9fa2a881768650aef6e92590796c8f795624ac677c061f218ec1e77e`
- SDK `.local-test-evidence/2026-09-12/p33-g/g-document-contract-green-v2.{log,json}`：65 passed/1.42秒，wrapper1.74秒，dirty a5c8fca。

计时：23:01恢复执行至23:28已用27分钟；22:38–23:01范围讨论单列。G自20:25累计执行约160分钟（扣除23分钟讨论），P3.3已记录执行至少351分钟；未知A/B前段仍不补猜。P3.3 G未完成，P3.4仅准备就绪草案，P3.5未实施。


### SDK源码开发身份接线（23:44 CST）

2026-09-12 23:44 CST：P3.3 G 源码开发接线新增显式 `editable-source` SDK 身份。正式 wheel 默认与 Service 0.3.13 pin 保持；仅非frozen进程、显式模式及独立source attestation可加载SDK源码。校验Git根/commit、两个生产包与数据全集hash（含新增文件）、editable安装metadata、版本及实际模块origin；文档修改不改变生产输入。main组装与RuntimeStack启动接线，编排manifest分别显示source_verified与installed_wheel_verified，不把源码当成旧wheel。71项定向控制通过/6.55秒，含真实composition启动前拒绝与依赖边界；主审及Ohm独立限定ACCEPT。实际源码冷启动与N1复验尚待完成；首次源码N1真实deepseek-flash任务因引用字段schema及Task预算不足FAIL，详见Host G journal。功能仍进行中，P3.4/P3.5未验收，打包/P3.6暂不执行。

独立开发venv以APFS clone复制依赖，editable只更新开发导入元数据；主backend/.venv仍旧wheel，未构建新的发布制品。`host-source-identity-v1`71 passed/6.73秒；格式/类型错误处理细化后`host-source-identity-v2`71 passed/6.55秒（wrapper7.49秒），不重复累计。Ruff新模块通过；既有composition/main/manifest在同py312配置下分别17/351/2项，和HEAD逐条(code,message)对照无新增。原始证据在SDK `.local-test-evidence/2026-09-12/p33-g/host-source-identity-v2.{log,json}`。


### 源码 N1 v3 与长结果/取消缺口（2026-09-13 00:20 CST）

源码身份接线已实际冷启动：Host d0c1ee4c clean、SDK a5c8fca 加当次未提交源码，307 个生产输入的聚合 SHA-256 `3851d2fc850c63d452f07cbe68876e25cb98bd7d12b76cf3f8efb09b894f4bc4`。manifest 为 editable-source / source_verified=true / installed_wheel_verified=false，Service 仍原 pin。真实 CUA 点击创建 Mission `mission-e2b63b91a02fde89`，文档 profile5；两份原始来源哈希仍 ad147a635d9292533bd440efbb12471beeba55b4e1df1637b514f93c2f28db8e / 505b4a72650cae886530ac980aeabd227eb76f0bbf80d2f091f88008c65d99f2，目标、三条条件与默认400000/12预算不变。Planner真实给三个Task均配format/rule/Critic；A/B/C预算120000/120000/160000。

业务验收 **FAIL**，Mission 通过 UI **CANCELLED**，不是自然预算耗尽：首Worker已提交可解析信封，format/rule通过并实际进入Critic；但workspace_read_file全量结果经SDK Context超过2048tokens后只投递1024字符预览，编排未提供可用续读。模型重复读取仍只见开头1–8行，明确说无法查看后文表格；四条完整关键依据/报告价值门未过，故主取消，保留现场后修读取链。没有把局限说明或局部rule PASS当完整报告验收通过。实际13次deepseek-flash invocation：9 succeeded、2 failed、2在退出时仍handed_off；后两项不伪称已结算。

另发现2342条HeartbeatReceived，其中2340条verifying，最高单秒20条；MissionCancelled seq2059后仍319条。候选因果链是Critic每0.05秒等待poll调用_hold_lease无节流、renew_lease无终态拒绝。正在补同事务终态/owner检查和半租期续租控制；未通过新控制前不标完成。

正常菜单Quit后PG94878无残留：源码载体运行701.909秒、峰值622560KiB，return0。此时防熄屏PID83049仍在，不修改永久电源设置。Host本机证据根 `.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v3/`，`failure-summary.json` SHA-256 `d4b99385e79ae7a1c31b63dc9efb87ed2ffc038c22870f03c91c277e69c11685`；索引含log/manifest/数据库及存在的WAL/SHM哈希。源码结果不代表安装包通过；N2–N6/O4仍OPEN。

测试记录分层：doc5实际Critic输出证明/原子settlement gate最终`g-doc5-proof-v3`71 passed/4.07秒（wrapper4.32），独审限定ACCEPT；历史fixture显式冻结旧版本，不把新默认回退。完整P33兼容回归`g-p33-compat-v5`819 passed/22.71秒（wrapper23.02），这发生于分页/续租新修复之前，不能作为后续变更已验。失败诊断v1–v4与原始setup失败均保留。所有原始测试证据仅本机ignored目录，后续新修复/真实UI仍需独立重验。

计时：从23:01恢复功能执行至00:20为79分钟；22:38–23:01的23分钟范围讨论另计。G已记录执行约212分钟（20:25–00:20扣讨论），P3.3先前191分钟加G为至少403分钟；未知A/B前段不补猜，不累计并行代理耗时。P3.3 G进行中；P3.4/P3.5仅设计准备，未实施完成；P3.6和打包暂停。


## 2026-09-13 01:04 CST：N1v4b 实际失败与摘要显示修复

最后更新：2026-09-13 01:04 CST。P3.3 G源码UI N1v4b已实际完成失败路径：Host e690bdcf、SDK e346689，Mission mission-61a22dea64fa4841为FAILED/budget_exhausted；真实flash35次成功、1次协议失败，已报告344854 tokens，不能说Mission花满400000。Worker1在90k Task预算下耗224780 tokens，Worker2耗115407，下一次Task预留时才拒绝。17页完整原文已实际可见，但模型一条引用错行、两条分析缺来源引用，rule正确拒绝。大页/逐请求预算与提交契约后继正在修复，N1/G未通过。原生正常退出PG2135无残留；不打包/P3.6。另修复Host诊断摘要读取：SDK摘要在detail内时不再显示空白；30项投影控制通过/8.31秒（wrapper8.97秒），实际UI后继重验尚待。
- N1v4仅启动配置失败：debug binary绑定Vite15173，而临时配置用了15174；没有创建Mission。正常退出PG1638，remaining=[]，61.939秒，峰值312800KiB。v4b改回15173并单独留证，无重建安装包。
- v4b root：`.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v4b/`；failure-summary.json SHA-256 `f97d0e0d83e115a89810fe0e4e59f73642300830d16815743409d455168cccfe`。包含稳定数据库/日志hash。原生实际界面显示失败与Task取消/失败，未生成最终REPORT；不会用六条resolved或formatPASS代替业务门。
- 固定来源ad147…与505b…，原目标/criteria/400k总预算不变。第5条quote位于134行却标133；第8/9条statement关联cite准则但无citations。Ohm逐页比对17页无错位，resolver正确。
- Host summary fallback：`projection._layer`保留顶层summary优先，缺失时取SDK detail.summary，并按原model/system类别显示。命令 `backend/.venv/bin/python -m pytest backend/tests/orchestration/test_g_document_projection.py backend/tests/orchestration/test_projection.py -q`，由共享runner串行执行，30 passed/8.31秒；PG5427结束。当前只代表后端DTO软件验证，源码UI复验待做。

## Source profile 与摘要投影组合验证（2026-09-13 01:51 CST）

显式源码模式使用同一个 DeepSeek counter 构造 Context tokenizer 与 provider token admission；只在官方 deepseek-flash profile 与显式本地 tokenizer 文件存在时接入。已有无 Context 身份的执行池不自动升级；wheel 模式仍保持旧入口。官方 tokenizer 是可选本地开发依赖，不下载模型或复制凭据。

主串行命令（工作目录 backend；绝对解释器为 Host ignored `source-sdk-venv/bin/python`）：`DEEPSEEK_TOKENIZER_PATH=<ignored pinned tokenizer> python -m pytest -q tests/orchestration/test_source_runtime_profile.py tests/orchestration/test_g_document_projection.py`。`g-host-source-profile-summary-v3`：16 passed / 0 skipped，pytest0.34秒、wrapper0.98秒，PG8677已退出。原始日志位于相邻 SDK `.local-test-evidence/2026-09-12/p33-g/g-host-source-profile-summary-v3.log`，SHA-256 `82b57a32cf2a6790a70040cb9eb52b4e2571a7b283c95c69a982acd880870d84`。

这是 profile 选择与 projection 软件验证，不替代新原生 N1 业务报告、重启和历史回读。N1v4b失败与SDK预算冷恢复首次失败均保留；source launcher恢复身份仍在修复。没有创建发布制品，没有改变N1来源/目标/400k预算/准则。

### Source launcher identity controls, 2026-09-13 01:57 CST

Main serial run `g-source-launcher-v1`: 19 passed / 0 skipped; pytest 0.11s,
wrapper 0.76s; PG9135 exited. Command from backend, using the isolated source SDK
interpreter: `python -m pytest -q tests/orchestration/test_source_orchestrator_launcher.py`.
Raw evidence remains in sibling SDK `.local-test-evidence/2026-09-12/p33-g/g-source-launcher-v1.log`.
SHA-256: `f36e1e392d59bef8578ffcad22a6c77bd2c42ec802b5034c3ddf93f7cf774009`.

The source launcher now binds the venv entry, executable hash, venv config,
distribution/pth manifests, resource/model directory metadata and small existing
manifest hashes, and fixed model override bytes. Resource payload bytes are not
verified; the identity explicitly records that limit. Carrier devUrl is an
operator-attested config/binary-hash binding, not an extracted binary value or a
new build receipt. This runs an existing debug carrier, one Vite and its managed
source backend without compiling or packaging. New N1 native startup remains open.

## Source compatibility checkpoint, 2026-09-13 02:08 CST

**Current source checkpoint, 2026-09-13 02:08 CST.** Host source profile/projection16 controls and launcher19 controls pass. Full orchestration source run v4 observed180 PASS/5 FAIL (164.55s); three frozen-package inventory checks do not apply to editable installation and remain unpassed under the user-paused packaging gate. The document fixture omitted mandatory critic_review and a path assertion matched the runner ancestor; both corrected controls pass in v5 (2 PASS/4.78s). Remaining source modules v6:27 PASS/1 fixture-path FAIL (31.78s); the pure scenario-path oracle now supplies a path actually outside ignored evidence and passes v7 (1 PASS/0.04s). Production test gates remain unchanged. These are source software checks; new N1 native run remains open, v4b failure preserved. No release packaging or P3.6 work.

The old document fixture was rejected with a durable `TaskGraphRejected`: missing critic_review. The updated fixture retains25 citations and now has the scripted independent Critic read actual REPORT.md before computing its verdict. It is a real SDK software path, not a real model or UI assertion. The lifecycle assertion is relative to supplied userdata, and the scenario oracle never creates its hypothetical ordinary directory.

- Sibling SDK `.local-test-evidence/2026-09-12/p33-g/g-host-orchestration-source-v4.log`, SHA-256 `b8043b60aee7d0755f4f14cd01f68f83443ad9bb5ec0a51f7be8abe7506318da`; exact command and elapsed time in adjacent JSON.
- Sibling SDK `.local-test-evidence/2026-09-12/p33-g/g-host-document-current-v5.log`, SHA-256 `dab39989e76a1c1cb46253b5d333f6bb139e9544bd9445697d8d40c1cb8295d8`; exact command and elapsed time in adjacent JSON.
- Sibling SDK `.local-test-evidence/2026-09-12/p33-g/g-host-remaining-source-v6.log`, SHA-256 `feb74ea58b82f180ee970676fe8d5540f6716d132e9c3f43f60c900e2164ca38`; exact command and elapsed time in adjacent JSON.
- Sibling SDK `.local-test-evidence/2026-09-12/p33-g/g-host-scenario-path-v7.log`, SHA-256 `f4c77b58e2280613a49ba18da1c4a02d440e862c49a6c8fe5daaf277333ff99d`; exact command and elapsed time in adjacent JSON.
