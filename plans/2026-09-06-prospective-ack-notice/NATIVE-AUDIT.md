# r17/r18：Host 持久审计关联

更新：2026-09-06。Host `55eb273d` / H079 / M618。仅以 `mode=ro`、`query_only`、read transaction 读取原 r14 userdata 的 Host `operation-audit.db` / `state.db`；未启动运行栈、读取 SDK 私库、修改原库或重发 ACK。UI结论由主原生证据给出，见主已提交 `dd3f0129` 的 `NATIVE-R17-R18.md`。

| 项 | r17 | r18 |
|---|---|---|
| Host Run | `c815ec2a-5366-531f-9f8b-92e629590297` | `d0840269-67ed-5f01-842f-872bdf5a2a58` |
| SDK Run | `product-sdk-49db2111ef6d060de3f6a95c9229d888f12599f701602af562ae5120f247951c` | `product-sdk-92caf1463842541065100a68dadfdf68cadd97b35eeda221606f0b78de7ca34e` |
| audit job | `6e882373890dfe3455abf3b3f8a20e92a7ee3da5af0591d3fc5a58ba3fed3c74` | `2d271fe1eff5573e2153006467048ad4c00871173d1e77e37f76dd5e82adb21a` |
| 枚举 | enumerated，122/122，1页 | enumerated，98/98，1页 |
| snapshot | `5285dac5954cd23f8746c18c55428a42bb84618c16c4028c16445f25a9ed57f8` | `02d37071a25650f6bc72193cc7e05736c2bc72bdc475642a942ae02e95b11ed2` |
| mandatory repair | 1 次真实拒绝及有界反馈 | 0；模型第一轮主动 ACK |
| Provider 成功 head | 3 | 2 |
| Host occurrence | claimed → presented → acknowledged → settled | claimed → presented → acknowledged → settled |
| UI | 63及青竹提醒正文；续轮无重复 | 47，无银杏提醒正文：FAIL |

两份公开审计页 metadata 均为 `recording_coverage=verified_current_intervals`、`coverage_gaps=[]`、`history_coverage=recorded`、`snapshot_source_complete=true`、Run completed。这证明该区间记录覆盖；不等于所有操作成功、所有SDK方法覆盖或用户看见提醒。r17另有 `memory.record_committed_turn` unknown，不能隐藏。

r17 第一请求 hash `a8d2919f99d68057419410c5bca386efee4a824a5878e83d6a32041847050c27`，响应 hash `9d1cb8f2a5bda432ae1fcb6d811e8ff1b43dacbf84fd25f08dfdfdba729e9e13`。公开 canonical hash 对 `{request,response,mandatory_repairs:0}` 得到失败 `context.no_recall` 的 request hash `300727f2910b1606032f7fb9890cb552f55514f071c784f39c277c0eacaff119`，错误 `mandatory_context_action_required`。以实际 Run/turn1/request身份及 repair ordinal1 重建公开 feedback commitment，`{repair: feedback.to_json()}` 的 hash `ad3c144b24605696ab1679c0996f51429c87f8920b2e2e236789ae43f3168afc` 精确匹配同 parent 的成功 `context.apply`；随后 turn2 的 ACK effect succeeded。这是实际 repair 关联，非仅凭安装测试或模型主动 ACK 推断。

r18 无上述拒绝/repair apply；turn1 ACK effect succeeded，随后 final no_recall completed。缺口在用户正文送达，不能把此例归因于 repair 失败，也不能把 processed ACK 撤回为 pending。

- r17 occurrence `f1d9823558f7c3df2edf781e29766d54592e58704dcd42bad21e9bc64678ac49`；ACK `9e95350325c3c7da4650e5d93d60b5b982dd1f9dba16008f525fe4973e84193f` / `f96704a8a6852458c15a7184bd3841dfec9f48062311db356df1dedd3a76c74e`。
- r18 occurrence `4a059122dc505ffe589fbd2b9c8717f2bf326db5bd3d16b865129e6ef0d3d0ed`；ACK `cbc58dbb86db79d0c5e5e22375166339319247e621a14652eedf5a28a4120fc9` / `7f574275fe960bc471d4995327ef464578cb9f83b71f38f8593cd3d963cac5ca`。

## ignored 证据索引

本叶根下 `.local-test-evidence/2026-09-06/prospective-ack-notice/native-audit-summary.json`：`c64c080bb2a8f973b77736615ea0a841f26b4748cefe42b8d06ce7dd26d2789d`。仅导出 Host 已持久公开审计 DTO 的身份/状态/hash字段；不含查询或 Provider 正文。此 hash 属于固定导出，不是声称仍在使用的原数据库字节永不变化。

以下在主 `simple_harness-primary-candidate` 根下，SHA复用主 `dd3f0129` 已固定索引，不重复原证据扫描：

- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/03-reminder-delivered.ax.txt`：`d41ffc9cce3cd423f90fbb1efa8e1b42b7aa8e8ec28bba728646156716942235`。
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/05-ack-without-reminder.ax.txt`：`ce765ac0dbaa50055d2752f69a203aaa80606ebb73cfa924259ad0b81517f4cb`。
- 同目录 `05-ack-without-reminder.png`：`8da1348931c382ea53b40dead974d8630539c2ec5db01672fab52b378dfd47d5`。

原 r18 FAIL 保留。新 typed notice 的后端可读、前端渲染、真人实际看见须分别记证据；本审计不追认送达。
