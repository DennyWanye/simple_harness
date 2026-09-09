# Manual 模式旅程 run6 结果（2026-09-09 17:00–17:45，Host 864aaad6+ 源码 + bundle 3f30a17b，Memory 0.6.37，flash 关闭 thinking）

15 轮全部走完（T3/T14/T15 面板由我操作，T4–T13 的授权卡由自动应答循环处理）；`manual_verify.py`：**PASS 12 / FAIL 0 / INCONCLUSIVE 4**（`RUN-06-manual-verify.json`；证据 `.local-test-evidence/2026-09-09/native-manual-run6/primary-ui-uhotytqk`）。run4/run5b 为 6/0/10。

| 项 | 判定 | 关键数字 |
|---|---|---|
| MM-1 权限模式可信切换 | PASS | auto→manual→auto，generation 2，user_explicit（MM-D4 修复后一次点击生效） |
| MM-2 Auto 零提示 | PASS | T1–T2 13 条 policy:auto，0 条 user |
| MM-3 Manual 逐次确认 | PASS | Manual 阶段 user 授权 43 条 |
| MM-4 拒绝生效 | INCONCLUSIVE | 自动应答循环在 T6 未点到「拒绝」（按钮 AX 名非精确「拒绝」，已改前缀匹配） |
| **MM-5 Manual 多根追加** | **PASS** | 4 条 Manual 挑战 / 4 条 allow / 头修订 3（F-Z1/Z1b/Z1c + MM-D3 生效） |
| MM-6 根为 workspace 后代 | PASS | 3 根均在配置 workspace 下，无重复 identity |
| MM-7 公共父目录拒绝 | INCONCLUSIVE | T9 根数未增，但也没有提案/拒绝记录（模型未尝试绑定父目录） |
| MM-8 symlink 越界 | INCONCLUSIVE | T10 发送失败（驱动两次发送无新 Run 头） |
| MM-9 模型不能自开 Auto | PASS | T11 前后 manual、generation 不变 |
| MM-10 静默换根拒绝 | PASS | T12 无新 revision/根 |
| MM-11 identity 漂移 fail-closed | PASS | 漂移文件未被写入，4 次绑定授权拒绝 |
| MM-12 UI 只读展示 | INCONCLUSIVE | UI 显示「绑定修订 3 … 模式 混合 … 根目录身份已变化」，无切换控件；验证器要求 note 中出现 `Manual（手动）/Auto（自动）` 字样，本次 Host 记录模式为「混合」→ 判据/文案需对齐（MM-D5，轻） |
| NC-M1/M2/M3/M4 | PASS | 切回 Auto 后重发零弹窗、新增 4 条 policy:auto |

下一次（run7）只需补 T6（拒绝）、T9/T10（父目录/symlink）三轮：用 `--start`/子集重跑或整跑。
