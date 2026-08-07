# r5 交接状态（2026-08-06）

> **2026-08-06 下午更新:r5 收官——18/18 场景全部 PASS(台账 25 条 run,含红绿双记录)。**
> 本轮补跑 S10 真机半边 / S13 / S14 / S15 / S16 / S18,全部入账。
> 新修两个 B13 家族缺陷(见下),前端两处判据外发现已挂后台任务跟踪:
> ① 删除保留会话 default 后主面板幽灵消息流(task_74bf936a);
> ② provider 失败回合会话列表不实时刷新(task_76ff51df)。
> 改动尚未 commit(含 r5 全部修复 + 台账),等待用户决定提交时机。
>
> **本下午新修的两个缺陷(都有红绿判别证据)**:
> - **Cmd+Q 孤儿 backend**(S16 首跑 FAIL 抓到):Cmd+Q 不触发 main Destroyed,
>   backend 变孤儿占 8100。修复:lib.rs 改 .build().run(回调),RunEvent::Exit
>   统一兜底 kill_child。证据 r5-S16-cmdq-orphan.txt / r5-S16-manual.txt。
> - **supervisor respawn 代孤儿**(S13 跑后发现):respawn 分支不更新 child_pid,
>   PID 兜底杀的是死 PID。修复:process_manager.rs respawn 同步 child_pid。
>   证据 r5-respawn-pid-fix-verify.txt。
>
> S15 实测注:钥匙串(机器级)已有当日令牌,冷环境 onboarding 走完**未出现登录窗**,
> 直接复用连接——"S15 需用户登录"的预期本轮未触发。用的全新目录 .testenv/cold-S15-r5。

## 覆盖情况(历史,下午前):18 个场景中 13 个已有入账,5 个尚无任何 run

**脚本道 6/6 全绿**：S02 / S04 / S09 / S10 / S11 / S12
（S02 中途红过一次：新加的测试 mock 把历史常量 `message-panel-main` 硬编码到第二个文件，
触发「零残留」判据；改成占位值后转绿。红/绿两条记录都在台账里，可审计。）

**真机道已过 9 个**：S01 / S02 / S03 / S04 / S05 / S06 / S07 / S08 / S17

**还要跑的 6 项**
| 场景 | 说明 |
|---|---|
| S10 真机半边 | 脚本半边（window_geometry 单测）已过；真机需拖拽缩放+移动 → 重启核对恢复 |
| S13 后端未就绪 | 需阻塞后端启动，验 UI 降级态 |
| S14 托盘菜单 | 显示主窗/隐藏主窗/退出 三项文案与行为 |
| S16 退出路径 | 红钮 + 托盘退出，各验 app/backend/8100 全清（B13） |
| S18 会话删除边界 | 会把会话删光，跑完需重新造数 |
| S15 空态 | **放最后**：冷环境要走 onboarding + 登录，登录会重写钥匙串令牌 |

> S15 的登录需要在登录框输入账号密码 —— 这一步必须由用户本人完成，AI 不代输凭据。

## 本轮修掉的四个缺陷（都带判别力验证过的回归测试）

| # | 缺陷 | 归属 | 关键文件 |
|---|---|---|---|
| 1 | 新建会话永不进侧栏列表，切走回不去 | **改版引入** | `components/SessionList.tsx` |
| 2 | root turn 预约前失败 → 会话被 continuation 路由永久卡死（消息进黑洞） | 待定 | `code-panel/controlWs.ts` + `backend/main.py` |
| 3 | 切走再切回，用户气泡重复渲染 | 待定 | `stores/sessionsStore.ts` |
| 4 | `default` 会话被墓碑化后永久不可用 + 误导性错误码 | **既存** | `memory/session_db.py` + `backend/main.py` |

缺陷 4 的根因链：`clear()` 把任何被删会话的属主行墓碑化 → r3 S18「依次删除余下所有会话」
把兜底会话 `default` 也删了 → `bind_session_owner_if_absent` 恒抛
`companion_session_owner_tombstoned` → 被聊天分支宽泛 `except` 捕获 → 报成
`companion_identity_not_ready`。而删除当前会话后前端必然回落到 `default`，所以删过一次
就永久堵死。修法：保留会话只清内容不退役；存量墓碑行在绑定时删旧行重插自愈
（触发器禁止 UPDATE 复活，删除+插入合法，且消息早已清空）；真实异常原因经
`chat_v2_error.payload.detail` 与日志 `detail` 字段透出。

