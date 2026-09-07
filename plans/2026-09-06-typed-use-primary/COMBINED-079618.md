# H079/M618 安装组合

2026-09-06。Host源码69a9cbd8，已合独审mandatory recovery Host/SKD源码。Harness 0.7.9源f841098b11c40192f1ad6b5676148533567ba706（已审59085a8后仅版本分配），一次离线构建，hatchling1.32.0。原H078制品未改；没有push/tag/release或用户主树切换。

wheel SHA-256 `ba1c13cd711635c9f59555d087422766d597fb7e213f954af26bcf18b9cdb02d`；candidate manifest `ffa5fb115bba2fb2c124275c97c3d48beef15f5c8abb6f2d8bb38ff8533c63f8`，execution schema9，新的repair-bearing检查点wire8。Memory0.6.18与Service0.3.13保持原制品。旧schema8不能交H078执行；原r16失败没有被重新标成成功。

仅3项受影响安装组合：真实main factory、实际首零tool→拒绝反馈→真实ACK→完成、候选精确身份，**3PASS/4.37s**。SDK源码14个已绿控制未重跑；这3项是新包/Host组合检查，采用确定性HTTP，不是原生/真实模型结论。

174 Harness、84 Memory、116 Service成员逐字节匹配vendor，202个已加载SDK模块全部来自新小target；没有新venv或源码overlay。build PG21785 exit0/.447秒/remaining[]；install PG21828 exit0/.223秒/remaining[]；组合PG21846 exit0/5.341秒/417072KiB/remaining[]/cleanup_error=null。最低磁盘4397MiB。资源锁释放供原生r17。

原生仍待验，r16实际pending/no_recall失败保留。原ACK/mandatory-exit语义、预算上限和新鲜来源校验未放宽；最多两次恢复，失败仍保留未处理提醒。

本机ignored证据SHA-256：

- `.local-test-evidence/2026-09-06/primary-079618/build_sdk079.py`：`12a1104397bb9a7c026bf65df049f4ef2e5cbbbc475dc8923547a537e84eff53`
- `.local-test-evidence/2026-09-06/primary-079618/install.py`：`5ce77de63b5714a670e0be62060c77df268ab28151b8202c9876201b284a7474`
- `.local-test-evidence/2026-09-06/primary-079618/run_controls.py`：`6ff85eea5d07a6feb22556a5ebaef1b8e441b1428a7a81ed54f378dca75bdc7e`
- `.local-test-evidence/2026-09-06/primary-079618/install-resource/resource.json`：`43989335425ae959f29d7637667f23aa60b1dbf17ccd5e9b5c073b6f8415bef2`
- `.local-test-evidence/2026-09-06/primary-079618/r1/command.log`：`4984f5e57452c6f9975995c8f03acd332b46f7667f6cb1e7f6172f238dde2720`
- `.local-test-evidence/2026-09-06/primary-079618/r1/resource.json`：`7bed2c9300e954dd83e2b3fa7facc539a6163f4890be2e7c26f71276dc65495b`
- `.local-test-evidence/2026-09-06/primary-079618/r1/identity.json`：`63433b7404a82354c8a25962bfd67b50fecf5670e0a2acc574a9d0096cc29ab2`
