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

## N1 source v5b actual failure, 2026-09-13 02:25 CST

Immutable source Host `133aaa62` / SDK `dfc9b7c`, doc profile6; actual UI Mission
`mission-a7960c5f9be8b736` **FAILED**, `max_attempts_reached`. The original two
sources, goal, criteria and Mission400000 tokens/12 attempts were unchanged.
Planner made one complete Task but gave it90000 tokens/4 attempts. First Worker
read both sources completely in4 pages; a later response spent8192 output tokens
on reasoning and returned no body. Its larger-output retry was denied by the
Task ceiling. Later zero-handoff failures consumed Task attempts. No REPORT or
Critic acceptance exists; this is not a business pass.

Eight physical handoffs (7 successful,1 empty-response failure) are all SETTLED.
Mission known usage86732 tokens (Planner4979 + Worker81753); actual current
reserved tokens0. UI incorrectly displayed44494 by summing historical initial
Attempt reservations. Source fixes for current-ledger projection, typed denial
stopping and Planner ceiling semantics are in progress, not yet UI verified.

Raw local record: Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v5b/failure-summary.json`,
SHA-256 `dc0cdd92e55ca404bdb331e7e5e6f4469a5499f986feea9ae0841a8e5fdb0291`.
Owned PG13015 exited normally; elapsed660.36s, peak921616KiB, remaining[].
Earlier v5 boot-only failure was missing sparse-checkout capability packs;
full same-commit runtime resources were provisioned before v5b.
N1–N6/O4 remain open. No packaging, release or P3.6.

## Budget/selection source integration checkpoint, 2026-09-13 02:35 CST
These are source tests, not N1 UI acceptance. P3.3/G, P3.4 and P3.5 remain open.
- `p35-tail-price-v1`: exit2, wrapper0.7s, PG16926; command `python -m pytest -q tests/orchestrator/p35/test_tail_and_priced_budget.py tests/orchestrator/p35/test_provider_budget_guard.py tests/orchestrator/p35/test_provider_budget_identity.py --maxfail=5`. Raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p35-tail-price-v1.log`, SHA-256 `fac0607f74c19f5785cbcd75c3dfec38632d2dc4024f9d1291b1aca52467b6e9`.
- `p35-tail-price-v2`: exit0, wrapper1.26s, PG17191; command `python -m pytest -q tests/orchestrator/p35/test_tail_and_priced_budget.py tests/orchestrator/p35/test_provider_budget_guard.py tests/orchestrator/p35/test_provider_budget_identity.py --maxfail=5`. Raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p35-tail-price-v2.log`, SHA-256 `ac75a7f88d4d202f0e7e1a5e53c50a93aa0e86b94c201e623314d35a82be2155`.
- `g-budget-public-v1`: exit0, wrapper5.57s, PG17239; command `python -m pytest -q tests/orchestrator/host_support/test_facade.py tests/orchestrator/p33/test_g_source_workload.py tests/orchestrator/step06/test_model_router.py --maxfail=5`. Raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/g-budget-public-v1.log`, SHA-256 `1727d03b467056b87bc1518249a7a9f882e0d22877fd639eae8442cc045875d4`.
- `g-host-budget-projection-v1`: exit0, wrapper9.09s, PG17301; command `python -m pytest -q tests/orchestration/test_projection.py --maxfail=5`. Raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/g-host-budget-projection-v1.log`, SHA-256 `d6e5c7545fbc7c35270891af5eb548302146b975a7711ec32f92df0e09a30ed2`.
p35 v1 had3 collection errors from an incorrect selection module import; fixed by its owner. v2:19 passed/1.00s. Public snapshot/workload/router:39 passed/5.12s, including simultaneous second-connection budget mutation remaining outside the original snapshot cursor. Host projection:22 passed/8.42s, including actual completed service ledger0, terminal held reservations and unavailable legacy values.
Frontend MissionsView first run47 passed/1 failed (new fixture reused a consumed request ID); corrected normal-load fixture gives49 passed/0.941s total (tests0.452s). Typecheck passed before the new fixture, and will be repeated for the final frontend state. Raw logs Host `.local-test-evidence/2026-09-13/p33-g/ui-budget-projection-v1/`.

## P34/P35 runtime integration, 2026-09-13 02:50 CST
- `p35-admission-collection-v1` exit1, wrapper0.7s; PG17691. Command `python -m pytest -q tests/orchestrator/p35/test_admission_collection_runtime.py --maxfail=3`; raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p35-admission-collection-v1.log`, SHA-256 `002acc7c54c87c34d539ae54e29a4aaf76deee1282017ecbd175d9350d82b8dd`.
- `p35-admission-collection-v2` exit1, wrapper1.02s; PG18132. Command `python -m pytest -q tests/orchestrator/p35/test_admission_collection_runtime.py --maxfail=3`; raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p35-admission-collection-v2.log`, SHA-256 `a8b10200c6f1dbad90ecb34760f79e54a70c720172bd67f7abbb365c00cf283d`.
- `p35-admission-collection-v3` exit0, wrapper0.56s; PG18144. Command `python -m pytest -q tests/orchestrator/p35/test_admission_collection_runtime.py --maxfail=3`; raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p35-admission-collection-v3.log`, SHA-256 `12c13b571d8e8108a46c806d2eb1b853b9b760aa4925f151f6ea89c325c4e808`.
- `p35-priced-cold-v1` exit0, wrapper0.64s; PG18391. Command `python -m pytest -q tests/orchestrator/p35/test_priced_budget_cold_reopen.py --maxfail=1`; raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p35-priced-cold-v1.log`, SHA-256 `59b9466559aeabf218a54ec041fd83cd357cf9bf1d80a17c47aab62ed0c205bf`.
- `p35-first-default-v1` exit0, wrapper0.8s; PG18526. Command `python -m pytest -q tests/orchestrator/p35/test_first_protected_tail_hooks.py --maxfail=3`; raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p35-first-default-v1.log`, SHA-256 `83ef11ace12d1ef8964708e04e58196db7ea23f86721f1686aa707958b594b11`.
- `p34-selection-fragment-v1` exit1, wrapper2.7s; PG18644. Command `python -m pytest -q tests/orchestrator/p34/test_candidate_selection_runtime.py tests/orchestrator/p34/test_fragment_scope.py tests/orchestrator/p34/test_fragment_runtime.py tests/orchestrator/p34/test_search_replay.py --maxfail=8`; raw sibling SDK `.local-test-evidence/2026-09-12/p33-g/p34-selection-fragment-v1.log`, SHA-256 `ffc52228cb494729224fc3a2a44e757d25f40c29a59bb0ee33286e27365aa1b2`.
Admission collection v1:3 FAIL/.43s from an empty list_intents query; v2:1 PASS/2 FAIL/.75s from main FIRST-release keyword-only call misuse. Both fixed; v3:3 PASS/.36s, actual SDK refusal routes stop or preserve UNKNOWN without blind retries. A SUCCEEDED provider record missing usage remains held because the existing late-accounting API cannot supplement a SUCCEEDED record; no automatic release or fabricated reconciliation.
Priced cold:1 PASS/.40s after both SQLite connections actually close/reopen, new owner epoch and actual response reconciliation preserve original price and charge once; this is not an OS-kill test. FIRST default:6 PASS/.58s, actual production Orchestrator (no overlay subclass) Worker write → Critic read/derived verdict → acceptance, failed/cancelled/UNKNOWN and terminal unused-tail release. Independent review scoped ACCEPT. Mission-level future conflict/synthesis pool implementation remains open.
P34 selection/fragment v1:3 FAIL/4 PASS/5 setupERROR,2.27s, maxfail8. Candidate mount identity mismatch and fixture tuple-vs-JSON contract require fixes. A separate code review found missing inherited candidate source dependencies in document C; fix and dedicated oracle are in progress. No P34 completion claim. Frontend search UI51 controls passed/1.01s total; its new test unused React import was fixed after a typecheck error; final typecheck rerun in progress. Native fixture/N1 and the remaining full audit still open.

