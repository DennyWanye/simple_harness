# 原生授权状态补读修复

更新：2026-09-07。r24 真实context_route已allowed/succeeded，但原生仍显示旧等待授权。修复保留原确切Run、generation、SDK和nonce校验，不自动批准。

手动刷新增加显式授权补读触发；同Run实际tool_result/final/error触发原exact读取，读取合并仍有界；断线清卡时同时标记授权状态未确认。SDK waiting但pending为空明确显示暂无可操作的请求，不能由waiting直接推断等待授权。

注意原controller.refreshLatest会撤回state，可能导致RunPanel卸载/重挂并间接读取授权；不能断言旧代码手刷绝不会读。本次显式触发同时覆盖保持组件的React批处理分支。未定位r24具体撤卡事件或allowed输入方式，保留原失败。

## 新增实际检查

使用真实PrimaryChatView、PrimaryRunPanel、runChannel、permission hook与请求关联器，只有传输服务响应为确定性模拟。不是原生或Provider验收。

- r1：2PASS/1FAIL，112ms测试，PG57701 exit1/1.547s/423728KiB/remaining[]。手刷读取已settled同Run及工具进展/空pending/断线控制通过。负控错误要求重挂后的旧卡保留；实际跨Run响应已拒并显示未确认。
- r2：只该负控1PASS、另2不执行，68ms测试，PG57786 exit0/1.097s/392352KiB/remaining[]/cleanupnull。按真实state撤回行为核查无旧卡、无允许态、无批准请求及错误可见。产品未因负控失败放宽。三个唯一新增控制分批通过，不重复原绿。

命令：`node node_modules/vitest/vitest.mjs run src/views/PrimaryDecisionRefresh.test.tsx --maxWorkers=1`，第二批加 `-t 'manual refresh does not accept'`。均由默认共享锁run_resource_bounded.py包裹，2048MiB/180s；无资源拦截。新构建及真实原生复验待做，不能记整体完成。

## ignored证据索引

- `.local-test-evidence/2026-09-07/primary-decision-refresh/r1/command.log` SHA256 `943deb651c35a0a9fd54331821917e823f2a98b4bbcdb2e5346e13e360f498e2`
- `.local-test-evidence/2026-09-07/primary-decision-refresh/r1/resource.json` SHA256 `382244402cfd294d7c240a3499b3ac9d463723b894f177a8ee56318fa5334932`
- `.local-test-evidence/2026-09-07/primary-decision-refresh/r2/command.log` SHA256 `479fd967e83c9ee972847ddfd27744bb88b2cbcca24bd23356ab5e15817e0ad9`
- `.local-test-evidence/2026-09-07/primary-decision-refresh/r2/resource.json` SHA256 `91257f7152bdf77ee1c4cd2c5ccf941fffb4f48b582078967f0efa7c0e8e83de`

## 独审与新构建

Dirac对产品3文件及真实View组合3unique结果限定ACCEPT。源UI `2adf9088`，合入旧root后被构建Host `0e146792`。新原生构建包含tsc/Vite、Rust和app打包，一次成功：PG58136 exit0，134.003s，峰1264128KiB，remaining[]，cleanupnull，最低磁盘3172MiB；固定编译端口18120。实际原生交互尚未复验。

App为 `SimpleHarness Memory Verify 2adf9088p18120.app`，bundle ID `com.dennywanye.simpleharness.verify09072adf9088p18120`；可执行文件SHA256 `e6bb364f8c1243207830b10de570b68ac533e5c974c29623f34ec354305a0afe`。保留原包及所有旧失败，caffeinate PID56392跨轮持续。

- `.local-test-evidence/2026-09-07/native-decision-build/build.py` SHA256 `0ced1f9fc7783f4bddf8d708e0453d757f9e35caf6a747838d482b4555f88d52`
- `.local-test-evidence/2026-09-07/native-decision-build/tauri.json` SHA256 `c444ed973735a8a1858d0a1e30ae90e55ddc6144d5316af89e2e473fb71cc553`
- `.local-test-evidence/2026-09-07/native-decision-build/r1/command.log` SHA256 `648fbf4b82161b145ae5da9953c1804d2fc8f3139ab16b3f8517fea772e9f1a3`
- `.local-test-evidence/2026-09-07/native-decision-build/r1/resource.json` SHA256 `802f25cb5acde744e75b4c8c09c8e41306cdb388b390b7c214be0f649b3b183e`
