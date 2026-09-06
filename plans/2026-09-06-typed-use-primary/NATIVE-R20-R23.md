# 原生 r20–r23：独立提醒正文与冷启动

最后更新：2026-09-06。限定结论：r22 新一次性时间提醒的独立正文显示、r23 冷启动保留及后续普通问题无重复提醒通过。原 r18 银杏 ACK 成功但正文缺失仍为历史 FAIL；不将本次结果外推为事件发布来源、全部 A7 路由、完整长旅程或 240 条质量通过。

## 固定身份与实际操作

Host `ff35fb822272f4963abb2d9e2903d7cceb1b5798`，H079/M618/S0313 当前安装组合，真实原生 Tauri 与 gpt-5.5。前后两进程复用原 r14 userdata，没有修改旧 ACK 或手工修补数据库。

正确包 `SimpleHarness Memory Notice ff35fb82p18120.app`，bundle ID `com.dennywanye.simpleharness.notice0906ff35fb82p18120`，可执行文件 SHA-256 `7c19a21868d9627660672c8be5b2f814752c92f3e0372ebe5b2922f3a2d51033`。构建时设置 `DESKPET_BACKEND_PORT=18120`，启动时由 Tauri 管理唯一 backend，未另起 backend/Vite。实际原生运行期间固定源码未变。

- r22：只发送一次“北京时间2026年9月6日23:20提醒我检查松柏测试记录，只提醒这一次”。到期后输入无关“38加19等于多少？”，实际 prospective_ack 权限请求经一次允许，ACK 成功；模型最终只答 57，界面另有独立“提醒”卡片：`提醒用户检查松柏测试记录`。完整 AX 和可见卡片截图同时保存。
- r23：正常退出后的新进程打开同一数据，历史中保留这张提醒卡片及同一 ACK 回执。新的“72减28等于多少？”只答 44，没有新 ACK 请求或新增提醒卡片；截图展示原提醒在新问题之前，而不是再次投递到末尾。
- occurrence key：`abec9b71b479f581e489923abd7717638ddb53d0d797c7db0ac3e05bb439850a`；receipt ID：`5a2baee48d9210cc2c13c5c8388863062d0b79f83df2ebb79ad4c1bb9e53e34a`；receipt hash：`2c2416831e4e549cfd7954dc5bde14b1e9c839d40bdcd08fcc96efe2fde2a1f2`。

这是原生可见显示与历史去重证据，不声称用户已阅读，不改写模型回答，也未撤销/重放旧银杏 ACK。精确公共投影身份及本次操作审计由后续只读审查补充，不用它替代 UI 证据。

## 两次前置失败和构建纠正

r20 的 carrier 提前 exit120，资源管理器按 orphaned_group_after_parent_exit 清理所属组并返回125。最低磁盘543MiB，但具体退出根因未确认，不能断言 OOM 或磁盘导致。其后 CUA 选应用意外自动启动一个没有 carrier 环境的独立进程，显示 backend 路径不存在；该窗口已截图并正常关闭，不把它归因于此前配置正确的 backend。

r21 的 backend 18120 健康，但原生界面未连接。原因是我第一次构建没有注入编译期端口，前端固定为8100；这是构建配置失误，不是模型验收失败。未发送新用户输入，正常退出。随后仅针对端口纠正重新构建一次（前端 typecheck/Vite、Rust 和 app 打包完成），没有重跑已绿功能套件。第一包 SHA `de2d8220f78a142b04ae341a9c3e0a15edf57d110ce00a2a6c449dc522e8c99d` 保留。

界面自动化有瞬时 ScreenCaptureKit 错误和历史滚动位置问题，先读实际 AX 判断是否已发送，未盲目重复消息；最终点击已观察到的末尾文本再 Cmd+End 后，r22/r23 截图均确实展示新提醒。03权限截图停留旧历史，不能声称它显示了权限参数；参数在当时实际 AX 工具响应可见，但单独 AX 文件保存失败，不补造文件。

## 资源与清理

共享锁串行运行；原生8GiB RSS/1800秒，4GiB启动内存余量，未提高预算。两次构建分别130.492/129.423秒、峰1,321,840/1,274,272KiB，均exit0、组清空。磁盘低时只删除已退出构建的可再生 Rust deps/build/incremental 缓存及本 crate中间.a/.rlib，三份清理记录核对当时 app 可执行文件哈希不变；未删除原始测试证据、模型、用户数据或应用包。正确构建清理后可用2746MiB。

|运行|PG|退出/秒|峰组RSS KiB|最低磁盘MiB|结果|
|---|---|---|---|---|---|
|r20|37793|125 / 16.227|1319632|543|carrier异常，清理后remaining=[]|
|r21|38212|0 / 114.369|1317728|743|端口错配，正常退出|
|r22|42213|0 / 605.803|1333584|1697|新提醒独立正文通过|
|r23|45599|0 / 101.791|1410192|1694|冷保留/新问题无重复通过|

所有上述资源回执均remaining=[]、cleanup_error=null；r22/r23无内存、磁盘或时限中止。完成截图后正常退出应用，再将唯一测试槽交给新增控制。

## 本机 ignored 证据索引

