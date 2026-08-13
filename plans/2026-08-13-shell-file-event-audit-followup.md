# Follow-up — Shell 瞬时文件事件审计

> **状态**：📋 已登记，后续处理；当前不阻断受信任 workspace 内的 Harness 使用
> **优先级**：P2（可观测性与取证增强，不是现有输出契约的正确性缺陷）
> **登记时间**：2026-08-13

## 1. 当前边界

Harness 已在工具执行前拦截直接文件工具的输出契约越界，并在 child 终审时重新计算 workspace
摘要，捕获全部最终残留。对于不透明的 `run_shell`，如果命令在一次执行期间于 workspace 外创建
文件、随后又在终审前删除，最终态扫描无法重建这个瞬时事实。

当前行为必须诚实描述为“最终残留可验证”，不能把命令文本解析或执行后目录扫描表述成完整的
运行期文件事件证明。该边界不影响已经验证的幂等 effect、Run 恢复和产物交付语义，但不适合把
未知来源脚本当成无人监督的高风险任务执行。

## 2. 后续目标

建立 OS 级、Run-scoped 的文件事件观察器，把执行窗口内的 create/write/rename/delete 事件与
`run_id / call_id / effect_id / attempt` 关联，并在终审中报告：

- 声明输出、声明 scratch 与允许目录中的事件；
- workspace 外或未声明路径的瞬时事件；
- 观察器丢事件、缓冲区溢出、权限不足和覆盖范围不完整；
- 事件摘要与最终目录摘要之间的差异。

这是一条审计/可观测链，不解析 shell 文本，也不伪装成强安全沙箱。平台实现需分别评估 macOS
FSEvents/Endpoint Security、Windows USN Journal/ReadDirectoryChangesW、Linux fanotify/inotify；
任何存在合并、丢事件或权限限制的实现都必须 fail-visible，不能静默宣称“零越界”。

## 3. 验收要求

1. `run_shell` 创建并保留越界文件：现有最终摘要和事件审计都能发现。
2. `run_shell` 创建后立即删除越界文件：事件审计能发现，最终摘要明确显示无残留。
3. 合法声明 output/scratch：事件被正确归类，scratch 清理后不误报。
4. rename、atomic replace、symlink 与并发 child 各自保持 Run/effect 归属，不串线。
5. 观察器关闭、无权限、事件丢失或缓冲区溢出时，终审明确标为 coverage unknown/partial。
6. 自动化覆盖事件归并与账本契约；真实 macOS Tauri E2E 至少验证一次瞬时 create-delete。

## 4. 非目标

- 不在本 follow-up 中引入容器、VM 或命令白名单。
- 不把事件观察器当成阻止恶意代码的安全边界。
- 不上传原始路径事件日志；证据继续保存在 gitignored 本地目录，后续归档到 NAS。
