# Memory 0.6.14 Host组合复核

最后更新：2026-09-06。Host固定ec046e84，业务源27dceffe05247415ba9b48a55e865a0faba37629，wheel SHA256 f60e7696af830704399f1e964fbd312ea1fea6e41ee7b9123a8a3dfe303368eb。SELF召回不能携带不同最终受众；协作者语义配对修复不等于开放外部/公开原始历史。业务源d7已获独立限定ACCEPT；Dirac随后对固定2b5e761制品证据限定ACCEPT，无P0/P1；不以本组合测试代替源与制品复核。

隔离安装H0.7.3/M0.6.14/S0.3.13，三个SDK的169/76/121个wheel成员（除RECORD）逐字节核对。新CPython3.12.13环境仅6.3MiB，通用依赖复用主树既有site-packages，三个SDK各自从新环境加载；不是完整依赖求解。旧M0613环境/制品保留。初次setup脚本在安装核对成功后因误写Service模块名而退出1；保留原始失败，随后仅纠正只读验证脚本为simple_harness_service，未重新安装或覆盖证据。

必要组合测试32 PASS、2 subtests PASS / 21.22秒；资源组35457，elapsed22.247秒，峰408560KiB，exit0，无剩余进程或清理异常。涵盖候选身份、公开SDK clock、模型长短期组合及物理出站受控传输检查；不是实际Provider/native、完整401或240质量结果。

工作目录为本Host候选树；命令：
```sh
PY=.local-test-evidence/2026-09-06/primary-m0614/venv/bin/python
"$PY" -B scripts/run_resource_bounded.py --evidence-dir .local-test-evidence/2026-09-06/primary-m0614/tests-r1 --rss-mib 2048 --seconds 180 -- env PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend CLOCK_TEST_ARTIFACT_ROOT=.local-test-evidence/2026-09-06/primary-m0614/clock-stores "$PY" -B -m pytest backend/tests/sdk_adapters/test_sdk_candidate.py backend/tests/memory/test_runtime_clock.py backend/tests/memory/test_model_short_recall.py backend/tests/execution/test_model_short_outbound.py -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp .local-test-evidence/2026-09-06/primary-m0614/tests-r1-db
```

继续待办：Host可信受众配置及原始输入许可、Harness实际receipt消费和continuation、Prospective真实信号链、原矩阵剩余项、240实际质量与当前原生链。未切换用户主树运行环境，未push/tag/发布。

本机ignored证据根：`.local-test-evidence/2026-09-06/primary-m0614/`。

| 文件 | SHA256 |
|---|---|
| installed-identity.json | 181e1c87b4ae606fe1ec9997145451db9aaa22ba28ca9e3082a9ee54ec0ecc41 |
| setup-resource/command.log | 12a583952e4875d86d69057f6b6d3d2eec75f06a792e6e28526102143e9c9a8b |
| setup-resource/resource.json | 44313ede5a4276f481e00d9660f3b2bd7b78ab5e027d7f884f369caf1e33b1ee |
| verify-resource/command.log | 28e49a49e2f55418fbf733e8ee05eccbfdb5cb58d9f5358af388112bc0e5aa4a |
| verify-resource/resource.json | 7cc6a3c4a01f415c7b7dca0521387d0868a067d1419192546f293e8e0c6b85ed |
| tests-r1/command.log | 52c5111e5f06a775dce12cd07f2e38c69fb000906e95012bb1aa276272a72317 |
| tests-r1/resource.json | 9823749d551258e73b28279292e478d22b59ec23ed973b2d09eca70e7fa92443 |