- `.local-test-evidence/2026-09-06/native-notice-build/build-18120.py`：`c88cdd0dfe1fd28b0d641f85ac9df635f00f6aba0952da8db018a8fac6bfd143`
- `.local-test-evidence/2026-09-06/native-notice-build/build.py`：`da4f5c19060436f5471fbef891d157b1bc2212ab463fc7f7ba700ddef7909e12`
- `.local-test-evidence/2026-09-06/native-notice-build/cache-cleanup.json`：`af7a37c1a965f3514b96b402a29703a788c495c871ea52e5564910aa426791c8`
- `.local-test-evidence/2026-09-06/native-notice-build/intermediate-cleanup.json`：`1948c72438406f5c2b9ad4d94f923eb9ece0b6467e0388e8d95ca1acf54acee0`
- `.local-test-evidence/2026-09-06/native-notice-build/r1/command.log`：`60e7f6ef8f5d9af5eda3b31ff53b894ba1ece81f6af37ecbea18f3f3080a89ca`
- `.local-test-evidence/2026-09-06/native-notice-build/r1/resource.json`：`473378bc75d5c8e718525f0cd37acc448890546417b47579cd23a0e14df58782`
- `.local-test-evidence/2026-09-06/native-notice-build/r2-cache-cleanup.json`：`e559b0c093f312d867c7411fdae89c6ac0ed0123aa2b50618e222773a991e81a`
- `.local-test-evidence/2026-09-06/native-notice-build/r2-port18120/command.log`：`63fb36f9aa87d2b88807b69190cc876494b23309e9d0db8673b88c4fa0a26aed`
- `.local-test-evidence/2026-09-06/native-notice-build/r2-port18120/resource.json`：`76e49c696cacb33fa60f60bf3a0f2d9071511bf2f318a3f905f54228674bd67b`
- `.local-test-evidence/2026-09-06/native-notice-build/tauri-18120.json`：`e3147bf69c5f7980ed5f324fde10a554ab0e03e95b549318327ab8c1dfd988c4`
- `.local-test-evidence/2026-09-06/native-notice-build/tauri.json`：`c6f9addf1ca49de66b90418be466385ddff003b3fc28dbe60aa0e19587b5e203`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-q8x67q9g/launch.json`：`b841f9345acfc679648f94538ab42030a4b6e80734cefc5ff434bd32063d41ab`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-q8x67q9g/native.log`：`3a7f63d4d77aa5983fc3d8f0dd7ca0c71877400c5f4de1134bba56e2134c6cc0`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-q8x67q9g/port-mismatch.ax.txt`：`dfe4ae1a1c9403126a294ef81f5f5264b6d64673eb94992a2e7b2ed42dec86a1`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-q8x67q9g/port-mismatch.png`：`67f7a4ea94bb0b2b3f9539c9193b8a033af86f737860f8df9843336db2e294d1`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/01-ready-old-acks.ax.txt`：`44f2c72ea3c2eb63bed30eec23eaa0b264728107c62359a19512b19411309428`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/01-ready-old-acks.png`：`55780d3890a4434a753269da65e36575da44a17171adf49bde57e83f75461a37`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/02-new-reminder-request.ax.txt`：`c9a7414048278bc09628475090fae23cbc0f7a24179b213e5fdfcd54aab9f07d`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/03-new-ack-request.png`：`432b82cebfc51469903de13e3560fb0149aa5851049e7aa10ac5a2a49fca88b4`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/04-after-ack.ax.txt`：`2dfb4afbfe9b6238cfc62438fa8e710fe38ef7e8662d9ddc5345e434378b8058`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/04-after-ack.png`：`277496008e774876e528569f519db4e7c5e81e89cc88d84fd9d3419856b43975`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/launch.json`：`71331c1a94ccce67d53d66766e1e7a8c4c2b090b8c199dfd18ca54bb197fe347`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-u0lhfbik/native.log`：`958958fa5544859251f435932a1aa85dbb012b8712a3f7e9c9afcfe25bb74464`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-x1d04xu7/launch.json`：`5b59c817918a776cd1232e24bc09f5d6ca9670ff921611e4cfc9129c39fa1fd2`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-x1d04xu7/native.log`：`f2a7efe7386f691454576b8765be21bd3905ecbc80c173e75ce4e4f9a5941625`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-x1d04xu7/ui-after-carrier-exit.ax.txt`：`cd4895a5d8a15cbe139aa4ad4e1c69a95223757c7c34495927d04b0c1cdd1e32`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-x1d04xu7/ui-after-carrier-exit.png`：`de2b965ef48349011e60503456e59e0f72f8a52d7461af0ed555c439e2c4b5d9`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-xv3yn803/01-cold-history.ax.txt`：`29e1235c4bb6f7b3e926db2928a872bd73adec47bba19f17966e75d434f481f0`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-xv3yn803/01-cold-history.png`：`277496008e774876e528569f519db4e7c5e81e89cc88d84fd9d3419856b43975`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-xv3yn803/02-after-independent.ax.txt`：`15024df082f490fd93ccbae665baf14e2012cec4091bf9ea53400a3d7ebaba67`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-xv3yn803/02-after-independent.png`：`84f170d0e92095118b482ba861cc4890b70f481025cf94b8795eac1a698a57b3`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-xv3yn803/launch.json`：`01fd45156729a38c89df2c3952814cbb75fd01ab23d617408c493baf638e78b6`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-xv3yn803/native.log`：`e12db8b2750013d7b65e4b562c487bcc9cd67f2abfcda6981c8d0f850938de6f`
- `.local-test-evidence/2026-09-06/native079618/r20-notice/resource.json`：`77aef938247f72d30c08683137d814a71bab09ecd23f2651ee9bceb9146da4d4`
- `.local-test-evidence/2026-09-06/native079618/r21-notice/resource.json`：`25e4719d8d363a7d8e3c5e1aae459fdf2c84cd3def8319cce4ff2e653f3e7499`
- `.local-test-evidence/2026-09-06/native079618/r22-notice/resource.json`：`eafd05fd7a4e1c555ace08bb874a5c85189ecd79b0552074f6d6d73d96b22073`
- `.local-test-evidence/2026-09-06/native079618/r23-cold-notice/resource.json`：`b002276edc5e1bcbe2447109ba1006dd391055b7a0b2a9875fb5fc2a485016ab`