## 2026-09-13 03:12 CST — source checkpoint (incomplete)
**Source checkpoint, 2026-09-13 03:12 CST:** integrated source checks:1037 PASS/2 legacy schema FAIL (48.27s); pre-schema15 reserved-attempt read compatibility fixed, targeted9 PASS/0.36s. Includes15 candidate controls, Mission system pool7, FIRST6, priced cold1 and missing-usage boundary2. COMPARE decisions and full immutable payloads now replay; frozen candidate deadline cannot dispatch new pending candidates. Controlled Host document fixture software2 PASS/6.21s proves28 formal citations and >256KiB paging; frontend search51 PASS/1.04s and typecheck pass. Fragment branch remains5 output-conflict failures (16 other controls passed); Mission-system runtime hooks, SUCCEEDED-missing-usage settlement, N1–N6/O4 and real P34/P35 gates remain OPEN. No release packaging/P3.6. This is an incomplete development checkpoint.

| Run | Exit | Actual wrapper seconds | Raw log SHA-256 |
|---|---:|---:|---|
| g-source-checkpoint-v10 | 1 | 48.75 | `bb7a232841dbc7adbf823791dc3777a4d89f1715c0de7b423ac9da07d3412b1c` |
<!-- Command g-source-checkpoint-v10: /Users/denny/projects/simple-harness-sdk/.venv/bin/python -m pytest -q tests/orchestrator/p33 tests/orchestrator/p35 tests/orchestrator/p34/test_role_context_runtime.py tests/orchestrator/p34/test_candidate_selection_runtime.py tests/orchestrator/p34/test_search_replay.py::test_actual_compare_snapshot_and_dropped_round_events tests/orchestrator/step02/test_workspace_and_gateway.py tests/orchestrator/step02/test_recovery_matrix.py tests/conformance/test_provider_contract.py tests/agents/test_provider_wire.py tests/agents/test_context_journal.py --maxfail=5 -->
| g-legacy-budget-v11 | 0 | 0.56 | `4326a6149a721258a65f0bb6017df2bef4fecac91560bf07f2f16de8517a5865` |
<!-- Command g-legacy-budget-v11: /Users/denny/projects/simple-harness-sdk/.venv/bin/python -m pytest -q tests/orchestrator/p33/test_p33_assessment_commits.py::test_schema9_readonly_and_upgrade_do_not_backfill_assessments tests/orchestrator/p33/test_p33_sources.py::test_schema8_readonly_snapshot_has_no_sources_and_needs_no_optional_fields tests/orchestrator/p35/test_mission_system_tail.py --maxfail=3 -->
| p34-selection-fragment-v2 | 1 | 4.03 | `5ec25d2141713ca52eae4df1301540d795fd6c9a0959402285c209490642a1b5` |
<!-- Command p34-selection-fragment-v2: /Users/denny/projects/simple-harness-sdk/.venv/bin/python -m pytest -q tests/orchestrator/p34/test_candidate_selection_runtime.py tests/orchestrator/p34/test_fragment_scope.py tests/orchestrator/p34/test_fragment_runtime.py tests/orchestrator/p34/test_search_replay.py --maxfail=8 -->
| p34-fragment-v3 | 1 | 1.54 | `a73440c8f252d56f01210948623e3bc960c1fa8ba3183e48f3dcd66b88d5d40a` |
<!-- Command p34-fragment-v3: /Users/denny/projects/simple-harness-sdk/.venv/bin/python -m pytest -q tests/orchestrator/p34/test_fragment_scope.py tests/orchestrator/p34/test_fragment_runtime.py tests/orchestrator/p34/test_search_replay.py --maxfail=6 -->
| g-native-document-fixture-v1 | 1 | 62.75 | `86dd06cc4e99556439297b7e63557530b47b8e9fe45a3e83ea0b283196d19f0b` |
<!-- Command g-native-document-fixture-v1: /Users/denny/projects/simple_harness/.local-test-evidence/2026-09-12/p33-g/source-sdk-venv/bin/python -m pytest -q tests/orchestration/test_native_document_fixture.py --maxfail=2 -->
| g-native-document-fixture-v2 | 1 | 32.76 | `5de15728be4ce5d6883a4d08365cd6ec7472e56e1a28a933ec0b88b9f8769e68` |
<!-- Command g-native-document-fixture-v2: /Users/denny/projects/simple_harness/.local-test-evidence/2026-09-12/p33-g/source-sdk-venv/bin/python -m pytest -q tests/orchestration/test_native_document_fixture.py --maxfail=2 -->
| g-native-document-fixture-v3 | 0 | 6.82 | `758386efe1e98f0b6a411c503f54e790941e389892603609533129a81b37d1b2` |
<!-- Command g-native-document-fixture-v3: /Users/denny/projects/simple_harness/.local-test-evidence/2026-09-12/p33-g/source-sdk-venv/bin/python -m pytest -q tests/orchestration/test_native_document_fixture.py --maxfail=2 -->
| g-host-search-launcher-v1 | 0 | 10.86 | `3964a55f44664e606922847e65fea363af16601f05e4fdf1dcb3f9e8ba6a5f70` |
<!-- Command g-host-search-launcher-v1: /Users/denny/projects/simple_harness/.local-test-evidence/2026-09-12/p33-g/source-sdk-venv/bin/python -m pytest -q tests/orchestration/test_projection.py tests/orchestration/test_test_scenario.py tests/orchestration/test_source_orchestrator_launcher.py --maxfail=5 -->

