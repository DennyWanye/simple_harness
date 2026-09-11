# HA-12 原生 App 验收报告（AX 驱动）

- 日期：2026-09-12
- 验收条目：HA-12 ①–⑥，同时覆盖 HA-1 ③（启动冒烟）、HA-23（产物读取，原生部分）、HA-22 ②（后端重启），以及裁决 C（默认预算）的原生复验
- 模型：DeepSeek 官方端点 `deepseek-flash`（按用户规则只用 flash）。测试场景用 SDK 的脚本化夹具，不调用模型
- 数据：
  - 真实数据副本 `.local-test-evidence/2026-09-12/native-ui-0910/prod/userdata`：从 `~/Library/Application Support/com.dennywanye.simpleharness` 复制 data、capabilities、本机身份、config.toml；
  - 测试场景用另一份全新副本 `…/scenario/userdata`；
  - 副本里的 `llm_runtime.json` 写入 deepseek-flash，不含密钥；密钥由启动器以 `DESKPET_CLOUD_API_KEY` 传给后端。
- 驱动方式：System Events AX（`ax_click*.sh`、`ax_set.sh`、`ax_dump.sh`），不截图；核对用只读 SQLite 和 `ha12_verify.py`
- 工具与脚本都在 `.local-test-evidence/2026-09-12/native-ui-0910/`（不纳入版本管理）

## bundle

| bundle | 源提交 | 用于 |
|---|---|---|
| `SimpleHarness Agent Verify 20c73ca5p18120` | `20c73ca5` | ⑥、①、第一个 Mission、④（已结束部分）、②（第二个 Mission）、③、HA-23 |
| `SimpleHarness Agent Verify f51ddc37p18120` | `f51ddc37`（默认预算修复） | 留空预算的 Mission、④（在途部分）、⑤ |

两个 bundle 都从干净的提交构建：构建脚本会检查 backend / tauri-app / scripts 没有改动。打包出的前端产物里确认包含本轮新增的界面文字。

## 结果

| 项 | 结果 | 证据要点 |
|---|---|---|
| ⑥ 启动时编排服务可用 | PASS | 共启动 6 次，每次都满足：`/health` 为 ok，`startup_errors=[]`，`native.log` 里有 `orchestration_ready state=available`，执行库迁移记录为 `[7, 9, 10]`，`native.log` 里密钥模式命中 0。<br>部署清单：`host_commit` 等于 bundle 的源提交，`host_dirty=false`，钉版一致（0.9.10 / 0.9.3），`local_code_execution=false`，模型 `deepseek-flash`。测试场景下 `test_scenario=approval-action`，模型为 null |
| ① 视图与策略卡 | PASS | 侧栏"任务编排"用前缀匹配点中（有待审批时按钮名会带上角标）。视图显示：新建 Mission、空态提示、只读策略卡，当前版本 `policy-5a04e242b3683c61` |
| ② 用 AX 新建，状态到终态 | PASS | 第一个 Mission：预算留空，Planner 编出的 Task 预算只有 800 tokens，结果 FAILED / `budget_exhausted`，停止原因如实显示。按独立评审裁决 C 修复后见下一行。<br>第二个 Mission：表单填了预算 400000 / 3，界面依次显示"请求已接收 → 正式交付"，`verification_passed` |
| 留空预算（裁决 C 复验） | PASS | 第三个 Mission 预算两项都留空。详情显示"预算：Token 上限 400000 · 尝试次数上限 12"，最终 COMPLETED |
| ③ 编排运行期间，主对话完成一轮 | PASS | 第一次尝试没能完成：副本里缺少 `model_overrides.toml`，报 `foreground_provider_context_window_unavailable`，属于测试环境问题。按用户 2026-09-09 的决定补上 flash 的 32000 窗口后重做：新 run 在 1789145859.76 创建、1789145862.94 completed，时间落在第二个 Mission 的运行区间（1789145846.99 到 1789145862.29）内 |
| ④ 退出（SIGKILL）后用同一份数据重启 | PASS | 已结束的 Mission：重启后列表与详情一致（失败 / budget_exhausted、各层结果、Token 预留、未计价），"取消 Mission"处于禁用状态。<br>在途的 Mission：后台脚本在 Attempt 进入 RUNNING 时立即让 App 正常退出（1789146380），Tauri 用 SIGKILL 结束后端。重启后第 22 s，新 owner 接手了租约，心跳 `liveness.blocked=1`；界面在列表和详情里都显示"UNKNOWN（结果未知）"，并出现"回合结果未知"卡片（依据为空时两个接管按钮都是禁用的）。用 AX 填写依据、点"接管：重试"后，约 52 s 进入 COMPLETED |
| ⑤ 测试场景的审批 | PASS | 视图显示"测试场景"横幅，状态显示"待人"，等待原因为"动作审批（自 01:13:28）"。审批卡上"拒绝"在理由为空时禁用。点"批准"后变为"正式交付 · verification_passed"。<br>编排库：审批为 GRANTED，决定记录带 `receipt_hash`，决定人 `local-user:…`；动作为 SUCCEEDED；事件依次是 ActionProposed → ApprovalRequested → ApprovalGranted → ActionHandedOff → ActionSucceeded；回执为 `after: on, applied: true`，带回执 hash。测试配置服务的 `feature_flags.new_ui` 变为 `on`，账本里只有 1 条。<br>场景副本里没有正式编排库，即 HA-20 的"正式库一行不写" |
| HA-23 产物（原生） | PASS | 第二个 Mission 的详情显示"NOTES.md · 269 B · 验证：UNVERIFIED · 7ea07898712e"。点"查看产物"后，显示完整 hash、大小和全文（三条中文），并标注"模型生成，未核实" |
| HA-22 ② 后端重启 | PASS | 见 ④ |
| HA-22 ① UI 重连（后端不重启） | 部分完成 | 原生 App 里只做过切换视图，视图重新挂载后，Mission 没有重建，已完成的 Attempt 没有重派，列表状态一致。<br>WebView 刷新和关闭窗口再打开这两种情况没有在原生 App 里做；前端测试覆盖了重连后自动刷新详情与事件的逻辑。登记为遗留 |

## 发现与处置

1. **留空预算的 Mission 注定失败**（产品问题）：交给独立评审子代理裁决，结论为 C。Host 门口给空缺项补默认值，已在 `f51ddc37` 修复并原生复验；SDK 的 Task 预算下限记为 F-ORCH-1。见 journal §4.4。
2. **副本缺少 `model_overrides.toml`**（测试环境问题）：`launch.sh` 在复制数据时会写入 flash 的 32000 窗口。
3. **启动脚本在 bash 3.2 下遇到 `set -u` 加空数组就报错**，属于测试工具问题，已修。
4. **退出后立刻重启时，启动器探测端口报 `Address already in use`**：这是短暂的 TIME_WAIT，等端口可以绑定后再启动即可。
5. **SDK 观察项**：Mission 结束后，个别 Attempt 仍停在 `RETRY_WAIT`（F-ORCH-2）；已交付产物的 `verification_status` 仍是 UNVERIFIED（F-ORCH-3，待确认）。

## 结论

HA-12 ①–⑥ 全部 PASS。

P3.1 的退出门槛"一个 Mission 从创建到正式产物完整可操作"已在原生 verify bundle（debug .app 加源码后端）上达成。冻结打包的安装包（PyInstaller，spec 仍停在 0.6.4，见 F-ORCH-6）没有验证，所以对外的表述是"原生 verify bundle 验收通过，冻结安装包待验证"。
