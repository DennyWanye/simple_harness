# S-1 工具驱动任务

**绑定**: TO-A1 (AC-1), TO-A2 (AC-2), TO-A7 (AC-7)
**状态**: NOT_RUN（macOS 锁屏，待用户解锁后真人复测）

1. 从主消息页用真实坐标点击 Inspector/执行记录入口；截图记录入口可达，声明 `坐标=...|动作=点击执行记录|期望=面板打开`。
   预期：面板打开，不影响消息输入。
2. 输入“请只读查看 `backend/tests/test_agent_activity_projection.py`，运行 `python -m py_compile backend/tests/test_agent_activity_projection.py`，并告诉我结果。”并发送；截图和 backend/tauri 日志保存到 `.local-test-evidence/<date>/S-1/`。
   预期：真实工具任务创建，记录顺序明确为阶段 → 工具调用 → 工具结果 → 验证 → 终态，且 root/run identity 一致。
3. 用真实坐标依次展开阶段、工具条目、输入和结果详情；每个动作记录 `坐标|动作|期望` 并截图。
   预期：只显示安全投影和有界预览；使用确定性长 fixture 时显示“已安全截断”；不显示 reasoning、凭据或原始 provider payload。
4. 等待任务完成并截图终态；关闭 Inspector，再发送一句“继续说明刚才的检查结果”，截图证明消息输入仍可用。
   预期：终态显示已完成，之前的进行中条目不再保持 running；关闭面板不阻断后续聊天。