Evidence paths are SDK `.local-test-evidence/2026-09-12/p33-g/<run>.{json,log}`. Source runtime remains development-only; no overall SHIP verdict. N1 v5b failure is preserved. Fragment v2 failures were bad dependency field/mapping shape; v3 reached the real output collision, still unresolved. Native fixture first callback used a string as function; second omitted required continuation SHA; both fixed without bypassing formal verification.

## 2026-09-13 03:26 CST — N1 v6 failed; context/criterion repair in progress

N1 source pair Host `aaf33d844ac30ec08068eb08ec2e76f5ccb1e777` / SDK `6360c20591e38d1708ac73a96fb9b39e328c2a55` passed the clean core smoke23/2.53s. Actual UI imported unchanged original sources and submitted Mission `mission-f15fe077e90a4ef2` (400000 tokens/12 attempts). Planner assigned sole Task the entire ceiling, fixing the earlier stranded-budget allocation. Worker produced REPORT.md (SHA256 `8cea3c8371774ef105855d7d159fc9f66f7add576b434c0a4d61f6dca71f075d`) and9 proposed claims; seven literal source statements had valid citations but additional generated free-text report-quality criteria caused `missing_limitations`. Critic was SKIPPED after rule FAIL. No formal Claim acceptance or native business PASS.

