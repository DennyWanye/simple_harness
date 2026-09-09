# HM-TO-A6 短旅程 ①（第 12 次·T1–T13，2026-09-09 15:45，Host e6696fd6+ 源码 + bundle 3f30a17b）— T6 后中止

目的：验证 F-Z1b（读越界 → S4 绑定提案）让 A6-2 可达。结果：T1–T5 正常；**T6 三次 read_file 均被 `workspace_binding_invocation_origin_stale` 拒绝、未产生提案**（`binding_proposal=-`）——F-Z1b 链路在 `runtime_binding_authority.py:494` 的调用来源检查上失败（continue_active 的 Run 拿 T4 create_new 那次绑定的 origin 去校验）。→ **F-Z1c** 已派子代理；旅程中止（后续轮次都依赖读）。
