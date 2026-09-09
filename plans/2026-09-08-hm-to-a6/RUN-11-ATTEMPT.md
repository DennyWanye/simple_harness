# HM-TO-A6 第 11 次整跑（2026-09-09 13:55 起，Host 26fd6f6f 源码 + bundle 3f30a17b，Memory 0.6.37，flash 关闭 thinking，窗口 32000）

带入（第 10 次之后合入）：事件 Z（拒绝文案按 Run 事实 + 预算见底收尾指令）、事件 AA（租约 <10 s 重收集）、W-b（关闭 thinking 时携带量只取 hidden）、事件 X（Run 终态释放 ToolRegistry._calls）、事件 AB（A6-4 口径）、**F-Z1**（读工具调用期门：投影露出 read_file/glob/grep/list_directory，调用时按已落地的任务路由校验根）、MM-D3/MM-D4、F-NC1 PERSONA、F-MMD-1（本次又捕获一处：`sdk_workspace_read_gate` 未入白名单，`26fd6f6f`）。

验收目标：A6-2（首次可达）、A6-3（组装超限 0）、A6-5（T17/T18 不再被门/租约打断）、A6-8 复核、整跑 ≥15/18。

## T6（14:05）：读门首次生效，但 fixture 在任务根之外 → F-Z1b

`workspace_read_denied tool=read_file reason=path_outside_workspace_root` ×3（read_file ×1、list_directory ×2）。读门按 F-Z1 正确失败关闭：任务唯一已验证根是托管目录 `task-<id>/`，而 fixture 在 `SimpleHarnessWorkSpace/a6-fixture/`。但读门没有像效果路径那样走 S4 多根绑定提案（Auto 下策略自动授予、Manual 下弹绑定卡），模型无路可走。→ **F-Z1b**（读越界进入同一绑定提案权威）已派子代理。本次 A6-2 仍不可达；旅程继续用于验证 AA/W-b/Z/X 与关系/争议段。