The next11 attempts made zero physical calls: the required retry package duplicated large assessment envelopes/display blocks (~64005–65630 tokens against32768). Seven total physical handoffs:6 succeeded/1 empty-response failed;11 additional CLAIMED records were never handed off. Mission settled128156, currentreserved0; Task123559; no zero-cost assertion. Actual UI confirmed failed/max_attempts_reached and correctly displayed128156/0, opened rejected REPORT, then quit. OwnedPG20772 elapsed450.12s peak724912KiB remaining[]. Failure summary: Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v6/failure-summary.json`, SHA256 `d6822c6ea1f4df2cac70dd1f72d7d4a7e0c01cf651568632d92ed10a99a70c85`. Original failure is immutable, not retried in place.

Repair: machine repair feedback retains all required missing claim/criterion pairs, reasons and original-record hash without reinlining historical source prose; unchanged current goals/contracts/catalogs remain full. Planner guidance distinguishes original success criteria/source facts from report-quality requirements retained in goal and independent Critic review. ContextRequiredContentTooLarge now stops unchanged-contract retries without Provider-health downgrade. Targeted5 controls pass/0.45s; first unit fixture exceeded the existing20000-character Attempt.feedback contract and was corrected, not production limits.

Other source results: joint system/accounting/fragment run50 PASS2 fragment-runtime timeout FAIL/33.44s; fragment failures pending. Broad integration1051 PASS2 FAIL3 optional-tokenizer SKIP/52.52s (missing optional env is explicit); new system hook regressions under repair. Priced system rounding reserve has an independent P1 finding and is not complete. First-page native fixture delivery delay1.5s preserves original response and only applies to ignored document-ui scenario; Host command controls12 PASS/17.59s after relocating a misplaced existing assertion. N1–N6/O4 and P34/P35 whole gates remain OPEN; no packaging, release or P36.

## 2026-09-13 04:13 CST — usage recovery and source UI checkpoint

**Source and native checkpoint — 2026-09-13 04:13 CST:** SDK protocol-error response parsing preserves independently valid Provider usage while still rejecting malformed tools (26 PASS/1.36s); missing/invalid usage stays unknown. Late-accounting automatic original-subject import/settle11 PASS/2.90s and receipt boundaries5 PASS/0.47s, independently reviewed. Citation repair retains failing claim/index/source/line identity without source-body reinlining3 PASS/0.46s. Broader integration v14 is still running/stalled in legacy recovery, not PASS. N1v7 was UI-cancelled after malformed-tool response without usage,170532 settled/108083 unknown held,13 physical handoffs, no successful value acceptance; original proof retained. Controlled source UI N2 delivered two28-Claim Missions (750 tokens each/zero reserve), actual long block290080 characters reached END_OF_LONG_TABLE, in-flight citation switching/CAS error and restored retry observed. N3 source supersede/revoke-reject/revoke-approve and historical read observed; cold verification in progress. N4/N6 boundary software27 PASS/10.67s including launcher identity, native cases not yet run. Whole Phase3 gates remain OPEN; no packaging/P3.6/push.

N1 raw evidence: Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v7/failure-summary.json`, SHA-256 `5f19501b1e24ecb2dea3277f2e1520124f34cac385c4eec4ee30b8adad4ebfc9`; original process group25765 exit0/905.089s/remaining[]. N2 first session PG31617 exit0/959.462s/remaining[], cold session separately recorded. CAS fault original bytes restored and SHA verified; no evidence uploaded.

