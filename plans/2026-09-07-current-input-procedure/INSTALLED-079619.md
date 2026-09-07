# H079/M619/S0313共同安装候选

最后更新：2026-09-07。Memory source `e27003c68b892fe061aac0ca2c9a140871f564cd`，业务为已审Procedure+current-input组合，版本元数据3项通过0.25s。只选受影响的历史快照/当前快照/当前README断言，旧2项facade未选。纯同步pytest未加载async插件产生1个asyncio_mode配置warning，非业务失败。

## 一次构建与固定依赖

- M619 wheel SHA-256：`c95cdf4852c3ca07a6d62f40aa3c8559f2715e7f4dfa966f9d412f8c063509d1`。
- M619 candidate manifest SHA-256：`b09e9fed7d3299afd8330aaa631bca45f92f6f05ea971ec29e795a5c381d822d`。
- clean commit经git archive抽取源码/元数据，复用hatchling1.32.0离线只build一次；uv --offline --no-deps将H079/M619/S0313安装到独立target，不创建新venv。
- 旧M618全部106根导出保留，新增12，总118；当前公开快照精确匹配运行时exports/migrations。Memory schema仍7.3，无新DDL。
- Host vendor、pyproject、uv.lock、sdk_candidate四处固定M619版本/文件/hash/source，旧wheel未覆盖。用户主checkout未切换、无tag/push/release。

## 安装后实际行为

固定Host83889984的唯一新增current-input/Draft组合，改用安装M619实际运行1PASS0.86s：签名SELF配置/实际claim、独立S1创建draft、混合两项前均可见，真实遗忘后仅draft拒绝，Host journal仍captured_bound、requesthash相同/快照变化。H/M/S全部174/92/116包成员逐字节一致；184个加载SDK模块全部来自target，无Memory源码overlay。未重跑旧source套件，不外推完整Procedure/native质量。

生产候选校验首次拒绝installed origin：初次target从build目录安装，虽然轮子字节相同，direct_url不是vendor。保留该失败，随后由实际uv安装器从vendor同一原wheel重新安装Memory元数据；没有手改direct_url或重build。复验生产verify_memory_candidate及pyproject/lock/hash精确关系通过，未再跑已绿业务用例。

## 资源

|步骤|PG|退出|总时秒|峰KiB|剩余进程|
|---|---|---|---|---|---|
|版本3控|49718|0|0.443|62400|[]|
|一次build/install|49794|0|0.444|31232|[]|
|installed新组合|49847|0|1.507|162624|[]|
|首次origin校验|50101|1|0.229|352|[]|
|vendor安装+生产校验|50135|0|0.226|720|[]|

所有cleanup_error=null。原生前端相对ff35fb82没有任何tauri-app差异，复用其已记录二进制，不重新编译前端。M619实际native启动与完整流程仍待验。

## 本机ignored索引

- `.local-test-evidence/2026-09-07/memory619-contract/run.py`：`0b1fcfd739e3065efbbd05a9fd03c3dc2878af4c5070a102f763ac04ef8151b9`
- `.local-test-evidence/2026-09-07/memory619-contract/r1/command.log`：`bbdc981560f7cccaec6cb70ffa348c5408cb3eb5451f39d14529aacf1ced552f`
- `.local-test-evidence/2026-09-07/memory619-contract/r1/resource.json`：`1d15b1a120db631b6e03169850546110e644589963a8ba94d2b9e7c7f4eda6e8`
- `.local-test-evidence/2026-09-07/memory619-artifact/build.py`：`c4f6917ec8497fe5dc193897eb1a949793a10ae1942e601cf1916d4241d60410`
- `.local-test-evidence/2026-09-07/memory619-artifact/identity.json`：`ea2cf299b418f9ed42a54a5fbbba5cca867131da9dd6849d2b9bc814f1bebfb8`
- `.local-test-evidence/2026-09-07/memory619-artifact/r1/command.log`：`2d61ab411d1583f5d44f6ef25626308fb12ba26187c7a2242e462300e531959b`
- `.local-test-evidence/2026-09-07/memory619-artifact/r1/resource.json`：`a42f379d5f4cdaff6ea2334346ec98618578eb4d16e6b37de6bb6f4243a707f5`
- `.local-test-evidence/2026-09-07/memory619-installed/run.py`：`1a091c522375fb85109d417c05518821be84da5342bce1cae59446e9a9070820`
- `.local-test-evidence/2026-09-07/memory619-installed/r1/command.log`：`6edbdf6855c1e52b6d4717078920b961e1ebf9a67b233ef94fd73219a3da75b8`
- `.local-test-evidence/2026-09-07/memory619-installed/r1/resource.json`：`ac324c84e04a9ff125abeac34a3b4becae261e3f5c26e12fb4c6cf99b504ae0e`
- `.local-test-evidence/2026-09-07/memory619-installed/r1/identity.json`：`2059854652659889e0a407d62ac6ed1e5398a1770d99b776be201466ecf137fa`
- `.local-test-evidence/2026-09-07/memory619-installed/pin_check.py`：`50b7fd32206e10fa6bbef489a01c808a9db0ac0178a27dd6b6de32690cd8e4c9`
- `.local-test-evidence/2026-09-07/memory619-installed/rebind_install.py`：`abd6c5f1275aaf8cf3362b16d1b018123579e0c1a40a770c84ce72c24cb780f0`
- `.local-test-evidence/2026-09-07/memory619-installed/pin-check/command.log`：`f07f883bf3afc5c2400145666297d93dbe27f74c58118b24d0c7c59746849db4`
- `.local-test-evidence/2026-09-07/memory619-installed/pin-check/resource.json`：`41b4c74bd0390389436ab71e432e933afb534de96a637cf357aabaa1f34aaad3`
- `.local-test-evidence/2026-09-07/memory619-installed/pin-check-r2/command.log`：`07ebb8dec46c0bcecbd37f397023f69697b00ff6a4f7b8e15c51884d07cce99e`
- `.local-test-evidence/2026-09-07/memory619-installed/pin-check-r2/resource.json`：`c3cb95e0c728f7b2da9cf2520879540937b58587fe6b8bf7e3dfa46309b059b6`
