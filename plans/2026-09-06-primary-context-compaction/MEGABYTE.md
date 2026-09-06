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