| Run | Actual pytest result | Measured wrapper | Log SHA-256 |
|---|---|---|---|
| `g-feedback-citation-v2` | 3 PASS /0.46s | 1.21s | `d67af6276960c86f8235293a7fd7c89828928cae760895086fdc5286a51dbb55` |
| `g-protocol-usage-v4` | 26 PASS /1.36s | 2.37s | `877eba3264006b10ef3ad3f249b5448625ecc1deadb00a4967c191be6183cd87` |
| `p35-accounting-boundaries-v1` | 5 PASS /0.47s | 0.75s | `9fe17b0188270dee55574f487842bfdc85861c7dca5c4c925b90b1f6bf41702f` |
| `p35-late-accounting-runtime-v2` | 11 PASS /2.90s | 3.42s | `3d7dbb7b864b63bf38cfcebc3cecbf257839202a13fe3a4e89d37cfe6f58aa23` |
| `g-host-native-cases-v3` | 27 PASS /10.67s | 11.24s | `d3cd8dd70daecb4c3737a8bfc1ffaafc49740f4004e26c7f01855eaa7bd9ca82` |

## 2026-09-13 04:15 CST — N2/cold lifecycle and compatibility checkpoint

**Current checkpoint — 2026-09-13 04:15 CST:** controlled native N2 and terminal-source lifecycle checks completed, including >20 Claim scrolling, 290080-character table end, A/B and Mission switching, real CAS error/restored retry, supersede approval/revoke rejection then approval, original citation read after cold restart. Two Missions retain28 Claims each/750 settled tokens/zero reserved;10 physical fixture invocations unchanged across cold restart. No real-model quality claim; active-revocation and conflict-arbitration native controls remain open. Integration v14:1105 PASS/1 legacy recovery FAIL/346.49s; original Attempt executor_stalled after180s then a second Attempt violated original no-rerun oracle. Focused unchanged recovery matrix v15:8 PASS/20.70s. Underlying intermittent stall is unclassified and retained, not dismissed as flaky or closed by the rerun. Local source checkpoint only; overall N1/P34/P35, offline backup and FIRST bounded initial request remain in progress.

