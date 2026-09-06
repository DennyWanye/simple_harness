# H077 / M617 候选接入

最后更新：2026-09-06。Host 已合入经独立限定复审的授权过期终局及冷启动修复 8cec2353。当前候选默认固定 H077/M617/S0313；用户主 checkout 不变。

H077 wheel `60f7fb164f65ca98a1aa54e4fc75cd7abbc08e409a6a10fe397106d47148c5a3`，固定 source `c29af66902dd0b418ab12c1c8f871182280f77bd`。M617 wheel `e119cdcc29cd8d3566848e80a1e1c3ebb897715d666b7311643af1b514de869c`，固定 source `32b9b9410cdf0f05ad205a18b42a5fc3ade55211`。原制品已分别复审，本次只从精确制品复制入 vendor、更新 SSOT/pyproject/uv.lock，未重建或扫描旧包全成员。

从主 vendor 离线 no-deps 安装两包到 `.local-test-evidence/2026-09-06/primary-077617/installed`，复用原解释器与通用依赖，没有新建完整环境。执行两项真实 installed candidate 校验及受影响 Memory lock 校验：3 PASS / 0.10s。原 Host cold 场景 H077/M616 和公开升级 H076/M617 证据分别复用；不写成当前 H077/M617 的整套功能测试。

安装验证 PG75007 exit0/remaining=[]/cleanup_error=null，耗时1.304秒，峰145872KiB、最低磁盘4769MiB。尚无本组合原生/真实模型结论，旧原生数据尚未恢复或升级。完整提醒调度及原程序不以本次身份检查宣称完成。

命令：原资源 runner → 原 primary-m0615 Python `-I -B` → `.local-test-evidence/2026-09-06/primary-077617/install.py <install-r1>`；脚本保留本机。历史失败证据和旧 installed 目录保留。

| 本机证据 | SHA-256 |
|---|---|
| .local-test-evidence/2026-09-06/primary-077617/install-r1/command.log | 960e1c4487a5c4ee3b0994c39a8512400ea133ad83512c9656ead6a46c5f02d5 |
| .local-test-evidence/2026-09-06/primary-077617/install-r1/resource.json | a2a286e41694a6d944bdf7660421d43dde5592e41266fa721c8faa5c808f3f30 |
| .local-test-evidence/2026-09-06/primary-077617/install-r1/identity.json | 1fc3dbaeb6331bfe2ef84e9f771bc64e326501587c47ef6526622a8a6956b8cb |
