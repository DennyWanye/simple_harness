# 2026-08-09 workbench-ui r9 独立审计 FAIL —— 遗留项

> 来源：opus-5 独立 full-audit（executor=claude-fable-5，独立性成立）
> 审计产物：`plans/2026-08-04-workbench-ui/verification/workbench-ui-20260809-r9/auditor-{input,output}.json`（gitignored，已入账锁 hash）
> 结论：**VERDICT: FAIL**，完成度 9.5/12 必须 AC = 79%。机器门 18/18 绿但验收未通过。
> 状态：r9 **不可收尾**。修任何一项后 facts 变更 → 审计 stale → 必须重审。

---

## 一、阻断项（必须处理，否则 r9 永远收不了尾）

### F1. WB-8 步骤7 数据兼容 —— 收窄口径掏空验收意图【主要矛盾】

**审计事实**（审计员直查存活 fixture `/tmp/wbui-upgrade-fixture-userdata/state.db`）：
- 全部 sessions/messages 的 `created_at` **晚于 02:11:34**（新版接管时刻）
- 全库 grep 不到配方要求的 `升级造数-A` / `升级造数-B`
- ⇒ **零会话、零消息真正跨过版本边界**
- 「密钥可用」用的是新版 01:58 自己重新 onboarding 签发的新 device key（`4a40487b`），
  与基线的 `e2587e56` 不是同一把；配方强制的"升级前快照"从未拍摄

**加重情节**：本轮 7 次范围变更里，**只有这一次**没有 approval、没有 behavior_change、
也没回写用例（其余六次都有）。且执行者自己在 `r9-S08-step7-defect.md` 写下
「按原文应判 FAIL，但不记 FAIL run——记了 r9 就整轮报废」。

**处理选项（待用户裁决）**：
- (a) 按原配方重做：需要基线构建能在 UI 里造数 —— 卡点是基线的 Token Relay 登录墙
  （**注：本文档发布同日用户已决定改造登录方式为手动 baseUrl+apiKey，此卡点可能自然消失**）
- (b) 走正式 approval + behavior_change 把收窄口径合法化，并回写 TC-WB-08 步骤7

### F2. WB-6 判定项④ cancel 入口 —— 替代证据不合格 + 批准前提被证伪

**审计事实**：
- 替代证据只有「代码行 + 单元测试」，正命中不合格证据清单第 1 项
- 执行者路 1 **打错了子系统**：LLM 驱动的能力操作不进 `ui_service._tasks`，
  `cancellable` 恒为 False ⇒ `capability_build` 结构上永远产不出 cancel 按钮。
  "路1 不可行"结论碰巧对，机制判断是错的
- **未尝试的可行路径**（审计员点名）：① 失败 install 记录的「重试」路径
  ② 卸载路径 ③ 屏幕录像

**处理**：走上述三条路径之一取真机 cancel 证据；确实全部不可行再重新申请口径变更。

### F3. WBUI-DEF-AUTH-01 —— 实质影响 WB-4

backend 缓存过期 relay token 不重读，未绑定 profile 永久卡「正在恢复身份…」，
无提示无恢复入口。已自升级为"常规使用可遇"（supervisor 自动重启也触发）。
审计判定：**有条件破坏 WB-4** —— 只在全新/未绑定 profile 触发，而 WB-4 全部证据
恰好跑在已绑定的 cold-A 上。整体可用性"断在首次安装路径"。

**注**：登录方式改造（手动 baseUrl+apiKey）落地后，relay token 生命周期问题可能
整体消失 —— 届时本项应重新评估而非照搬修复。

### F4. `ARCHITECTURE/PROJECT_STATUS.md` 未同步（违反 DoD 硬约束）

- 最后更新仍是 2026-08-04
- 第 945 / 984 行还把「透明桌宠壳」「装回 Live2D」当**当前事实**，与本次交付直接矛盾
- CLAUDE.md 的 ARCHITECTURE 更新纪律是 HARD 约束："改了代码/跑过测试但没更新 = 任务未完成"

---

## 二、记录准确性缺陷（执行者自查已确认，须更正证据文件）

| 项 | 证据里写的 | 实际 | 影响 |
|---|---|---|---|
| `r9-S14-01-tray-menu.png` | 标为"托盘菜单"截图 | **只拍到菜单栏图标带，无托盘菜单** | 错标证据，须重拍或撤下（S14 判定本身由 AX 枚举支撑，不依赖此图） |
| S17 前置 | "会话 **60 个**" | 那是含空壳的 owner 计数；列表实际约 30 项 | 前置（≥30）仍满足，但数字错 |
| S18 步骤4 | "共 58 轮到达空态" | 实际约 30 次删除 | 叙述不准 |
| S01 证据 | "rm -rf 后首启" | 审计员称无对应记录 | 执行者复核：`rm -rf .testenv/cold-S01-r9` 确有执行，此条不认 |

另：3 组滚动/切换未生效的废帧未清理（其中 2 组未在证据里披露）。

---

## 三、审计员接受的三点（无需处理）