Controlled native raw evidence: Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n2-v7/`; cold-resume-summary.json SHA-256 `2d8dc865891988e11f3fbdd5f638ff373e0fb93166678a4e52da150ffd03c88d`. First session959.462s, cold session234.642s, both owned groups fully reaped. Whole native wall time includes concurrent software review; do not add it again to engineering elapsed time.

`g-checkpoint-integration-v14.log` SHA-256 `e578859fedf850bce1bf5bd6d84128b80b745e9ffcd478c2eed415fd7f20d99c`.

`g-recovery-stall-v15.log` SHA-256 `6de81b2c3ba4378803b3f919aea51778c87f8f6a46b555e57b266023156fd61a`.
## 2026-09-13 文档回写：N1v8失败边界

Host `985e403` 的 N1v8 本机 UI 证据保持 FAILED，预算为 378113 settled、reserved 0、budget_exhausted；实际 worker 多次 physical 调用但 `progress_marker=None`，最终 180s executor_stalled，主线正在修复。原始证据相对索引：`.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v8/ui-failed.ax.txt`，SHA-256 `7ef99cc4f4c5b7bec8347559dcac65ecc839268110354c742b68c72d02470041`。此条不构成恢复/备份 UI或OSkill通过；P33N1/P34/P35 仍 OPEN，P36/打包暂停。SDK 对应源码工作树为 `e4da042`，kernel+backup 未提交。


## 2026-09-13 04:49 CST：持续 Provider 进度修复与来源撤销控制

**当前源码检查点 — 2026-09-13 04:49 CST：** P35 离线备份 20 PASS/17.29s；租约丢失恢复及取消 10 PASS/23.14s，受影响取消/恢复/租约回归 44 PASS/6.68s。N1v8 真模型仍失败：18 次实际调用、378113 tokens 已结算、当前预留 0；已定位 RUNNING 时终态 ordinal_to 为空造成 180s 错误超时。改读 SDK 持久进度的定向检查 8 PASS/8.98s，保持真正停滞超时控制；Host 六类文档场景及启动器 28 PASS/12.74s。上述为以 SDK e4da042 / Host 985e403 为基线的未提交修复证据；新原生 UI 待验，P33N1/P34/P35 整体 OPEN，不打包、不执行 P36、不推送。

- g-live-progress-red-v1: FAIL: 1 / 1.97s; original false AttemptTimedOut; SDK repo .local-test-evidence/2026-09-12/p33-g/g-live-progress-red-v1.log SHA-256 `8bc8b9da90b074816cd0bac6331b9a3443658a657f153146b1127ad22179664c`.
- g-live-progress-green-v2: 1 FAIL / 7 PASS / 10.18s; duplicate fixture tool requests reached no-progress termination; SDK repo .local-test-evidence/2026-09-12/p33-g/g-live-progress-green-v2.log SHA-256 `a39cd35aefac0aa128ff4b889811ee22d04e65197959627fbbb0d6b013d75a7d`.
- g-live-progress-green-v3: 8 PASS / 8.98s; ordinary paced Worker retains one Attempt and stalled control remains enforced; SDK repo .local-test-evidence/2026-09-12/p33-g/g-live-progress-green-v3.log SHA-256 `8a7c7a96a4658b3c17f522d2ced2ee2fc3ca51364085bb3f0e45d2279ffe1639`.
- g-recovery-impact-v16: 44 PASS / 6.68s; SDK repo .local-test-evidence/2026-09-12/p33-g/g-recovery-impact-v16.log SHA-256 `695a634a7f1efcb54057461837664bb1ce625dc9e23ea6e127b556b956d347e2`.
- g-native-six-cases-v4: 28 PASS / 12.74s; SDK repo .local-test-evidence/2026-09-12/p33-g/g-native-six-cases-v4.log SHA-256 `449ffa826f008e03c7b54e91bfff0d9cdd8e5bb8a7cdd5e95791af02d6adb79a`.

N1v8 failure summary: Host .local-test-evidence/2026-09-13/p33-g/source-ui-n1-v8/failure-summary.json SHA-256 `56e4cca4aef75dc4d992f7782cd49daddd642913a0716fff5987144fb6bceac6`; owned PG39303 exited, remaining children zero. No formal accepted report.

独立审查：Terra medium 单次只读审查本次 kernel/live-progress/native-active-revoke diff，未发现 P0/P1。保留在途 Provider lease-loss 与真实 OS kill 后续验证，不据现有测试泛化全部恢复路径；源码检查点提交，整体仍 OPEN。


## 2026-09-13 05:25 — N1v9 正式交付与冷恢复

**源码与原生 UI 检查点 — 2026-09-13 05:25 CST：** N1v9 原始两文档、400000/12 原目标在 SDK c9a1f183 / Host 45c09756 源码环境完成：220.968s，正式 REPORT f6b192a3…f905、6 条 VERIFIED 逐字引用（两来源、完整表格行、完整限定单元），242431 tokens 已结算/预留0，13 次 Provider handoff。真实 UI 读报告、引用并冷启动重读，调用仍13/无重复；文档区“尚未判定”投影缺陷已修复，后端13 PASS/0.06s、前端25 PASS/0.912s及typecheck通过，新 UI 待验。动态新增已完成依赖的 Task 回放修复42 PASS/36.16s，原 v14 #14 历史43事件全覆盖/无差异；Python3.12空AST字段兼容35 PASS/0.29s，保持原生产基线。总体P33/P34/P35仍OPEN；进程kill测试仍在修复，FIRST请求保护仅helper7 PASS未集成；不打包/P36/推送。

原始本地证据：`.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v9/`，mission `mission-a13c50d355d83850`。当前正式报告SHA256 `f6b192a3d180b3a43e9f2ce69184810f2403bf12aa36a0f51566327fac33f905`。首次cold命令在启动前因端口bind拒绝，监听检查无残留；后续cold2同源恢复成功，不记首轮PASS。初始owned PG50346退出0、剩余0，1283.331s包含终态人工阅读等待，不等于220.968s Mission运行时间。UI显示缺陷根因：SDK成功报告提供success_criteria与stop_reason，没有final_report.result；Host只在冻结条件逐条严格匹配、判定完整时显示实际结果，保留STRUCTURAL覆盖类别。测试：`backend/tests/orchestration/test_g_document_projection.py`13PASS；`MissionDocument.test.tsx`25PASS；typecheckPASS。新修复原生UI待验。


## 2026-09-13 05:40 — 原生边界与实际OS恢复

**原生边界与恢复检查点 — 2026-09-13 05:40 CST：** 新冻结 SDK c8e2541 / Host b7dc4c64 综合1127 PASS/75.26s。N1v9真模型正式交付及同源冷恢复已核对；新Host显示修复在受控原生来源指令用例验证。N4来源指令归属、错误逐字引用、矛盾证据三例原生UI符合预期，独立原始证据保存在Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n4-*-v10/`。实际OS SIGKILL后两库冷恢复2 PASS/9.59s：成功结果零重复Worker、独立Critic读产物；UNKNOWN保持原token/cost占用。仅覆盖该两边界，不覆盖完整Mission或P32逃逸进程恢复。FIRST新保护虽18PASS/0.91s，独立审查仍有系统hold丢cap和priced分别取整2项P1，修复中。P33剩余N6/active管理/O4、P34综合价值场景及P35其余门槛保持OPEN，不打包/P36/推送。

