# WeMM 按需加载：主组合接入及浏览器验证

最后更新：2026-09-06。固定b70ccda5a55285d327d5a9305aa5084f0648f565经独立只读复核ACCEPT，原19b851f2审结保留为历史；主组合以fast-forward接入，无产品合并改写。SDK仍为已核H073/M0612/S0313；用户主checkout和已安装原生应用未切换。

## 行为和验证

WeMM构造及kind/dim/lineage/status不再import或加载模型，首次实际embed才创建唯一共享load任务。编码先异步排队，再进入物理线程；取消等待者不重复加载、不提交已取消的排队编码，也不把仍在运行的线程当成停止。失败完成任务按identity丢弃实例持有的引用，避免异常traceback长期保留被拒模型；不清其他waiter异常、不自动重试。设置页显示实际模型及cold/loading/ready/failed，刷新只读状态。

叶子实际线程/fake模型、公开Memory0612空库builder、IPC、React及类型检查已通过，原构造导入红和失败模型引用保留红均留下；必要窄组修复通过，见[精确命令与分组](RESULTS.md)。主已读取关键差异及独审结论，无无改动的后端整组重复运行。

主在实际WebKit引擎加载当前React组件，以明确的模拟状态通道覆盖cold/loading/ready/failed四状态；每个状态实际点击刷新，均仅新增一个embedder_status请求，状态正确、模型名正确、pageerror为零。检查800×500截图，按需加载状态文字与路径可辨。该fixture不含真实模型或真实WS后端，不是完整原生Settings验收。

浏览器组PID17044 exit0，峰值442944KiB（约433MiB），未触发1GiB/120秒看护上限；page/browser/Vite均在finally退出。前端只有组件验证fixture，不是交付给用户的HTML审阅文档。

## 内存边界与后续执行

真实模型/权重未加载，没有实测6GB节省或GPU/allocator回收。旧库缺向量或lineage变化仍可能在Memory0612 build_production.ensure_embeddings期间加载；首次使用后的权重仍常驻，没有自动unload/跨进程共享/线程强制终止能力。失败weakref回收只证明Python对象引用释放，不代替真实内存测量。模型选择、2048/L2/lineage、本地资源校验与原deadline保持。

后续本机测试继续串行：只保留正在使用的一个测试应用；启动前检查内存与已有进程；所有自有测试进程设RSS/时间上限，结束清理子进程，不启动多个模型后端或重复Vite。现有看护属于本任务运行器，不声称有系统级自动清理守护程序。原始证据保留本地ignored，不用删除数据/证据来清内存。

## 本地证据

根目录 `.local-test-evidence/2026-09-06/wemm-combined/`；通过bounded_command.py运行 `node probe.cjs`，其配置为1GiB/120秒。原始文件不提交。

| 文件 | SHA-256 |
|---|---|
| webkit.log | 01968ebf28574feaab51f95176f10370f86f69e56eb34f63b868986f81362573 |
| webkit-resources.json | 2873a787902ce745181d6c69e44d940803e14e1fa3b945750653386e6a94f971 |
| ui-observations.json | 493741446e0c6c2366f7904632ec6e969a5572001331c0219578d6ed8b2ce712 |
| cold.png | 48626b5272be6e0b938eed389955a30451e847c4cc27dfde5ad677536955e5d6 |
| loading.png | 6f0da33a8a94b7fa13111c8101eaa482dbf02eb55c5ba34bda5722f8c76ed4f2 |
| ready.png | 9d7942f5d0fdba8055df7da5a7b1218b5af7cc5bba2e54fe25cf01608f951e53 |
| failed.png | 7f1af241f9dd5b84786ed1c13aeb4c087f83804ee0b70567c849b39617d66e8e |
