# 用户发现白屏：源码测试窗口恢复

最后更新：2026-09-14 19:59 CST。

用户报告后实际检查到 SimpleHarness Source UI 全白，AX 只有空 HTML content。独立窗口进程98593的父进程为1，启动于19:28:06；原受管Tauri进程96459也在19:28:06退出，原进程组96422确实已清理。此时15173 Vite与18140后端均无监听。原“0残留”receipt只证明受管进程组，未覆盖再次打开的独立窗口；此前收尾没有检查到这个问题。

源码测试carrier依赖开发服务器，不能把它单独打开当作完整源码启动。CUA的getApp/getAXState会在应用未运行时唤醒它；结束测试后不能用这类调用确认已退出，应使用进程和端口清单。该行为能解释本次同秒出现的独立窗口，现有记录没有单独保存其LaunchServices调用栈。

恢复：先保存白屏截图和AX，再正常退出失去服务的窗口；使用原source-snapshot-v48和原数据目录，通过launch_source_orchestrator.py --resume完整启动唯一Vite、Tauri及其管理的后端。实际UI经历“未连接”→“已连接”，随后点击任务编排、已有COMPLETED Mission、REPORT.md，确认报告与完整hash正常显示。没有把只恢复外壳当作完成。

恢复后补充核对：12张选定持久表逐行相同，5产物路径重定位关系及字节hash保持；3张Provider表共15条旧记录不变，0新增模型调用。源码未修改。本次证明恢复后的实际导航、数据读取和报告显示，不扩展为新的AppWorld原生任务或长期稳定性验收。

当前UI有意保持运行：launcher7073、Vite7097、Tauri7100、backend7121；15173/18140均监听。这些是提供给用户继续查看的活跃进程，不应报告成“所有测试进程已停止”。后续结束该UI时，先正常退出Tauri，等待其launcher清理Vite与backend，再检查对应PID/端口，不再读已退出应用的窗口。

本机ignored证据：`.local-test-evidence/2026-09-14/dgx-local-connect/native-white-v48/`，包括before.png、before.ax.txt、restored-list.png、restored-mission.ax.txt、restored-report.png、restored-report.ax.txt、diagnosis.json和recovery.json。后者SHA-256为`4190ceea162777973336e15f35f9115318c8cf7dc2f80fe228af2a1ccead115c`，内部索引其余证据hash。原v48冷读receipt保留，不改写历史；本条修正其收尾覆盖范围。
