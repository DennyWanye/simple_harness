# 图谱无关系节点标签布局

最后更新：2026-09-06。修复此前原生少量无边节点单排挤压问题。产品仅修改MemoryGraphCanvas：无边使用平方网格，有边保留有向breadthfirst；两者布局纳入标签尺寸；标签允许长中文/长词按宽度换行。节点身份、关系数据、权限过滤、选中回调、viewport滚动、缩放/销毁与遗忘ACK路径不变。

## 真实浏览器结果

使用实际WebKit/Canvas及本树React组件，嵌套滚动fixture/7个无边节点，包含长中文标签；同一oracle在改前、只有网格、最终换行三个阶段执行。fixture是明确的合成UI数据，不是实际API或native数据。

| 阶段 | 800宽 | 1000宽 | 结果 |
|---|---|---|---|
| 原breadthfirst | 17对标签相交，字高7.20px | 13对相交，字高8.23px | FAIL |
| 仅网格/标签尺寸 | 0对相交，字高4.95px | 0对相交，字高7.09px | FAIL，不能只消除重叠后宣称可读 |
| 网格+长中文换行 | 0对相交，字高9.53px | 0对相交，字高11.05px | PASS |

检查使用引擎真实renderedBoundingBox（含labels），并实际鼠标选择节点、点击放大/显示全图；两个尺寸各5项全部满足。字高下限8px为本回归明确的最小可读检查，不替代全产品可访问性审查；用户仍可放大和使用文字列表。主代理看过800宽实际截图，节点/多行标签可分辨。

相邻5个前端模块 **18PASS/1SKIP，2.55秒**，TypeScript noEmit exit0。SKIP仍为旧HOST_GRAPH_FIXTURE未配置的真实SDK/API关系夹具，不算通过。仅有边布局保留现有算法并加入label bounds，本次浏览器未覆盖密集关系/边标签，不声称完整dense graph或真实关系生成通过；native exact-build复验仍待后续。

全部串行、有界进程执行。初次浏览器峰值523712KiB，最终403584KiB；前端417088KiB，tsc608256KiB。没有达到1GiB/120秒停止线。WebKit页面/浏览器/Vite均在finally关闭，没有模型/native启动。

## 命令与证据

本地probe.cjs自己启动唯一strictPort18176 Vite、WebKit，并在finally退出；通过本地bounded_pytest.py的 --exec 模式看护。生产改动后用同一probe.cjs执行wrapped标签，输出只保存在本机ignored目录。

前端单worker执行PrimaryMemoryPanel、PrimaryMemoryGraph、MemoryGraphCanvas、PrimaryChatView、PrimaryAuditBinding五个测试模块；随后 `tsc --noEmit -p tsconfig.app.json --incremental false`。不是原生UI验收。

证据根：`.local-test-evidence/2026-09-06/graph-label-layout/`。原日志、截图、測量及测试工具保持ignored，不上传Git。

| 文件 | SHA-256 |
|---|---|
| before.log | 7b744e367b92322c5a004a8c43b677031998b29016d5e0d39ea85e3aa8b7826d |
| before/measurements.json | 6dc0f7bb47ff6ce43539f35fe3049a79cc5fc4a5f7f2ccacd65f7dbc29f62c28 |
| after.log | af4d96a338e94147aa693ecd2484c6268600d9914535a79a5c07b18e484f6e63 |
| after/measurements.json | f1173a2251045bb50c96ad5ee14102def5b1388d32c996b3befa5308df2c0266 |
| wrapped.log | 22c4c6ca45353cb936a77c4b8a0fc6accdd3e50ca2dc76fbcae35f7d3a9ef25a |
| wrapped/measurements.json | 571036df07ab6c33767d8739e5225dd43fc850c81c7df3f23dc53f1b10c104cf |
| wrapped/800.png | c76be671e9838b4b7fceb5e6ee2418966e7ff63685d50d51a45309d788286ba4 |
| wrapped/1000.png | 0eeea0113005848696d71bebc916e6874f19ba019a8eb9302cfcc00e97f6dd9c |
| wrapped-resources.json | a50c36cdb6e369a9038a98d2f4cc70b8952cab6f055dc79ba49798ca342ac317 |
| frontend.log | 5670ca17180f45793417300b89fb76ea17e70d7511bfd9b585fab4be9ec9ea26 |
| frontend-resources.json | 8cdcaa760535a1fdd26a23049525a3fb1babd0957806b2eb6532fc68b561da4f |
| typecheck.log | e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 |
| typecheck-resources.json | 6b0710f7979bb7301776988830154c5a598ac9dee918334073937201dab8d56b |
| probe.cjs | 50aa36d24a6801b050fb9d4b211ef6145e2069c3aaf8cab8adc2dc672b26a089 |
