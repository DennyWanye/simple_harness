# 源码 v37 修复与验收边界

最后更新：2026-09-14 CST。

诊断能力探测先读取可选 store，重建失败的半初始化对象不再因缺少 store 属性中断关闭和重试。相关生命周期及诊断20 PASS，17.44秒（runner17.96秒）。前端详细产物完整hash、路径及审批反馈自动换行；列表保持原短hash。MissionsView/useMissionsFeed 87 PASS（Vitest1.44秒；命令1.995秒），正式 tsc -b --noEmit PASS（2.733秒），总4.729秒。最新源码原生换行与待复核冷恢复仍待验。

Host前一轮累计为303 PASS/5 FAIL/1 deselected、249.57秒；2项重建失败已由上述20项复验，另3项依赖wheel RECORD的冻结包资源检查不适用于当前editable源码环境，保留原FAIL，按用户要求暂缓打包验收，不改弱原测试。SDK前一轮1970 PASS/9 opt-in SKIP、643.26秒是输出扩展修复前基线。SDK后继2957ed7对明确length且带有效usage的工具解析失败执行有界输出扩展；205 PASS/5 SKIP及固定tokenizer28 PASS补验已独立记录。不能由这些分组推导全Phase3通过。

原始证据保持在本地 ignored 目录：Host `.local-test-evidence/2026-09-13/p33-g/g-ui-long-wrap-v37.{log,json}`；SDK `.local-test-evidence/2026-09-12/p33-g/g-host-diagnostics-recovery-v37.{log,json}` 及对应 cumulative-v36。P34严格真实对照仍FAIL；正常旧PLANNING待派发恢复点和最新源码累计审计仍OPEN。没有打包、发布或推送。
