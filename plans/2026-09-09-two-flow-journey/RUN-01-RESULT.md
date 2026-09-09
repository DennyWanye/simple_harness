# 两轮完整流程（含重启）旅程 run1（2026-09-09 20:10–22:00，Host 8d8b61fb+ 源码 + bundle 3f30a17b，Memory 0.6.38，flash 关闭 thinking，`--memory-probe-light`）

证据：`.local-test-evidence/2026-09-09/twoflow-run1/primary-ui-lc1dpujx`（flow1 + 全部 DB）与 `primary-ui-o7npp9jv`（重启后的第二段 native.log / launch.json）。

- **flow1（T1–T11）全部 COMPLETED**（T4 读 fixture 走读门绑定提案；T8 228 s 最长）；重启前后端 RSS 3.6 GB。
- **重启**：按同一 `userdata` 重启，后端 `product_sdk_runtime_ready` / `reconcile.recovered` / 31 次 `memory.evidence_ingestion_replayed` / `companion_projection_history_closed_identity_unready`；WebView 显示「已连接」但主对话停在「**等待主对话就绪**」超过 3 分钟，flow2 的 T12/T13 两次发送均无新 Run 头。→ **事件 AK**（重启后主对话就绪信号/闭合历史投影身份未就绪）已派子代理；旅程中止。
- 验证器判定见 `RUN-01-twoflow-verify.json`（flow2 未跑，重启项按此判 FAIL/INCONCLUSIVE）。
