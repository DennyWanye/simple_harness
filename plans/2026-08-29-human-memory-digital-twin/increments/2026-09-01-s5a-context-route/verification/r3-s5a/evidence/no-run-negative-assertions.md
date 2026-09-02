# 负向断言：S5A-S6 与 S5A-REG-FULL 不创建用户 root Run
- S5A-S6 冷启动 E2E：chat 被 Tauri 身份桥门控，未创建任何 SDK root run（backend log 无 run 创建事件；state.db run_context_snapshot_receipts 为空/仅测试库内）。cutover 演练均为迁移/组合测试，无 Run。
- S5A-REG-FULL：纯测试套件与静态检查，不产生产品 Run。
