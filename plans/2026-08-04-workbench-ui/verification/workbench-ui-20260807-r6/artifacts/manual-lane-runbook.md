# r6 补注（2026-08-07）
- 基线 9a19261（干净树）。r5 记账教训：证据一律 `--ui-action`（UI 场景主证据）；
  不应创建 Run 的场景补 `--negative-assertion` 证据；应创建 Run 的场景 record-run
  加 `--run-id-under-test`（从 backend log chat_v2_run_reserved 取）；`--depends-on`
  只能引用 evidence id，不能用路径。
- 执行序：token 刷新 → S04 → S05 → S18 → seed → S17 → S02 → S03 → S06/07/08 →
  S10 → S13 → S14 → S16 → S01(冷dir) → S15(冷dir，放最后)。
- run-creation 证据：真机道开始前 dump sessions/messages 基线，收尾出 r6-run-creation-evidence.txt。

# r5 真机道执行清单

驱动器：`drive.sh {geom|click dx dy|paste text|key code|shot name}`，坐标一律窗口相对逻辑点。
窗口基准 1000x700。侧栏坐标：会话(60,104)、技能中心(75,143)、产物库(65,184)、
更多(47,609)、设置(61,647)；更多展开后账户图标(34,607)。
输入框(560,642)，新建会话(122,140)，会话列表首项(150,180)、次项(150,230)、第三项(150,285)。

> 注意：`paste` 必须走 `LC_ALL=en_US.UTF-8 pbcopy`（已修进 drive.sh）——否则中文进剪贴板变空串。

## 执行顺序与理由

令牌有效期 1 小时，需要 LLM 链路的场景优先。破坏性场景（删会话、退出应用）靠后。

| # | 场景 | 需要 | 备注 |
|---|---|---|---|
| 1 | S04 chat+companion | LLM + devtools | 主往返 + `get_window_control_credential` canonical 调用 |
| 2 | S05 会话列表 | LLM | 重点复验：新建会话**不重启**即进列表顶部 |
| 3 | S18 会话删除边界 | LLM | 重点复验：删除当前会话后切到 default 仍能正常发消息（原黑洞） |
| 4 | S17 规模长文本 | 造数 | 先跑 `seed-scale-fixture.py` 补到 30 会话 + 120 字符长标题；产物 35 个已在位 |
| 5 | S01 窗口形态 | — | 最小尺寸钳制、三系统按钮、Dock、transparent=false |
| 6 | S02 单窗形态 | — | AX 窗口枚举 + CGWindow 离屏面排查（swift 脚本） |
| 7 | S03 四视图导航 | — | 十轮切换 |
| 8 | S06/S07/S08 | — | 技能中心/产物库/设置三视图 |
| 9 | S10 几何记忆 | 重启 | 拖拽缩放+移动 → 重启恢复 |
| 10 | S13 后端未就绪 | 特殊启动 | 阻塞后端启动，看 UI 降级态 |
| 11 | S14 托盘菜单 | — | 显示/隐藏主窗/退出文案 |
| 12 | S16 退出路径 | 销毁 | 红钮 + 托盘退出，各验 app/backend/8100 全清 |
| 13 | S15 空态 | 冷环境 | `.testenv/cold-S15` 全新目录，走 StartupOverlay + onboarding + 登录 |

S15 放最后：它要重新登录，会重写钥匙串令牌。

## 每个场景的入账三件套

```
python3 $GATE record-timing   --run-dir $RD --phase manual-lane --task <SID> \
        --activity-class manual_ui --command "<TC 步骤>" --declared-start <t0> --declared-end <t1>
python3 $GATE attach-evidence --run-dir $RD --path artifacts/<file> --kind primary --scenario <SID>
python3 $GATE record-run      --run-dir $RD --scenario <SID> --kind root --result <pass|fail> \
        --lane manual-mcp --driver ai --command "<TC 步骤>" --engine-terminal manual --business-terminal <pass|fail>
```

时间戳用 `date -u +%Y-%m-%dT%H:%M:%SZ`（本机 CST=UTC+8，别手写 UTC 日期，会撞
"declared-end 早于 declared-start"）。

## 已知环境事实（不是缺陷，别误报）

- Rust 重新编译后首启会弹钥匙串授权（应用二进制签名变了），dev 期固有，签名发布不会有。
- 反思后台链路用 `sf-glm-5.2`，该模型账号无额度 → 日志里会出现 402 Payment Required；
  kimi-k3 主链路正常 200，不影响任何断言。
- companion pytest 9 条既存失败（test_performance 耐久车道 8 + skill 清单哈希 1），
  与本次改动无关，已用 stash 对照确认。
