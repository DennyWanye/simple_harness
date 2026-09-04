# S5B-S1 生产入口车道 —— **全链闭环 PASS**

Host HEAD `34ea5274`。驱动：`tools/production_entry_run.py` 对**真实运行后端**经控制
WebSocket 走 `primary.open → task_scope.create → binding.append → queue.enqueue`
（与将来 S6 界面按钮同一段代码 `memory/human_memory_api.py:207`），真实 provider
gpt-5.6-luna，自然用户语言：「把项目里 README.md 的版本号从 1.1.3 改成 1.2.0，改完告诉我新版本号」。
工作区 `~/SimpleHarnessWorkSpace/demo-project`（AUTO 模式下由 Agent 在既定 workspace 自建并绑定）。

## 全链业务终态：completed + valid
| 环节 | 结果 |
|---|---|
| 文件副作用 | README `version: 1.1.3` → **`1.2.0`**（真实模型经 `edit_file` 写入） |
| 客观事件 | `task_scope_events` **39 行** |
| 语义收口 | `task_scope_closure_receipts` **1 行，outcome=mutate** |
| 前台回合 | `foreground_turn_heads.current_state=**SETTLED**`（终态提交已执行） |
| Memory 摄入 | `memory_ingestion_outbox` **1 行**；`memory_ingestion_evidence_links` **1 行** |
| 记忆物化 | `cognitive_memory_heads` **1 行**，`source_kind=episode`，principal=deskpet-local-owner-v1 |
| SDK Run | 终态 `completed`；路由账本 1 行 `resume_existing / host_initial` |

## 为打通这条链修掉的既有缺陷（7 处，均非本轮引入）
| # | 缺陷 | commit |
|---|---|---|
| 1 | 首次 workspace binding 死锁 | `1206929d` |
| 2 | `ingress.start` 不传 conversation | `5cba5e9e` |
| 3 | 派生执行会话缺 `sessions` 行 → 身份绑定外键失败 | `1d9e5596` |
| 4 | context source 载荷缺 `provider_messages` | `1c5c8cc8` |
| 5 | Host 首轮路由回执不落账 → 首轮无活跃任务域 | `b08924d6` |
| 6 | `edit_file` 拒绝不透明 + 相对路径按进程 cwd 解析 | `26e6c1fb` |
| 7 | 授权决策落地后不唤醒前台驱动 → 终态提交永不执行 | `34ea5274` |

## 复现记录
业务动作（README→1.2.0）此前已独立复现 3 次（probe-10 / prod-lane-02 / prod-lane-04）；
**全链闭环（含 Memory 物化）本次为第 1 次**，需再跑 1 次取得 ≥2 独立 root。
