# Slice C black-box oracle — production cutover

> 本文件补充并不替换已冻结的 `testcase/2026-08-13-simple-harness-sdk/{manual-test,
> black-box-fixtures,full-surface-smoke,value-smoke}.md`。

| ID | 输入类 | 方式 | PASS oracle |
|---|---|---|---|
| SDK-C1 | text/voice/background ingress | automation + desktop | 三入口只取同一SdkRuntimeReady；same request同root；无fallback/第二owner |
| SDK-C1U | voice/background真实桌面入口 | real desktop | 真点击麦克风与Companion可见入口各创建一个SDK root并完成唯一terminal/delivery；generation与chat相同 |
| SDK-C2 | terminal/session/artifact delivery | temporal fault | live/backfill竞争、无WS、sink crash、restart后Session assistant/artifact各恰好一次 |
| SDK-C3 | authority absence/feature parity | static + runtime | production零旧generic/private/matcher/router；DeepResearch/PPT/companion/voice功能不减 |
| SDK-C4 | reset/frozen backend | script + desktop | reset只命中`<user-data>/data/workflow.db`旧开发执行库及WAL/SHM；新SDK/product-state DB拒绝删除；frozen inventory/version/SHA/schema正确；supervisor restart可恢复 |
| SDK-C5 | SDK-S6/S7 fault matrix | automation | malformed/乱序/unknown/各crash window均不串单、不盲重放、最终可对账 |
| SDK-C6 | SDK-S1..S5 value/UI matrix | real desktop | frozen manual matrix全部required PASS；S3/S4/S5各2 roots；S3长上下文+reject/cancel |
| SDK-C7 | release identity + three-platform conformance | script + remote CI | candidate/vendor/tag commit/BUILD_INFO/SHA/SBOM一致；远端下载bytes与vendor相同；同一wheel SHA在macOS ARM64、Windows x64、Linux ARM64 Python3.11完成import、schema reopen与provider/tool/runtime/workflow四suite |

真实reset和远端publish分别要求用户单独批准；未批准时对应case保持NOT_RUN/PENDING，不能通过其他case
替代。UI证据必须是真窗口坐标点击/输入；WS直注、pytest、backend import与脚本回放不能替代。