OSkill selector：`tests/orchestrator/p35/test_process_kill_recovery.py`；raw `.local-test-evidence/2026-09-12/p33-g/g-process-kill-astra-v3.{json,log}`，wrapper9.87s。Terra初稿及三轮返工保留：v1两项importFAIL0.65s；v2 marker45sFAIL/第二case无壁钟上限，主线程139.62s中止；Astra重做合法envelope/真实分库/硬超时后首次实测2PASS。新启动器SO_REUSEADDR只允许TIME_WAIT重绑定，仍拒绝live监听，定向20PASS/0.11s。所有新证据不入Git。


## 2026-09-13 05:44 — 保留不确定性展示边界

**N6 显示修正 — 2026-09-13 05:44 CST：** 原生 n6-half 验证1/2不确定条件按原策略可交付，局限与INCONCLUSIVE均保存；UI“实际判定：满足”措辞会误导，已改为保留不确定性，Mission统一显示“通过交付判定”。新增UI反例先红，修复后26项通过；新快照原生复验待完成，不能把该措辞修正算原生PASS。

本机证据 `source-ui-n6-half-v10` 的 Mission `mission-e2c32d6b00f2d5dd`，Task PASS/Mission COMPLETED，原条件1/2 INCONCLUSIVE，900tokens预留0。UI反例与修复日志为 `g-ui-uncertainty-{red-v1,green-v2}.log`，来源/.local-test-evidence/2026-09-13/p33-g。底层判定/阈值/来源均未改变。

## Functional checkpoint 2026-09-13 07:11 CST

No packaging, P3.6, release or push. Host local source commit cbc89f81 contains current-doc7 arbitration fixture and real service-route oracle. SDK crossbranch production and cold-context tests are still in progress.

| Main-run receipt | Exit | Wrapper seconds | Receipt SHA-256 |
|---|---:|---:|---|
| `g-checkpoint-integration-v20` | 1 | 97.21 | `f9a188d650e21399476e9ef47e4836b801019e32b862979ff10b40a154e05c70` |
| `g-manager-active-dedup-v1` | 0 | 7.41 | `e1abc65f658d92deec24d5f40267f0b87b7adc1e3610e3ec11f28398b9903621` |
| `g-queue-deadline-review-v4` | 0 | 8.27 | `702ec1ff96317f18839801206d99528a26453c2ed03375d24cd57fe897b5cf00` |
| `g-host-doc7-route-v5` | 0 | 26.05 | `0714018bb72f7aef0c89aeef9cd164ae574b8b4e0dd6c57f1378c0040a4ceff2` |
| `g-context-cold-v2` | 1 | 5.41 | `6c57ed5e76f8b18b586661ba3783b3b4c086647c8431f324f4cae39f7977e4b9` |
| `g-context-cold-v3` | 1 | 6.23 | `44e6e3818204a0c8850e1a1fef55cbc525a51bbe06b96b0683534c532e33ff27` |

Counts are per actual batch:integration1133 PASS/2 FAIL in96.74s; active Manager5 PASS/6.84s; queue/Manager6 PASS/8.02s; Host route14 PASS/25.55s. Context cold-v2 failed on logical-versus-wire hash assertion; v3 reached SIGKILL and failed in cold zero-call/retrieval assertion. Failures are preserved, not acceptance.

