# HM-TO-A6 第 12 次整跑（2026-09-09 18:35 起，Host 3308af01 源码 + bundle 3f30a17b，**Memory 0.6.38**，flash 关闭 thinking，窗口 32000，`--memory-probe`）

带入（第 11 次之后）：F-Z1b/F-Z1c（读越界 → 绑定提案；来源检查）、事件 AF（分页 offset 披露）、事件 AE（Prospective 触发器落地校验，协议 v10）、事件 AC（视图水位）、W-c（CJK 估算 + 实测比例）、X-2/X-3（page-in 字节上限；内存探针）、MM-D3/D4、验证器口径；SDK 0.6.38（租约退化码、争议 incumbent 向量、世代自证）。

验收目标：A6-2 首次 PASS（读 + 分页 + ANCHOR 行）、NC-3 PASS（模糊愿望不成 Prospective）、A6-5（T17 不再被门拦）、整跑 ≥17/18；内存探针报告回答 X2-F2（每轮 +250 MB 的站点）。

## 18:45 探针版中止

`--memory-probe` 开启后 T1 完成 50 s、嵌入批 55 s（正常 0.3 s），随后 T2/T3 连续「两次发送无新 Run 头」——tracemalloc 全量追踪 + Run 终态时的快照/gc 普查阻塞了事件循环，前端发送被吞。→ X3-F4：探针采样须移到线程/降低成本（默认关 gc 普查、快照仅每 N 轮）。本次改为不带探针重跑（run12），探针另用短旅程验证。

## 无探针版（run12，19:00 起）T6 → 事件 AG

T6 读路径全通（提案 → 重路由 → `read_file` 成功），事件 AF 的 `next_offset` 也被模型正确跟随——但 `PAGE_BYTES=1024` 让 40 KB 文件要翻 ~40 页，翻到第 13 页时开放分组把预算挤爆（`planned=27202`），事件 Z 的收尾指令未触发（`wrap_up_injected=0`）→ Run FAILED。→ **事件 AG**（页大小提到 4 KiB + 描述符带 line_count/char_count/首行，让「标题和总行数」不必翻页；收尾指令在开放分组溢出时也要触发）已派子代理。T8（参照件 B）31 s 完成。
