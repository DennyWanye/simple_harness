# Manual / Procedure 当前原生界面构建

2026-09-07，固定源码e1e714d2（UI包含Manual授权/重连恢复，原有记忆图谱与授权刷新）。本次一次完整npm build（TypeScript/Vite）+Rust/Tauri app成功，尚未启动原生验收。

构建仅单Cargo job、offline、debug info0/incremental0；使用原共享target，设置编译端口18120，生成独立名称和bundle ID，不替换用户应用。原source未边构建边改。后续SDK/Host后端继任以实际启动source/installed target另记；仅backend变更不需要重建相同UI，不能把本构建当新SDK组合已验。

- Bundle：`/Users/denny/projects/simple_harness/tauri-app/src-tauri/target/debug/bundle/macos/SimpleHarness Memory Verify e1e714d2p18120.app`
- Identifier：`com.dennywanye.simpleharness.verify0907e1e714d2p18120`
- Binary SHA-256：`815bc700c82e8f0ced75677245b163b6892034445bc864281a3046b45e880f8a`
- 资源：默认共享锁，4GiB/600s，PG72450 exit0/132.283s，peak1277184KiB，minDisk3684MiB，remaining[]、cleanupnull、stop_reason=null；没有触发限额。
- 构建进程已清空；保持旧r25应用与原证据。PID56392 caffeinate仍覆盖整个测试阶段。

## 下一原生验证边界

先完成SDK0.7.10 nullable后继与主安装组合，显式复验原失败召回链，再用本UI构建启动同一实际候选。Procedure步骤应通过真实界面记录、公开发现、授权与实际文件读写核验；旧r24/r25失败不改写。三独立Scope的qualification另核真实环境fingerprint，不将不同目录的无关运行自动相加。Manual UI服务/组件11控不代替真实点击，App冷启动自动发现已bound引用仍不冒称已完成。

| 本机原始证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/native-manual-build/build.py` | `e80339dffcedf9ddb3d1ced6f2a9d2789106b162efcedf9f19000f9ffe53f49a` |
| `.local-test-evidence/2026-09-07/native-manual-build/tauri.json` | `abe83bc6838c41016427dc8e3bcc0759d918a554dd051dfa7a220a52e781a29a` |
| `.local-test-evidence/2026-09-07/native-manual-build/r1/command.log` | `3c306894c84b3d769a8392f3091b2a25fdb5310d9dc1cf2f834d7add64848c32` |
| `.local-test-evidence/2026-09-07/native-manual-build/r1/resource.json` | `204d772c51ef0f4398219a761beceef8501a436b1ca6a4f3c6152f94a5cc787f` |
| `.local-test-evidence/2026-09-07/native-manual-build/artifact.json` | `d2718ede29e70a24fe91795495af9da36a587a5ebdb07c2267db8371f11089c8` |
