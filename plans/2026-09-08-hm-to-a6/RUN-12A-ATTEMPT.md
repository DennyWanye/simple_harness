# HM-TO-A6 短旅程 ①（第 12 次·T1–T13，2026-09-09 15:45，Host e6696fd6+ 源码 + bundle 3f30a17b）— T6 后中止

目的：验证 F-Z1b（读越界 → S4 绑定提案）让 A6-2 可达。结果：T1–T5 正常；**T6 三次 read_file 均被 `workspace_binding_invocation_origin_stale` 拒绝、未产生提案**（`binding_proposal=-`）——F-Z1b 链路在 `runtime_binding_authority.py:494` 的调用来源检查上失败（continue_active 的 Run 拿 T4 create_new 那次绑定的 origin 去校验）。→ **F-Z1c** 已派子代理；旅程中止（后续轮次都依赖读）。

## 短旅程 ① 第二次（run12b，F-Z1c 合入后，16:25）— T6 首次走通读路径

T6 调用链：`context_route` → `tool_search`/`tool_describe`/`tool_activate read_file` → `context_route`（重路由）→ **`read_file` 成功（17.9 KB 结果被分页）→ `context_page_in` ×4 → 逐页读完** → 回答「标题 秋分资料整理 · 主清单 A，407 行」。读门日志：1 次 `read_workspace_binding_revised`（提案 `185fae71…`，Auto 策略自动授予），绑定根 1→2。**A6-2 的结构性前置（F-Z1 / F-Z1b / F-Z1c）在原生首次全部走通。**

## run12b 结果（T1–T13，16:50）

T1–T12 全部 COMPLETED（T6/T8 读文件 + 分页走通，T11 ANCHOR-ALPHA 轮 67 s 完成）；**T13 超时**（24 次调用：`context_page_in` 9 成 7 败，败因 `primary_page_offset_invalid`——拒绝只说「不可用」，不给合法 offset/页大小/页数，模型反复猜 offset 并 grep 直到 react 上限）→ **事件 AF**（分页拒绝披露合法 offset、描述符带 page_size/page_count、引用被逐出用独立码）已派子代理。验证器（13/24 轮）：`RUN-12B-a6-verify.json`，A6-4 PASS、A6-3 因超时轮 INCONCLUSIVE、A6-2 待 AF 后整跑判定。
