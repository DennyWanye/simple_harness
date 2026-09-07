# 1 MiB 当前运行分页边界

更新：2026-09-06。固定测试源码5d8c74fe，H078/M618/S0313安装组合。

新增控制1PASS/6.32s：实际隔离文件两次读取结果分别1,065,776与1,066,691字节，经当前运行公开effect来源、精确尾页及物理HTTP请求链路；冷重开保留依赖且不重发。8次请求分别5,046、6,444、7,148、8,711、9,925、26,748、28,209、26,323字节，均小于256KiB，完整大结果未回流。未重跑原小结果矩阵。

这是受控fixture写入扩充文件后的真实readback与HTTP MockTransport组件证据。模型窗口32,768；不代表标准write_file本身会返回文件全文、不代表原生或真实模型，4k/8k及完整TC-HM11仍未关闭。

资源PG7739正常退出0，7.358秒，峰552,848KiB，最低磁盘5,557MiB，remaining_group_members=[]，cleanup_error=null。173/84/116包成员与vendor逐字节一致；模块来自固定installed target。

命令：既有Python运行scripts/run_resource_bounded.py，默认2048MiB/180秒，共享锁；子命令为同Python `-I -B .local-test-evidence/2026-09-06/megabyte-current-pages/run.py <r1绝对路径>`，仅执行test_current_tool_megabyte.py。

证据均留本机ignored目录：

- `.local-test-evidence/2026-09-06/megabyte-current-pages/r1/command.log` SHA-256 `55de899ad8acb5c7ef42aa898b1d1ad07a5db1a753d083c2ddbfcd42161e620f`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/r1/resource.json` SHA-256 `0df4359f11f48e6e73f0d25e18c9dec32204fe0a34332c3aeacfb5d47e711357`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/r1/identity.json` SHA-256 `33e109a279b1796641bb433548a6ff03b5a7872ac150c16888753eff5ed899e5`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/r1/cases/test_actual_megabyte_result_pa0/megabyte-metrics.json` SHA-256 `09bafb4712107b24645f9d11a2643e6892772b370496e003ea6a98fffcea565d`

## 小窗口增量：分页成功与预算拒绝分开

固定6d447062在4k/8k沿用原大型assistant写入参数：small-r1两条FAIL，实际sdk_context_budget_exceeded。该fixture缺少生产ClosureFallback，后继Host终态closure_pending不能外推主应用死锁。原红保留；没有提高窗口、删保护信息或重跑32k。

固定1a66249a改用三个小输入参数驱动同类实际readback，隔离大结果压力与大型assistant参数压力：small-calls-r1的8k场景PASS；两个文件1,048,586/1,048,601字节，8次物理请求最大19,219字节，公开精确尾页及重开通过。该批4k仍预算拒绝，未达到分页完成。整批1PASS/1FAIL/8.81s，PG8727正常退出且清空。

固定78899d07仅补4k拒绝后的生产收尾负控：原ContextBudgetExceeded原样传播；安装真实ClosureFallback，禁止失败收尾构造模型/变更。实际SDK FAILED、Host队列清空、Scope保留pending/closure_run_not_completed；冷重开零重发，预算拒绝后无新HTTP。refusal-r1为1PASS/4.51s，PG8943正常退出并清空，峰550,832KiB。这是安全拒绝PASS，**不是4k分页完成PASS**。32k及8k绿色场景未重复。

边界遵守原S5预算契约：保护当前open因果组与控制凭据，允许裁剪仍不足时拒绝超预算请求。真实Provider估算校准、完整4k正向旅程和全TC-HM11仍未关闭。

新增原始证据索引：

- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-r1/command.log` SHA-256 `4334735ce410b1a3f92c37378285bf03995cff6c53a9a9cc2a2e9fb144253af8`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-r1/resource.json` SHA-256 `56704f707c838529886e8fab2d7e743bba865210c42a6386d05984aab55f5698`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-r1/identity.json` SHA-256 `4bec52a839e1c90644498c0110489870b63b50c1cfaf535f727e7bd1e5eb6ce6`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-calls-r1/command.log` SHA-256 `171dbb7c106eb629643bca549ba5759521aa2b180dcae5fb063d1b29a05adcd1`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-calls-r1/resource.json` SHA-256 `1d8a7d88bd6e36eb1d15374bf18c217cc8397f36b3d5734ae47e2a1f8fe7c0ac`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-calls-r1/identity.json` SHA-256 `4bec52a839e1c90644498c0110489870b63b50c1cfaf535f727e7bd1e5eb6ce6`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-calls-r1/cases/test_actual_megabyte_result_pa1/megabyte-metrics.json` SHA-256 `f14b977f73752d532226d273ec72f1fe17caa48a9ee58e16eccd1f9d32227fef`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/small-calls-r1/cases/test_actual_megabyte_result_pacurrent/megabyte-metrics.json` SHA-256 `f14b977f73752d532226d273ec72f1fe17caa48a9ee58e16eccd1f9d32227fef`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/refusal-r1/command.log` SHA-256 `cc071a5cb807d6fa561c5791039bd680567aed55a1738f1b4afc510330bdd91c`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/refusal-r1/resource.json` SHA-256 `6b45f45e734d1a8949acaa6950ae6f1002175a7f90c093d0cb0b30a4129e1610`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/refusal-r1/identity.json` SHA-256 `33e109a279b1796641bb433548a6ff03b5a7872ac150c16888753eff5ed899e5`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/refusal-r1/cases/test_actual_megabyte_result_pa0/megabyte-metrics.json` SHA-256 `9d8018f2b184724ea4b77208a6d6ccfa082441f6a7aa5584ab6152156257796a`
- `.local-test-evidence/2026-09-06/megabyte-current-pages/refusal-r1/cases/test_actual_megabyte_result_pacurrent/megabyte-metrics.json` SHA-256 `9d8018f2b184724ea4b77208a6d6ccfa082441f6a7aa5584ab6152156257796a`
