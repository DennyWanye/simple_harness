# r8 当前阻塞点（2026-08-08 04:15）

## 状态
- 脚本道 **6/6 全绿**（S02/S04/S09/S10/S11/S12），已入账
- 真机道 **0/15**，卡在第一个场景 S01 的冷启动
- `finalize --check-only` = NOT_READY —— **这是预期的**，r8 正在进行中，不是失败

## 阻塞原因：需要用户输入钥匙串密码（AI 不代输）
用全新隔离目录 `.testenv/cold-S01-r8` 冷启动后，macOS 弹出：

> "simple-harness" 想要使用你储存在钥匙串中的 "deskpet-relay" 中的机密信息。
> 若要给予许可，请输入"登录"钥匙串的密码。
> [?] [始终允许] [拒绝] [允许]

该弹窗未获授权即消失（SecurityAgent 窗口数归 0），结果：
`app=1 backend=0 port8100=0 主窗=0`，日志只有
`get_shared_secret failed: No secret available (backend not started?)`，
**`[backend_launch]` 一行都没有** —— relay 密钥没拿到 → backend 没起 → 无窗口。

已清理：app/backend/port/vite 全部归 0；半途的 `.testenv/cold-S01-r8` 已删除
（S01 要求全新冷数据，脏目录会污染"首启居中"断言）。

## 续跑需要什么
1. 用户在弹窗出现时点 **「始终允许」**（不要点「允许」）并输入登录钥匙串密码。
   r8 真机道 15 个场景涉及几十次重启，点「允许」会每次重弹，把整轮拖长。
2. 授权后重跑 `artifacts/launch-cold-s01.sh`，等 `[backend_launch]` 出现、
   `drive.sh geom` 返回有效几何，再按 TC-WB-01 步骤 1-7 取证。

## 未在账本记 BLOCKED 的理由（如实说明）
S01 **没有**记 `blocked` run。这个阻塞是"等用户点一下"的临时性阻塞，不是
"判据前置结构上不可得"。r7 就是因为 S06 记了 blocked 而永久无法 finalize
（`compute_scenario_status` 里 BLOCKED 同样让 required 场景不达标），
不能为了让 Stop 门好看就把临时阻塞写成永久结论。

## 附：r3–r7 出现 FROZEN_ORACLE_CHANGED 是预期的
本轮按 WBUI-BC-04 修改了冻结 testcase
`testcase/workbench-ui/TC-WB-04-chat-roundtrip-companion.md`（"全绿(0 failed)"
→ "与基线等值"）。r3–r7 的 testcase_lock 记的是旧内容且它们的 manifest 里
没有 BC-04，故报 `FROZEN_ORACLE_CHANGED`。**r8 自身不报**（init 在改动之后，
BC-04 已登记）。r3–r7 都是已被取代的轮次（r6 有永久 root FAIL、r7 有 BLOCKED），
待 r8 finalize 后用 `retire --superseded-by r8` 正当退役，该诊断随之消解。

---

# 更新（04:20）：出现第二个、更严重的症状 —— 应用起不来了

## 现象（cold-A 与全新冷目录都一样，与钥匙串无关）
`launch-app.sh` 后：
- Rust 侧**跑到了**：日志有 `apply_saved_geometry: loaded 1260x840 logical pos=Some(420),Some(232)`
  → `set_size OK` → `on_move` → `flushing ... saved OK`
- 前端**加载了**：有 `[vite] (client)` 输出
- 但 `[backend_launch]` **一行都没有**，`backend=0 port8100=0`
- 前端只报 `get_shared_secret failed: No secret available (backend not started?)`
  之后日志在 04:16:58 **彻底静默**
- `count of windows` = **0**，全屏截图只有桌面壁纸
- 第二次冷启动（`.testenv/cold-S01-r8`）症状完全相同 ⇒ **不是钥匙串导致的**

## 已排除
- 不是 gpu_check：`lib.rs:123` 的 NVIDIA 前置检查是 `#[cfg(target_os = "windows")]`，
  macOS 不走，日志也无 `[setup] gpu_check failed`
- 不是编译未完成：`Finished dev profile in 0.35s` = 无改动待编，二进制已含改动
- 不是窗口跑到屏幕外：存盘几何物理 (420,232)+2520x1680 = 逻辑 (210,116)+1260x840，
  正好贴合 1470x956 屏幕，`clamp_position_to_screen` 无需移动
- 不是 launch-app.sh 的 env 丢了：DESKPET_USER_DATA_DIR / DESKPET_BACKEND_DIR /
  DESKPET_DEV_MODE 都在

## 关键线索：backend 是**前端**启动的，不是 Rust setup 启动的
`tauri-app/src/App.tsx:130` → `core.invoke("start_backend")`；失败会走 :142
`console.warn("[bootstrap] start_backend failed:", msg)`。
日志里**既没有** `[backend_launch]`（Rust 侧 spawn 的打点）**也没有**
`[bootstrap] start_backend failed`（前端侧失败打点）
⇒ 前端 bootstrap 那段 effect **根本没执行到 invoke**，或执行了但两条打点都没落到这个日志。