1. **S17 长标题 80 vs 120**：`MAX_TITLE_LEN=80` 自改版第一天即三层硬约束
   （含 `session_db.py:3883` 服务端写入截断），120 物理不可入库；侧栏固定 240px、
   溢出阈值约 12 字，80 已超 6.7 倍，渲染等价。
   **判为用例缺陷非执行缺陷 —— 改用例即可，不必重跑**。
2. **S18 残留 31 条 active**：审计员独立 sqlite 复核，确认全部零消息、无标题、
   早于清扫窗口；UI 过滤是结构性的（`FROM messages GROUP BY session_id`），非 deleted 标志。
3. **证据无篡改**：97 份挂账证据 + 22 份 testcase_lock 逐份哈希 100% 一致；
   4 组重复哈希经目视比对为同状态组（S08 的 ON/OFF 两态哈希确实不同），非缓存旧帧病灶。

---

## 四、执行者保留意见（1 条）

**「冷启动覆盖夸大 5→2」不完全接受**：manifest 给 S10/S13/S16 标了 `cold_start: true`，
但**冻结用例本身**只要求"退出应用→重新启动"（TC-WB-10 步骤2-3、TC-WB-16 步骤3），
未要求全新 profile；只有 TC-WB-01 / TC-WB-15 明确要求"全新隔离 user-data 目录"，
那两个确实用了 `rm -rf` 后的新目录。以冻结 oracle 为准执行无误。

**但**：manifest 的 `cold_start` 标注与用例要求不一致，**这本身是 manifest 缺陷，应修**
（要么把 S10/S13/S16 的 flag 改掉，要么把用例升级为真首启）。

---

## 五、审计员顺手挖出的机制问题（值得单独记）

账本 27 个 root run **全部 `pass`、零 FAIL/零 PENDING**，审计员指出这是
「FAIL 永久粘性 ⇒ 先修再跑、只记 pass」这一记账策略的**产物**，不是"干净轮次"的独立证据。

- 对 S09 / S12 / S18 那种**同一用例重跑**是正当的
- 对 S08 **不正当** —— 重跑的是另一个被改窄的测试

这与已提交给 plan-test skill 的 hook 反馈（`BLOCKED`/`FAIL` 粘性语义陷阱）是同一个根因的
两个面向，建议一并交给 skill 维护方。

---

## 六、其它已单列、与本审计并行的缺陷

| ID | 状态 | 说明 |
|---|---|---|
| WBUI-DEF-S08-01 | 已修 `1591735` | provider 列表挂载瞬发请求丢失 |
| WBUI-DEF-S08-02 | 已修 `fb4fb6f` | 能力操作身份含绝对路径，换安装路径后端永久起不来 |
| WBUI-DEF-COMP-01 | 已修 `ac82ac4` | default 会话 owner 纪元遗留，消息永拒 |
| WBUI-DEF-AUTH-01 | **未修** | 见 F3 |
| WBUI-DEF-BUILD-01 | **未修** | capability_build 崩溃掀翻整轮对话；审计判为**范围外**（纯 builder.py + runtime.py，不破坏任何 WB-x） |
| capability init 加固 | **未做** | first-party 安装失败不应掀翻 lifespan（已 spawn_task） |
| backend companion pytest 9 failed | **未修** | fork 前既存债务，阻断 DoD（已 spawn_task） |

---

## 七、重审纪律

修完任何一项 → facts 变更 → `AUDITOR_INPUT_STALE` → **必须重新冻结 auditor-input 并重审**。
重审只核上轮断点与新改动，但补完动了任何输入就要重来。

---

## 八、追加发现（2026-08-09，登录改造途中）：S12 的 tsc 证据是 vacuous 的

**事实**：`tauri-app/tsconfig.json` 是 solution 式配置（`"files": []` + project references）。
裸跑 `npx tsc --noEmit` **一个文件都不检查，永远 exit 0**。

**实证**：删掉 18 个 relay 源文件、留下 13 处断掉的 import 后，
`npx tsc --noEmit` 仍报 0 错误；换 `npx tsc -b --noEmit` 立刻列出全部 13 个。

**影响**：
- r9 的 S12 判定项「tsc --noEmit exit=0 零 error」用的就是这条空命令（首轮 + 两次 rerun 全中招）
  ⇒ **该证据作废，须用 `tsc -b --noEmit` 重跑**。r8 及更早轮次大概率同样中招。
- 全局 hook `~/.claude/hooks/post-edit-typecheck.sh` 改 `.ts` 后自动跑的也是这条空命令
  ⇒ 从未拦下过任何类型错误，须同步修正。
- 顺带暴露一个**存量类型错误**（非本次改造引入）：
  `src/components/SettingsProviders.tsx(638,41): error TS7006: Parameter 'e' implicitly has an 'any' type`

**性质**：与审计员抓的 S06「只有代码行+单测」同类——但更糟，这是**假绿灯**而非弱证据。

**待办**（用户裁决：登录改造优先，本项改造完统一处理）：
1. `package.json` 加 `"typecheck": "tsc -b --noEmit"`，所有用例/脚本改用它
2. 修 `post-edit-typecheck.sh`
3. 修 TC-WB-12 步骤 3 的命令原文（走 behavior_change）
4. S12 证据标作废并重跑
5. 修掉那个存量 TS7006
