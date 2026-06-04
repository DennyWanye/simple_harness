# FP-2 实现笔记 + defer 记录

## 已完成（committed）
- WI-1.3 re-anchor：compress/compact 加 `goal_text` + 软上限1500 注入 `[目标锚定]` system 段；agent_loop **决策点每 5 轮 anchor**（`_GOAL_ANCHOR_EVERY`，独立于压缩触发，比压缩路径更可靠）。
- WI-1.4 handoff：spawn_team/_build_charter 加 `parent_goal_text` + Parent Goal 段 + `[off-goal]` 回收分组。
- WI-1.5 resume：auto_resume `goal_text_getter` + nudge 前注入 `[goal]` 续目标 anchor；main.py 已接 getter。
- WI-1.2 task graph：goal_tasks 独立 ensure（R-T5 flag-OFF 不建）+ 原子 claim（_write_lock 串行/同进程 T5）+ TaskGraphStore（环检测/progress 回填）+ 4 工具 + build_teammate_tools 接电。
- 测试：55 焦点 + 116+168 回归全绿；R-T5 baseline 扩断言 goal_tasks 不建，退 0。

## defer / 待确认（不少做，显式记录等用户拍板）
- **R-T4 respawn pending-resume 队列**：子代理确认 main.py:2090-2097 `_auto_resume_dispatch` 在 redispatcher 未注册 / ws=None 时静默 return（nudge 已入 queue 但不 dispatch）。**WI-1.5 核心（resume 注入 goal_text）已工作**；R-T4 是「redispatcher 未注册边角」的健壮性增强，需谨慎的 WS 重连 drain（control_ws 快照坑同构）。**defer 到单独小切片**，理由：非 FP-2 主线抗漂移功能，且改 WS 重连时序有回归面，宜独立验证。等用户确认。
- **WI-1.3 压缩路径 re-anchor 的 context_manager 接线**：实际压缩走 `agent/context_manager.py:361 compact_messages`（非 agent_loop 直调）。决策点 anchor（每5轮）已提供 re-anchor 主力。若要压缩路径也注入，需 context_manager 持 goal store 句柄——可作 1.3 增强。决策点机制已满足"长对话不漂移"，压缩路径注入为 belt-and-suspenders。

## 手测门
见 [02-manual-test.md](./02-manual-test.md)（MR-1.3 抗漂移真机）。
