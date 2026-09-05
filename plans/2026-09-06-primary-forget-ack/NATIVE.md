# 原生遗忘确认验证与内存清理

最后更新：2026-09-06。

## 实际通过的行为

实际 native source b32a96d92d94ddce437cc1bb4c89c4a83f994133，安装 Harness 0.7.2 / Memory 0.6.12 / Service 0.3.12。
二进制 SHA-256：407d56ced02175d0c0b5daba5c1a5d9e79b0e5721d4970f4178f9f26fc009d8a。

1. 原隔离测试数据中，记忆列表与图谱均实际显示 6 条记忆、0 条关系。
2. 在真实 UI 点击测试记忆“User requested a fixed one-line reply”的遗忘按钮。
3. UI 明确显示“已忘记该记忆；保留原始历史档案。”，其余列表仍可用。收到确认而非仅观察到内容缺失。
4. 切换关系图，实际图谱更新为 5 条记忆、0 条关系，不再出现未确认遗忘导致的阻塞。
5. 日志记录一次 memory.suppression_appended。没有发新聊天、CREATE 或 Provider 请求。本次测试只验证该动作的确认和图谱恢复，不覆盖所有SDK操作。
6. 原应用通过正常退出返回 exit0。

以上局部闭环 PASS。多节点标签重叠和真实关系展示仍待验证；本次没有预先选中目标详情，不能额外声称详情清空已实测。

## 被中断的重启验证

同二进制、同userdata重新启动 dt793dyi/PID8073。恢复主对话后读取记忆时，用户报告系统应用内存不足。
立即停止该应用及后端，退出为 -15。重启后的记忆列表/图谱验证未完成，明确记为 INTERRUPTED；不从重启前成功推断持久化验收通过。

## 内存原因与处置

16GB机器上同时存活两个模型后端：新native子进程8085的 top MEM约6338M；旧临时测试数据目录的孤儿后端95141约6678M。
旧后端虽占8100，但所用目录为临时 hm072-ui 测试数据且父进程已退出，确认后才清理。
终止两组进程及其自有MCP工具子进程；没有退出用户的ChatGPT、Chrome、编辑器或远程应用，没有删除测试数据和证据。
清理前物理空闲约99M、swap使用约16.7GB；清理后一次观察空闲约5GB、swap约6.5GB，后续约4.5GB/6.3GB。
这些是当时系统采样，不将swap直接等同进程RSS；内存不足不能归因于某个SDK缺陷而未经调查。

本地测试启动器现增加独立进程组退出清理（含父进程先退出、子进程仍持有stdout的情况）、单native实例检查和7GiB可用/可回收页的启动预算。
以真实短生命周期父进程和存活子进程验证：孤儿子进程退出、继承管道关闭、拒绝清理本协调进程组，PASS。
该修改仅在ignored本地测试启动工具中，不声称生产Tauri进程管理已修复。
后续只允许一项重型测试；无真实模型需求的检查不加载模型，测试结束核对PID和内存。保持简短工具输出和必要截图，避免聊天渲染负担。

## 本地证据索引

根目录：simple_harness/.local-test-evidence/2026-09-05/human-memory-resume/primary-ui-9al9dv2k/。
启动器沿用历史日期目录；实际动作日期为2026-09-06。AX文件均为disableDiffing=true完整快照。

| 文件 | SHA-256 |
|---|---|
| launch.json | ca4bfd79db8a22f459b4f41aba0732f2882bbe377a00b92ef0aabf2d99fe9ca6 |
| 01-before-forget.ax.txt | b6496545f91a58eddead7b98f2d025482e73c1af4e2bc27280d3db8205d927f1 |
| 02-graph-six-before.ax.txt | d09e301d01aa78c0ee16916a2b6e923c40d2dbff59f62622a57102b5fd1b8338 |
| 03-forget-confirmed.ax.txt | 683a51dbb48bf7f87cb00ffc28bdcae797fc237fdb42197c1f21f28aa9a5aff5 |
| 03-forget-confirmed.png | 8893d5026ff6d8a968fa01956a5dab196bc9b56a02bec42695c00bccee2885bd |
| 04-graph-five-after.ax.txt | c41c10ec4639ae433268bf9522aa0c096dd248e22a4db29bb736d534b1e5e4fd |
| 04-graph-five-after.png | c31f39251a6d58b69842bf2b54c2482b1973a26b5fb4e444ec5c32c7e447e2d9 |
| native.log | 624a8e29c6320ec33150b71b85759554dacbb3c4c61f49fec09f2e50ca22be49 |

清理进程清单在 main `.local-test-evidence/2026-09-06/memory-cleanup/owned-processes.json`。原始证据只留本地ignored目录。
