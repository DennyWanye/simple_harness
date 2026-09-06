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
