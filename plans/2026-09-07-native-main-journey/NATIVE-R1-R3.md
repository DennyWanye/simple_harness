# 原生旅程 r1/r3（Host main d94e93dc，H0.7.10 / M0.6.20 / S0.3.13，gpt-5.6-luna）

2026-09-07，taiwan Mac。构建：`SimpleHarness Memory Verify d94e93dcp18120.app`（debug bundle，二进制 SHA-256 见 `.local-test-evidence/2026-09-07/native-d94e93dc/../native-build-d94e93dc/artifact.json`），隔离 userdata，凭据仅进程内。启动/驱动脚本：`scripts/native/launch_native_candidate.py`（含 luna 预检与回退记录，本轮未回退）、`scripts/native/ax_click.sh`（System Events 辅助功能点击，WebView 在后台不接受模拟输入）。

## 通过的场景（真实 UI + 真实模型）

| 场景 | 结果 |
|---|---|
| 空目录首启 | 唯一主对话就绪，欢迎向导可跳过，后端 startup complete，WeMM 预热 11.7s |
| 写入长期偏好 | 用户说「阅读技术说明偏好 Markdown…」，真实 luna 回复；后台分析用真实模型提取 semantic `technical_documentation_format_preference="Markdown 格式"`，落库 1 条 |
| 记忆面板 / 图谱 | 列表显示该条并带「忘记这条记忆」；Cytoscape 图谱 1 节点 0 边 |
| 精确路由授权 | 模型提出 memory_standalone/semantic，UI 弹「Allow context_route for this exact request?」，点允许后执行 |
| 类型化召回命中 | 提案查询「用户阅读技术说明时偏好的格式…」→ `recall-item:…` 命中，route 决策记录 recall_refs；最终回答按偏好用 Markdown 标题与代码块 |
| UI 遗忘 | 点「忘记这条记忆」→ 提交后短暂「尚未确认」，刷新后「已忘记该记忆；保留原始历史档案」；存储 suppression 1 条；图谱同步为 1 条记忆 0 边 |
| 干净退出 | Cmd-Q 等价 quit，资源 runner parent 0，remaining=[]，峰 1.4 GiB |
| 重启恢复（r3，同 userdata） | 无向导、已连接、主对话历史恢复；记忆列表仅剩未遗忘项；重新召回命中并给出最终回答（21s） |
| 遗忘后召回 | 忘记该偏好后再问同一问题，route 召回 `recall_refs=[]`（零匹配） |

## 发现（真实缺陷 / 待办）

1. **召回为空后模型循环重试路由**：遗忘后的提问，模型连续 5 次提出同一 memory_standalone 提案，每次都要用户点「允许一次」，最终以「停止当前任务」结束。语料 C01-10 修复前同样出现 7 次循环。建议：Host 对同一 Run 内重复且结果为空的 route 提案给出明确"无记忆"反馈或上限。
2. **每次精确路由都需人工授权**：真实 UI 每轮记忆召回都弹授权卡；与"每轮判断需要什么记忆"的产品目标相冲突（语料跑道用只读审批自动放行）。属产品策略决策项。
3. **对话视图渲染原始工具 JSON**：召回后聊天区显示完整 context_route 回执 JSON（1443 字符），可读性差。
4. **遗忘会隐藏整个来源轮次**：忘记记忆同时使其来源对话轮从普通视图退出（符合 AC-1「退出普通视图」），但两条记忆都忘掉后主对话显示为空——是否应保留不含该记忆的普通对话文本，需产品确认。
5. **重启后视图只恢复完整因果组**：未产生最终回答的轮次（被拒绝/中转站 502 的轮）不在恢复的历史里，属设计口径，记录备查。
6. **中转站 luna 通道不稳定**：本轮两次最终回答因 502 失败（r1 第 3/4 轮），预检 200 后仍可能失败。环境问题。

## 我造成的污染（已清理，需用户知晓）

r2 启动时我在 App 未注册前执行了 `osascript activate`，按 bundle id 拉起了**第二个无隔离环境变量的实例**（默认数据目录）。该实例的后端打开了用户真实数据目录 `~/Library/Application Support/com.dennywanye.simpleharness/`：`state.db`（user_version 已 45，仅 context_route_registration_repaired 修复写入）、`companion.db`、写入 `onboarding_done.json`、`window_geometry.json`；`human_memory_v7.db` 与 SDK `execution-v6.sqlite3` 本体未改（wal 0 字节）。该实例已终止。r2 的"重启失败/连接风暴"观察全部来自这个错误实例，作废；r3 为正确重启验证。启动脚本本身从不 activate；驱动侧改为等 startup complete 且实例已注册后再 activate，并在 activate 前后核对进程数。

## 原始证据（ignored，相对 Host 根）

| 路径 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-e37vj5m9/launch.json` | 478636c13c391df91717b2263e62c1ad81858bdd00cc30a1e19f83eda59e76f8 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-e37vj5m9/native.log` | 3d18fa0be6459a4cac55ff7c35f36d2152173548e9305de4cbf6dd97065bc48b |
| `.local-test-evidence/2026-09-07/native-d94e93dc/resource-r1/resource.json` | 9c596117cb552a4f20a6cf8ec92ad4813fd1245cb4798547f25d239b3a24b8ec |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-y_uqs4ux/launch.json` | a5ea701f05575a59cd4c5e56e92669d6ba187f257354477108973bd15c88a3ff |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-y_uqs4ux/native.log` | 23bfcb67cc02756e807960a039164eebaf1c512b54f52ec58f4cdaa79044cd3c |
| `.local-test-evidence/2026-09-07/native-d94e93dc/resource-r3/resource.json` | 0ff601e833ec52bef3f3a6470effbc1aa2e27e37de250f61f43403a9eb39a551 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/resource-r2/resource.json`（作废轮，仅存档） | 2d77404a14bfa8bc005290d7775e159561add26ffa9edb8ae7d0033ede3658c0 |
| `.local-test-evidence/2026-09-07/native-d94e93dc/primary-ui-e37vj5m9/userdata/data/human_memory_v7.db`（三库共享 userdata，退出后快照） | d86bc8a78763da1bf271433c1329427faa9e01620ce6f354b6d8d6c6b81f9062 |