三处修复均已真机复验（见 `artifacts/r5-fixes-verification.txt`）：
`default` 属主行 tombstoned → active，聊天恢复 `chat/completions 200`。

## 测试基线

- 前端：75 文件 / 663 测试全绿
- 后端 companion + projection：660 通过，**9 条失败是既存基线**
  （`test_performance` 耐久车道 8 + `test_skill_pack_adapter` 清单哈希 1），
  已用 `git stash` 对照确认与本次改动无关
- `tsc --noEmit` / `cargo check` 干净

## 环境事实（不是缺陷，别误报）

1. **Rust 重新编译后首启会弹钥匙串授权**（应用二进制签名变了，三个槽位各弹一次），
   dev 期固有，签名发布不会有；普通重启不弹。
2. **反思后台链路用 `sf-glm-5.2`，该模型账号无额度** → 日志出现 402 Payment Required；
   kimi-k3 主链路全程 200，不影响任何断言。
3. **relay access token 寿命 1 小时**，后端侧无 refresh 能力（见待办任务）。挂机超过 1h
   后需先打开「更多 → 账户」面板触发前端 refresh，再重启让后端经 env 拿到新令牌。
4. 应用刚启动的几秒内 AX 可能读不到窗口（`count of windows` 返回 0），
   在窗口位置点一下即恢复，不是缺陷。

## 驱动器使用要点

`artifacts/drive.sh {geom|click dx dy|paste text|key code|shot name}`，坐标为窗口相对逻辑点。

- **中文粘贴必须走 `LC_ALL=en_US.UTF-8 pbcopy`**（已修进 drive.sh），否则中文进剪贴板变空串。
- **侧栏纵坐标随「会话」视图是否展开列表而变**：紧凑布局下技能中心=143 / 产物库=184 /
  设置=647；展开布局下这些项整体下移（技能中心≈531，或按实际列表长度变化）。
  **每步先截图定位再点击**，不要复用固定坐标。
- 列表滚动用 `mcp__macos-mcp__Scroll`；`osascript` 的 `scroll at` 在该容器上不生效。
- devtools：Cmd+Opt+I 打开（停靠窗口内），控制台输入行在窗口底部 ≈(200,687)；
  **先 `type "1+1"` 确认聚焦再粘贴**，直接粘贴常常落空。关闭点左上 ✕ ≈(15,214)。

## 入账命令模板

```
GATE=/Users/denny/.claude/skills/plan-test/scripts/plan_test_gate.py
RD=plans/2026-08-04-workbench-ui/verification/workbench-ui-20260806-r5
python3 $GATE record-timing   --run-dir $RD --phase manual-lane --task <SID> \
        --activity-class manual_e2e --command "<TC 步骤>" \
        --declared-start <t0> --declared-end <t1>
python3 $GATE attach-evidence --run-dir $RD --path artifacts/<file> --kind primary --scenario <SID>
python3 $GATE record-run      --run-dir $RD --scenario <SID> --kind root --result <pass|fail> \
        --lane manual-mcp --driver ai --command "<TC 步骤>" \
        --engine-terminal manual --business-terminal <pass|fail>
```

时间戳用 `date -u +%Y-%m-%dT%H:%M:%SZ`（本机 CST=UTC+8，别手写 UTC 日期，会撞
「declared-end 早于 declared-start」）。`activity-class` 合法值：
`automated_test / implementation / interruption_recovery / manual_e2e /
provider_wait / rework / user_wait`。

## 测试环境

- `.testenv/cold-A`：主测试环境，当前 30 个会话（26 个造数 + default/C/D/E），
  35 个产物文件。S18 会把会话删光，跑完需重新造数。
- `.testenv/cold-S15`：S15 空态专用冷环境（全新目录，要走 onboarding + 登录；
  登录会重写钥匙串令牌，所以 **S15 放最后跑**）。

## 待办任务（已建，不阻塞本 release unit）

1. 后端 `/v1/me` 无 token refresh 能力（改版前既存）——挂机 >1h 后身份永久失效，
   UI 停在「正在恢复身份…」无错误态无重登入口。
2. unix 外部 kill 应用会遗留孤儿后端（B13 修复只覆盖红钮/托盘两条 UI 路径，
   Windows 靠 Job Object 兜底，unix 无等价物）。
