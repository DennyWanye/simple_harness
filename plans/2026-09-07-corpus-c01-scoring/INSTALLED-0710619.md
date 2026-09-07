# Harness 0.7.10 / Memory 0.6.19 / Service 0.3.13 安装候选

2026-09-07。SDK已审业务commit `031fdc688ceea604ffa409a06a69fd85071aa612`，后继文档fd12e7d只改ARCH/计划；从业务commit的不可变git archive离线构建一次，不因文档后继重建。Host nullable业务2d64e6e5、测试修fad81ebb、结果8217d400一起接入。原SDK079/Host4个唯一控制分批历史保留；新nullable源控制为SDK2＋Host2，原Host fixture缺真实入口失败保留，Dirac限定接受。

SDK有限nullable pair与Hostnull-as-absent适用性：required键存在规则独立，enum/const仍约束null；原非nullable string拒null；不支持anyOf或一般多非null联合。Host保留原proposal/null/hash，空串、空白和字符串“null”都不是无值授权；真实workspace/source继续精确核对。

- wheel SHA-256：`e559bc1b58ebfce0423247bc11ead0969f2364d76209fe7b9481bb2892ad2539`
- candidate manifest SHA-256：`0760be0f29eace4f78c874bfa1e3d9d41d38660080c1f42b640d2b883b719273`
- 新target：`/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/harness0710-artifact/installed`
- 唯一wheel构建后先放生产vendor，再从三个vendor wheel真实离线安装到新target；direct_url使用实际vendor来源，未手改metadata。旧H079 target与wheel均保留。
- 对新安装逐个比较wheel成员：Harness174 / Memory92 / Service116，全部字节一致。尚不将包成员一致宣称功能/原生通过。
- PG76369 exit0/0.437s/remaining[]/cleanupnull/stopnull，peak23328KiB，最低磁盘3832MiB；默认共享锁2GiB/180s。

Host pyproject/uv.lock/pin/manifest身份同批更新0.7.10。`uv lock --offline --check`首次因旧agent-reach固定URL的元数据不在缓存而失败；随后普通`uv lock --check --no-progress`成功（416包/2.41s）。仅检查，没有更新其它锁定版本；SDKwheel构建/安装全程offline。

实际安装组合控制已通过：Host `eaa72b51` 的 `backend/tests/quality/test_nullable_installed_main.py::test_installed_nullable_actual_main_catalog_and_wire`，1 PASS / 6.38s；wrapper 另确认生产候选身份及模块来自新target。覆盖实际main初始化、Memory/factory/工具注册、该installed SDK的本地wire/null正向与数字负向；没有调用真实Provider。PG76882自然exit0 / 7.470s，峰434080KiB，minDisk3783MiB，remaining[]、cleanupnull、stopnull。原coroutine警告保留，不扩大测试范围。

随后同一候选显式复验C01-20，真实请求HTTP400失败，详见[REAL-R4.md](REAL-R4.md)。安装控制通过不能替代模型质量。原C01-10/13/20失败保留，UI已在e1e714d2完成一次构建，不为SDK后端变化重建。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/harness0710-artifact/build.py` | `18a3123f9286ed1dc1cb3f6d39d06fdfb03dce6fbc1b984667a1844fb50f1701` |
| `.local-test-evidence/2026-09-07/harness0710-artifact/identity.json` | `6f56f2a75154497c550856c8b351cdb8942a7f6d4046f8f5e07a26288c046cc9` |
| `.local-test-evidence/2026-09-07/harness0710-artifact/r1/resource.json` | `04ce1f88966a64245e8db5f043b86504695e493a332c390857d5f74cef39a74a` |
| `.local-test-evidence/2026-09-07/harness0710-artifact/r1/command.log` | `5d5c03d7ad7a0224a9978940cb3d806438930dc97f7d2ab5653745043197bda8` |
| `.local-test-evidence/2026-09-07/harness0710-artifact/main-nullable-r1/command.log` | `a7d0435f5f933dcc27eb36e9e9641b96754e89ab55d5083a3962aa5f57b846cf` |
| `.local-test-evidence/2026-09-07/harness0710-artifact/main-nullable-r1/resource.json` | `6c0008962e56914a2a972b4b7c7a8aa86781c32341845493e6107d965e1af8fb` |