All existing source native runs:16 closed orchestration DBs,17 Missions plus16 deployment streams,33 replay observations PASS/0 findings/0 errors,0.532s. Before/after Provider/tool/action/selection/event counts identical. Host `.local-test-evidence/2026-09-13/p33-g/replay-all-native-v1.json`, SHA256 `376f19c68746cee73c8f186fb8b66e4aa099e3f2cb944718d2db34013b15cce5`. This is orchestration replay of existing runs, not complete execution-DB replay nor future-run acceptance.

Outcome: DEVELOPMENT IN PROGRESS; current P3.3/P3.4/P3.5 exit gates remain OPEN.

## 2026-09-13 07:35 — Conflict reserve form and cold action backup

**最后更新：2026-09-13 07:35 CST — 冲突核对预算表单。** 新建 Mission 可显式从总 Token 预算中预留冲突核对额度；留空不发送预留字段，非法值、负数、小数及超过总额会阻止提交。文档原子创建和重试保持该值，创建成功后新表单清空。Luna 独立审查发现并复验通过跨任务残留问题；主线程前端 85 PASS/1.33s、typecheck PASS。原始证据 Host `.local-test-evidence/2026-09-13/p33-g/g-ui-conflict-reserve-{green,typecheck}-v4.log`。当前源码原生仲裁表单仍待验，P33/P34/P35 整体 OPEN。

- `g-ui-conflict-reserve-green-v4` SHA-256 `d3343335dfebc69bc8114482db2642b90e24412e41e85d0b5ac9a07cfcd52caa`.
- `g-ui-conflict-reserve-typecheck-v4` SHA-256 `5b7678e1378357b8e083c5d0c9913336411378485c091e11ef7cbd1372a06460`.

Actual external effect/SIGKILL/cold UNKNOWN/offline three-DB backup/restore/reconcile: 1 PASS/8.35s, wrapper8.57s. SDK receipt `g-action-cold-backup-v5.json` SHA-256 `464dbbac72963b78979206b97a161add1355199913bc98b52e1fcde5b6598080`. Main corrected ordered Critic fixture coverage and both unsettled NULL expectations; earlier failed runs retained. This is software recovery evidence, not native UI nor real-provider quality. P34 independent review has three P1s (retry identity, criterion coverage, eligible dependency identity), repair in progress.

## Native arbitration source-v13

**最后更新：2026-09-13 08:12 CST — doc7 原生仲裁与冷重开。** SDK aaa3593 / Host 648ad185 的源码快照v13，经真实表单导入两来源，240000/6总预算与30000冲突预留实际生效；两Worker/Arbiter/独立Critic后UI进入待仲裁，展开两份完整原句、通过UI提交contextual并打开实际245B仲裁报告。审批GRANTED、Conflict RESOLVED_BY_HUMAN；两原主张保持DISPUTED、0知识条目，Mission按claim_not_usable成为mission_criteria_unmet（不是交付成功），2850已结算/0预留。新建表单预留为空，原生复验了跨任务残留修复。相同源码/数据冷重开后原身份与状态保持，Provider19/19、0rehandoff、12工具效果不变。原始证据Host `.local-test-evidence/2026-09-13/p33-g/source-ui-arbitration-v13/case-summary.json` SHA256 `5c7946166a2593bdafd0edfb5f92a53bdf1400fec9d652c2297d232c41a9a967`。初始/冷进程组均退出0无残留，539.712s/43.737s为包含人工操作等待的载体生命周期，不是模型运行时间。此为受控原生仲裁边界通过，不是真实模型能力或Phase3整体完成。


## Next source value/load acceptance (2026-09-13 08:15 CST)

Native doc7 arbitration/cold is completed within the explicit boundary above. Next: real deepseek-flash original N1 input recheck on SDKaaa3593; native P34 real Manager/fragment/consumer/synthesis visibility and controlled search policy; multi-Mission source UI queue/cancel/UNKNOWN and backup visibility. P3.4/P3.5 overall remain OPEN. No packaging/P36/push. N1 original 400000/12 goal/criteria and both source byte hashes copied from prior successful v9 read-only DB/workspaces into ignored n1-original-inputs-v13; no changed acceptance.


Source-native slot binding (2026-09-13 08:24 CST): launcher supports explicit 1..4 logical/model slots, default 1/1; values are part of the source identity and are written only before first backend startup. Resume checks exact integer config without rewriting seeded policy. Older source runs require their recorded pre-slot launcher. Main verification: g-source-slots-search-v2, 32 PASS/2.09s (runner2.70s; includes controlled search software test). Terra rework fixed incomplete CLI oracle and TOML bool equality; native multi-Mission load remains OPEN.
