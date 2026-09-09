# HM-TO-A6 第 12 次整跑结果（2026-09-09 19:50，Host 3308af01 源码 + bundle 3f30a17b，**Memory 0.6.38**，flash 关闭 thinking，窗口 32000，无探针）

24 轮走完；`a6_verify.py`：**PASS 14 / FAIL 1 / INCONCLUSIVE 3 / BLOCKED 0**（`RUN-12-a6-verify.json`；证据 `.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx`）。历次：第 10 次 14/2/2 → 第 11 次（复算）16/1/1 → 第 12 次 14/1/3。

| 项 | 第 11 次（复算） | 第 12 次 | 说明 |
|---|---|---|---|
| **A6-2 大结果分页** | BLOCKED | **PASS（首次）** | 34 个分页引用、read_file 读 fixture、ANCHOR 行在回复出现（F-Z1/Z1b/Z1c + AF） |
| A6-3 | PASS | FAIL | 组装超限 3 次（T6/T11/T17：1 KiB 页 + grep 失败 + 收尾未触发）→ 事件 AG/AH（修中） |
| A6-5 | FAIL→（AC 后可判） | INCONCLUSIVE | T17 未记 goal（模型走 write_file/构造引用，三处不透明回执 AH-2/AF-2） |
| A6-7 / A6-8 | PASS / PASS | INCONCLUSIVE / INCONCLUSIVE | T20/T21 分析批**全部操作被拒**（协议 v10 回归，事件 AJ 修中） |
| **NC-3** | FAIL | **PASS（首次真实计量）** | 事件 AE 生效，模糊愿望只落语义兴趣 |
| A6-1/4/6/9/10/11/12、NC-1/2/4/5/6 | PASS | PASS | — |

## 本次证实

- 读路径 + 分页 + AF 的 offset 链在整跑中稳定（A6-2）；AE 生效（NC-3）；后端 RSS 终值 **2.0 GB**（第 11 次 4.7 GB）。
- 失败 Run：T6/T11（AG：页太小、收尾未触发；AH：grep 文件路径 tool_failed）、T17（AG + AH-2/AF-2 不透明回执）、T18（**事件 AI**：glob 效果证据种类被归档入口拒绝，整 Run 死）。
- 新回归：**事件 AJ**（v10 下 T20/T21 更正与争议提案全被拒）。

## 进行中

事件 AG（页大小 4 KiB + 描述符行数/首行 + 收尾触发）、AH（grep/glob 文件路径 + tool_failed 原因码）、AI（读工具证据种类）、AJ（v10 回归）；X3-F4 非阻塞探针已合入（`1e825018`）；两轮完整流程旅程 flow1 已启动（`twoflow-run1`）。
