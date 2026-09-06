# 测试磁盘准入与低空间停止

最后更新2026-09-06。实际磁盘仅439MiB触发本次修复；同一默认测试OS锁内官方npm cache clean仅清下载缓存，实测释放3415MiB，3853MiB可用。installed依赖、uv缓存、源码、用户应用与原始证据保留。

原145入口未检查磁盘。新增三个实际子进程反例先红：439MiB仍启动、磁盘probe异常仍启动、运行中低至128MiB仍等待deadline。磁盘数值来自受控shutil.disk_usage，未真的填满用户磁盘；进程启动/终止/锁与证据是实际操作。修复后全部13项通过6.44秒（含原10项退出/信号/孤儿/采样错误邻居），独立审查待续。

默认同一锁内启动前检查cwd和证据目标卷（不存在目录取最近现存祖先）的可用空间：小于1024MiB或probe OSError时返回125，不创建证据目录、不启动child，stdout保留原因。运行期间每次采样最小空间低于或等于256MiB时disk_limit，清理自有进程组并保留receipt。新增receipt字段disk_scope/min_disk_free_mib/disk_admission_mib/disk_stop_mib。忙锁仍75、无目录，旧RSS/时间默认及信号归属不变。

边界：仅cwd和证据所在卷；其他卷写入未覆盖。0.2秒轮询并非磁盘硬配额，外部高速写入可能在采样间耗尽空间，不能保证满盘下receipt写入成功。不会自动删除证据/代码或终止用户应用，不代表实际模型内存已经回收。

本次统一运行命令：现有Host primary-m0614/venv/bin/python -B运行主145 scripts/run_resource_bounded.py，默认锁、2GiB/180秒；child pytest scripts/tests/test_run_resource_bounded.py -q -p no:cacheprovider，禁插件autoload。r2-red仅-k disk，r3-green全13项；独立ignored basetemp。r1忙锁未创建目录/child。r2 PG42270/exit1峰38528KiB耗时1.139秒；r3 PG42343/exit0峰60896KiB耗时6.72秒，均remaining空/cleanup_error空。

| 本机ignored证据 | SHA256 |
|---|---|
| .local-test-evidence/2026-09-06/disk-resource/r2-red/command.log | 8c79b671d10446069f45b738083fadbe3adf31e19621878d770a5d4be86bae68 |
| .local-test-evidence/2026-09-06/disk-resource/r2-red/resource.json | 55540fbfeb8cd41520dd1429c6183bcb91e07abe83f5120dce40df8335d13540 |
| .local-test-evidence/2026-09-06/disk-resource/r3-green/command.log | 0afc78b4d186e94cbbc4eb95cba1119aab67c7f500f499a7dccfd74d6ee8744f |
| .local-test-evidence/2026-09-06/disk-resource/r3-green/resource.json | 3935740f65e8b896c832cdb9e5685b968320ecd8e5371c202a3206287e22fe9e |
