# S-6 Malformed / replay activity events

**绑定**: TO-A5 (AC-5), TO-R3 (AC-4/AC-5)
**方式**: deterministic fixture/script
**状态**: PASS（deterministic contract smoke）

用固定 snapshot/event fixture 通过 `scripts/acceptance/agent_activity_timeline_smoke.py` 或等价 contract runner，黑盒验证：

1. 结果先于调用到达：按 stable identity 归并并按 causal/order 排序。
2. 相同 event identity 重复到达：只保留一条，不增加计数。
3. 缺少 status、tool name、phase title 或 identity：安全降级为“记录不完整/未知”，不得伪造成功且不得崩溃。
4. 超长输入/结果：保留有界预览和截断标记，不输出敏感字段。
5. 模型拒绝调用工具：正常完成且无伪造工具步骤。
6. terminal completed/failed/cancelled 到达后再注入 open/late event：终态保持不变，不复活 running。

证据要求：命令由 gate `record-run --exec` 执行；primary 日志写入 `.local-test-evidence/<date>/S-6/`，结论记录 fixture digest、root_run_id 和断言摘要。
