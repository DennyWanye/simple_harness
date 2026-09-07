# 短期索引与 Service 0.3.13 组合验证

最后更新：2026-09-06。主组合基线deb10b0e已合入独审426db3bb及829726d6，Service固定源74a622572602bf3b6973f5d8a55c09bf62ff08ba及双次一致wheel通过独立复核。本次仅更新主组合Service pin、vendor与锁文件；未切换用户主checkout、发布或运行原生应用。

## 结果和范围

同一专用安装环境H0.7.3/M0.6.12/S0.3.13，六个受影响模块 **62 passed /25.45秒**。看护墙钟26.17秒，峰值296800KiB（约290MiB），PID16167 exit0，未触发2GiB/180秒限额。禁止本地模型模块导入；测试进程已结束。

覆盖完整两消息组自动索引、低序号迟到与轮转、公开ACK后取消/重开重放、真实durable analysis邻接、选中来源隔离、实际签名/ws/control审计往返、SDK身份及组合构造。保留tool/多消息组拒绝。此组没有重新运行全部terminal audit目录，也没有真实Provider或native验收；不把62与既往计数相加为进度。

安装后逐字节核对所有wheel成员（排除安装器重写的RECORD）：Harness169、Memory75、Service121，一致；各direct_url都指向本组合vendor。此计数包括元数据，区别于旧仅包文件164/72/112。Service由本树固定wheel经 `uv pip install --offline --no-deps --python .local-test-evidence/2026-09-05/primary-candidate/venv/bin/python backend/vendor/simple_harness_service_sdk-0.3.13-py3-none-any.whl` 安装。

`uv lock --offline --project backend` 成功，只更新Service身份，保留产品已有H073 override。SDK声明的H062/可选M052依赖不变；这是产品显式组合，不代表无override的SDK普通解析兼容。69个协议authority成员与旧0312字节相同；Host小manifest明确local-candidate-not-release，未复制旧release下载/CI/平台锁声明。

Service新增生产工具操作观察（请求、发送attempt、UNKNOWN、真实ACK、后继响应），但无Host持久sink或可信Run绑定，所有snapshot仍durable=False。不能据此声称每次Agent操作已永久记录/审计。Memory SDK全subject projection成本、完整多消息生产、新模型short请求、两轮240质量及401矩阵仍待完成。

## 可重复命令及本地证据

工作目录为本组合，PYTHONPATH=backend，Python为 `.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python`。本地bounded_pytest.py实际参数：

```text
backend/tests/memory/test_short_index_worker.py backend/tests/memory/test_memory_display_producers.py backend/tests/memory/test_selected_short_runtime.py backend/tests/memory/test_primary_control_binding.py backend/tests/sdk_adapters/test_sdk_candidate.py backend/tests/sdk_adapters/test_composition.py -q -p no:cacheprovider --basetemp=.local-test-evidence/2026-09-06/service-0313-combined/tmp
```

原始证据仅在 `.local-test-evidence/2026-09-06/service-0313-combined/`。

| 文件 | SHA-256 |
|---|---|
| identity-bounded.log | 541467671799823450faebdc7ab62a665ca885d039dab97e37e27e5df2f9907e |
| identity-bounded-resource.json | 03632694e8d18bfb71b251a9f22419c0bca4690a4db54c3e6800a78ec4dd1ee5 |
| installed-identity.json | 093c526f547f511621930048ab05ed82dbd0e771c3747d4dafefcb5bb956a91e |
