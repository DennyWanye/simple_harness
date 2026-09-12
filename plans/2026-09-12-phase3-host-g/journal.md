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