## 嫌疑与下一步（**未验证，不下结论**）
自 `b68e958`（品牌修复）之后**从未成功启动过应用** —— r8 脚本道只跑构建/单测，
没起过应用；最后一次成功启动是 r7 期间（b68e958 之前）。故 b68e958 是首要嫌疑。
但我的改动只有字符串字面量（backend_launch.rs / gpu_check.rs / process_manager.rs
各改对话框文案，前端改 UI 文案），**理论上不该影响启动路径**，所以不排除是
环境/缓存问题或与本次改动无关的既有问题。

**必须先做的对照实验**（在下结论前）：
```
git worktree add --detach <tmp> 33f6b5f^   # = b68e958^，品牌修复之前
# 在该 worktree 起应用，看 [backend_launch] 是否出现、窗口是否可见
```
若基线正常而 b68e958 异常 → 是回归，逐文件二分定位；
若基线同样异常 → 与品牌修复无关，是环境问题（Vite 缓存 / target 缓存 /
node_modules / macOS 权限），按环境路线排查。

## r8 现状
脚本道 6/6 绿；真机道 0/15，**因应用起不来而无法推进**。
仍未在账本记 blocked（理由同上：blocked 在 `compute_scenario_status` 里同样是
永久粘性，会让 r8 步 r7 后尘永久收不了尾）。

---

# 更正（04:27）：屏幕处于锁定状态 —— 上面"怀疑 b68e958 回归"很可能是错的

## 实测
```
ioreg -n Root -d1 -a | grep -A1 CGSSessionScreenIsLocked
  → <true/>
```
**现在（04:27）屏幕是锁着的**，而三次失败启动发生在 04:09–04:23。

（注：`CGSSessionScreenLockedTime` **不是 Unix epoch**，按 epoch 换算得到未来时间
05:14，属误读，不能用它推锁屏时刻。此处只采信"当前已锁"这一可靠事实。）

## 锁屏可以解释全部症状，无需假设代码回归
- 全屏截图只有壁纸、**连 Chrome 等其它应用窗口都没有** —— 锁屏时 WindowServer
  不合成用户窗口，这条之前被我忽略了，它才是最关键的线索
- `count of windows` = 0 —— 同上，AX 在锁屏下取不到窗口
- 钥匙串弹窗"未经授权自己消失" —— 会话锁定时系统会撤下该弹窗
- **WebView 被挂起 → `App.tsx` 的 bootstrap effect 从未执行 → 没有
  `invoke("start_backend")` → 既没有 `[backend_launch]` 也没有
  `[bootstrap] start_backend failed`** —— 这正是之前想不通的那一点
- Rust 侧的 window_geometry 打点照常输出 —— 那段在 setup 里跑，不依赖 WebView

## 结论修正
**撤回**"`b68e958` 品牌修复是首要嫌疑"。当时的推理是"自 b68e958 后从未成功启动过"，
但那段时间恰好也是机器进入锁屏的时间窗，两者混淆了。**不要**据此去二分排查
b68e958，会白白浪费时间去找一个可能不存在的回归。

## 决定性验证（需要用户）
1. **解锁屏幕**（这一步 AI 做不了）
2. 重跑 `artifacts/launch-app.sh`，观察：
   - `[backend_launch] Dev python=... backend_dir=...` 是否出现
   - `backend=1 port8100=1`、`drive.sh geom` 是否返回有效几何
3. 若正常 → 之前三次失败纯属锁屏，r8 真机道直接继续，**无需任何代码改动**
4. 若仍异常 → 那时才回到 b68e958 对照实验（`git worktree add --detach <tmp> 33f6b5f^`）

## 附带教训（值得写进驱动纪律）
真机 UI 测试前**必须先断言屏幕未锁**，否则所有 AX 查询、截图、窗口计数全是
无效观测，而且失败形态酷似"应用起不来"，极易误判成代码回归。
建议加进 `drive.sh` 的前置硬闸：
```
ioreg -n Root -d1 -a | grep -q "CGSSessionScreenIsLocked" && \
  { echo "SCREEN_LOCKED: 观测无效，先解锁" >&2; exit 3; }
```

---

# 已解除（08:10）：解锁后一次通过，确认无代码回归

解锁屏幕后重跑 `artifacts/launch-app.sh`：
- 锁屏硬闸放行（不再 exit 3）
- **`[backend_launch] Dev python=.../backend/.venv/bin/python backend_dir=.../backend`
  于第 27s 出现**
- backend 第 15s 就绪：`app=1 backend=1 port8100=1`
- `drive.sh geom` → `210 116 1260 840`（有效几何，exit 0）

⇒ 04:09–04:23 那三次"应用起不来"**全部是锁屏导致的假象**，
   `b68e958`（品牌修复）**确认无回归**，未改一行代码即恢复正常。
⇒ 上面"更正（04:27）"的判断得到实测证实；本文件的阻塞状态到此解除。

r8 真机道从 S01 开始正式推进。
