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
