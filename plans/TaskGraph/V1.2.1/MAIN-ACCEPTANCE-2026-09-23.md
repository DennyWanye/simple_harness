# TaskGraph 主体阶段验收与第三部分交接

plan-status: finalized（用户 2026-09-23 明确调整本阶段测试范围）

本阶段目标：完成 TaskGraph 主体代码和接线，使用 HTN 的 DEEPSEEKER provider 做少量真实关键链路验证，修复发现的问题后通知任务“第三部分”继续 Assurance 开发。整体计划没有取消；重型测试延后到三部分整体完成后的统一验收。

集中由主代理编码；独立 Sol 只读挑战代码。共享 HTN/Host 不覆盖，源码交接使用隔离候选与补丁；原始证据只在 ignored `.local-test-evidence`。

## 本阶段固定预期

| 项目 | 通过条件 | 本阶段方式 |
|---|---|---|
| 主体实现闭合 | TG-A–E 的真实入口已连接，没有已知未修主体缺口 | 按模块静态调用链及独立挑战；不是测试覆盖率 |
| 一条真实业务链 | 原 Host 创建、完成要求确认、规划授权、显式 kernel 启用、真实模型方法/执行/正式评审到 Mission COMPLETED；真实文件内容符合需求 | 一个 DeepSeeker CONTENT_ONLY Mission；单并发，上限24物理调用，不以输出声明代替正式完成 |
| 图读入口 | snapshot/why/diff/convergence 四只读入口无新事件/派发；历史 frontier 为空且非执行视图 | 复用上述真实Mission，原API读取 |
| 冷恢复 | 同一候选 rebuild，原Attempt/intents/events/revision/Provider调用和文件hash不变 | 复用上述真实Mission，不另开模型 |
| 故障责任 | 新启动装配顺序、原Worker强退及Host UNKNOWN恢复不重复调用/释放未决占用 | 候选19已完成的具名定点证据复用；仅源码改变时复验受影响面 |
| Host图界面 | 对本次候选作基本真实点击检查，显示当前/历史结构及读取失败状态，不扩大原生矩阵 | 待主体真实链通过后 |

脚本Provider结果不计真实模型证据；不伪造授权/APPLIED/receipt。必要的测试授权由用户本阶段指令提供，通过原公开/固定可信接口执行。

本阶段明确后置：42组完整负控矩阵、12定点变异、200×50 stateful、大规模legacy/全H1回归、多个领域/多seed/统计稳定性/性能上限、原生完整交互矩阵。均记 DEFERRED，不能记PASS或关闭原全门。最终TaskGraph acceptance manifest保持 NOT_RUN 直到对应全门真实通过；主体阶段结果另行记录。

“第三部分”任务ID `01a0c5e5-ad91-71a2-930f-9d7ddbc66600`。通知只能在上述本阶段主体验证和必要修复完成后发送；通知须附源码/包身份、范围、局部失败历史和全部后置门。
