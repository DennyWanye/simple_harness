# 原生 r24：Procedure 验收未完成

更新：2026-09-07。被测 Host `b2da14da`，H079/M619/S0313；运行期间源码固定。复用相同前端 ff35fb82 的原生包，编译后端端口18120，不重建。

## 实际结果

首条真实用户输入只要求记录待定“松柏记录”文件流程，不执行。原生回复确认记录，记忆列表显示相关条目；仅凭列表不判断条目已经是 Procedure/DRAFT。

第二条真实用户输入要求新建独立任务、查找待定流程、临时执行并读取 record.txt/backup.txt 核验。过程中曾看到 context_route 权限弹窗；本轮续接时弹窗已不见，主界面显示“等待授权”。刷新状态、窗口菜单重新选中、切换技能中心再返回仍无弹窗。保存03现场后，通过真实“停止”按钮终止运行。

停止后 UI 变为空闲并补出历史：context_route（call_8MdSh1x7UiWsdvtpRj1iWYub）为 succeeded，随后4条 tool_search 成功结果。未观察到 procedure_discover、procedure_use、文件写入及读取核验的完成结果。此前仅凭“等待授权”推断创建仍待批准并不可靠，需结合准确 Run/决策日志定位。原生 Procedure 完整验收不计PASS，也不计240质量执行。04 AX含补出工具历史，04截图只显示当前可见部分，不宣称截图包含全部工具正文。

## 退出与防熄屏

通过原生Cmd+Q正常退出。资源包装 PG50771 exit0，elapsed1046.271s，峰值1298608KiB，RSS限8192MiB、时间限1800s；stop_reason=null，remaining_group_members=[]，cleanup_error=null，最低磁盘1365MiB。此次不是内存准入或超预算中断。

用户要求防熄屏直到整个测试阶段结束。macOS自带 `/usr/bin/caffeinate -diu` 已独立运行，PID56392，终端会话98128；pmset确认 PreventUserIdleDisplaySleep 与 PreventUserIdleSystemSleep 均为1，asserting forever。旧绑定单次测试的PID55721已终止。后续应用重启不清PID56392；仅全部测试结束后核对PID仍为本命令再终止它。此记录是运行交接，不更改系统永久电源设置。

## 本地证据

原始文件仅留 ignored 路径。首条记录及记忆列表证据见同目录00–02；以下为本轮续接与退出证据：

- `.local-test-evidence/2026-09-07/native079619/primary-ui-0veyms93/03-waiting-without-dialog.ax.txt` SHA256 `3e96f9310d79d96d5dd28cbca5adb18a6f72c5a8ecb53d3000e806f5bcbd0078`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-0veyms93/03-waiting-without-dialog.png` SHA256 `a89d758e3cdd976aa3abb51c1eb80f287f973aafe944eb6edf4168e333f43eeb`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-0veyms93/04-stopped-actual-history.ax.txt` SHA256 `610fd1f46eaab10456d92c3cca637599864d407051b9fa9cc96d01d87c014bfc`
- `.local-test-evidence/2026-09-07/native079619/primary-ui-0veyms93/04-stopped-actual-history.png` SHA256 `279a2915d6b4273920bfc33e5b6da84bae5505b8c90ffa70075e77a4134eb7c4`
- `.local-test-evidence/2026-09-07/native079619/r24-procedure/resource.json` SHA256 `f6cf86134dc2d69ac6701216dd483aed77468e8307ebd1aaf726e6b0731a3302`
