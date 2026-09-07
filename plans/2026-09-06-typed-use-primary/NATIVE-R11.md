# 原生r11：预加载生效，首次编码仍超时

最后更新：2026-09-06。Host `464b86ee26c86aa261b6d4090b49b1e6922e5d4e`，H077/M617，沿用原18ec7194 bundle与userdata。仅新进程第一条查询，没有试探查询或失败后重试。

**结果FAIL，边界已缩小到首次编码。** 10:06:49.272日志已确认 `p4_embedder_ready / wemm`，早于首次Run。真实UI发送short=true/types=[]/无糖茉莉茶并核对精确授权，Host `2cced912-b041-5f7d-95c9-2e5beae0c61c`，SDK `product-sdk-aa2d6e533de537b49e499b4f89ef540bccab95a048a871dfb8b783525b2acb87`。工具 `call_dhaL3YhIhm5NlJpsRi4B6rhy` 于10:08:46.151授权、10:08:47.231报context_route_recall_timeout；本次首次encode进度耗时1.44秒。模型最终明确答“查询失败”，UI返回空闲。

预加载接口修复已真实起效，但load完成不等于首encode已预热。继续在同实例、同编码队列补一次有界合成输入预热；尚未实施或通过新原生验收，1s预算保持。r10暖态成功保留，不重复测试该绿项，也不把本失败说成无记忆。

现场滚动到本次错误与最终回答后保存截图/AX，再正常CmdQ；PG93935/native93940，232.904秒，峰1363600KiB，runner exit0/remaining[]/cleanup_error=null。运行最低磁盘390MiB，未越256MiB停止线；退出后磁盘仍391MiB，不足下一批默认1024MiB准入。

随后资源维护：`uv cache prune`报告无unused项。确认无cargo/rustc/native进程后，仅删除主树target/debug与deps内可重建的.o/.rlib/.rmeta/.a链接对象81214个；物理可用从407707648到4457914368 bytes（释放约3.77GiB）。应用/动态库/模型/全部证据和userdata保留，实际native SHA前后一致。没有重建SDK或应用，后续必要Rust构建会重新生成这些缓存。

| 本机证据（相对候选根） | SHA-256 |
|---|---|
| .local-test-evidence/2026-09-06/native077617/primary-ui-dnpdvuk9/launch.json | 25fb27b41ba648a60641a3466552c1e586e1aae957dfd69143038a12bfdfce17 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-dnpdvuk9/native.log | ef16434093476ecbf02e4a16fd11a094a93504e48f0b4d73455d0a3a0c6f85d3 |
| .local-test-evidence/2026-09-06/native077617/primary-ui-dnpdvuk9/first-query-timeout.ax.txt | 98aec8cc03581689a3ac062308f70ca2891744191c2c61f92c5e8506d0980dcd |
| .local-test-evidence/2026-09-06/native077617/primary-ui-dnpdvuk9/first-query-timeout.png | 8b751a78594da0c0b47eacb21885b0f5e56d2a72636333466f597f353b92c85e |
| .local-test-evidence/2026-09-06/native077617/launch-r4/resource.json | 7ac49db442eca3b6c3e789673ba7c4d11a416bd357570541668ad6aeb8475f53 |
| .local-test-evidence/2026-09-06/resource-cache-cleanup-r11/cleanup.json | 7fae8d8224b96328b5d99abdd89981100af9bb6f282e07b8bdfc8a95b0f89903 |
