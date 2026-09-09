# 裁决备忘：设置面板「自动模式（推荐）」复选框初始状态与后端策略相反（MM-D4）

- 日期：2026-09-09
- 事故：Manual 模式旅程 run5b（2026-09-09 12:45），记录 `plans/2026-09-09-manual-mode-journey/RUN-04-PARTIAL.md` 尾部
- 相关裁定：`DECISION-MM-D1-D2.md`（授权策略唯一权威 = `workflow.db.authorization_policy_state`）
- 分支：`worktree-auto-mode-checkbox`（基线 main `6ff7fb46`）
- 口径：与 MM-D1 一致——**`sdk-product-state.db` 里的同名表是 DDL 残留，任何读路径都不得读它**；
  本轮不改后端权威、不改 CAS 语义、不改 `PermissionGate` 判定，只修「界面读什么、写什么」。

---

## 一、现象（run5b 逐条）

全新 userdata，后端 `workflow.db.authorization_policy_state` = `auto / generation 0 / factory_default`。

| # | 动作 | 界面 | 后端 |
|---|---|---|---|
| 1 | 打开『模型与设置』→ 权限区 | 复选框 **未勾选**（AX value 0），持续 ≥8 s | `auto / gen 0 / factory_default` |
| 2 | 第一次点击 | 变为已勾选（1） | **未变**，仍 `auto / gen 0` |
| 3 | 第二次点击 | 变为未勾选（0） | `manual / gen 1 / user_explicit` |

即：**初始渲染与后端相反**，且**第一次点击对后端是空操作**——用户以为自己"关掉了自动模式"，
实际上第一次点击只是把界面对齐到后端本来就有的 auto，第二次点击才真正写入 manual。

## 二、根因（三处叠加，全部在前端读写路径）

以下行号为修复前的 main `6ff7fb46`。

### 2.1 初始值来自 localStorage 显示缓存，而不是权威策略

`tauri-app/src/components/SettingsPanel.tsx:453-459`：

```ts
const [enabled, setEnabled] = useState<boolean>(() => {
  try {
    const cached = localStorage.getItem("deskpet.auto_mode");
    return cached === null ? true : cached === "true";
  }
  catch { return true; }
});
```

复选框一挂载就带着一个**猜测值**并且**立即可交互**。webview 的 localStorage 与后端 userdata
是两套生命周期：清掉 userdata 不会清掉缓存，run1~run5 里任何一次把开关关到 manual 都会在
缓存里留下 `"false"`，于是 run5b 的全新后端（auto）配上旧缓存（manual）= 界面与后端相反。
`tauri-app/src/components/CapabilityCenterPanel.tsx:95-102` 是同一个缓存键的另一个读者。

### 2.2 权威快照的请求会被静默丢帧，且永不重发

`SettingsPanel.tsx:466-487` 只在 `getChannel()` 返回 null 或 `send` **抛异常**时重试。
但 `tauri-app/src/ws/ControlChannel.ts:164-179` 的 `send()` 在 socket 不是 `OPEN` 时
**不抛异常**——它只把非 `chat_v2` 的帧丢掉并返回 `false`：

```ts
if (this.ws?.readyState === WebSocket.OPEN) { ...; return true; }
if (identified.type === "chat_v2") this.queue(serialized);
return false;
```

设置页在通道对象已存在、socket 仍在 CONNECTING/重连时挂载，`permission_auto_mode_get`
就此丢失，没有任何重试、也没有 `onStateChange` 上的重发钩子。界面于是**永远**停在 2.1 的
缓存值上——这正是"≥8 s 不变"的解释。

### 2.3 回执走 App 级单槽 `lastMessage`，且写入是"翻转我以为的当前值"

`SettingsPanel.tsx:489-496` 从 props 里的 `lastMessage` 读回执，而
`tauri-app/src/hooks/useWebSocket.ts:11,18` 只保留**最近一条**控制消息
（`channel.onMessage(setLastMessage)`）；同批到达的其它控制消息会把回执挤掉。同文件里
`ChatTurnTimeoutSetting`（:552 起）用的才是正确写法：`ch.onMessage(...)` 订阅 + `request_id` 关联。

写入路径 `SettingsPanel.tsx:498-511`：

```ts
const next = !enabled;                       // ← 基于可能过期的本地值
ch.send({ type: "permission_auto_mode_set", payload: { enabled: next } });
setEnabled(next);                            // ← 乐观写，不等落盘
```

`!enabled` 把"用户想要什么"翻译成"翻转我以为的当前值"。当本地值本来就与后端相反时，
第一次点击必然写成后端已有的模式（CAS 里 `current.mode == requested` 直接短路，
`backend/main.py:2993-3018`），也就是 run5b 观察到的空操作。

### 2.4 后端读源本身是对的

`backend/main.py` 的 `_authorization_auto_mode()`（旧 :3022-3029）读的是
`service_context.get("capability_store")`，而该 store 在 :2712/:2935 由
`uow = workflow_service.execution_uow` → `CapabilityStore(uow)` 建立并注册——**就是 workflow.db 权威**，
没有读残留表。所以这次的缺陷**纯在前端**；后端只需要把回执补全到够渲染。

## 三、修复

### 3.1 前端（`SettingsPanel.tsx`，`AutoModeToggle` 重写并导出）

- **不再有默认值**：状态是 `policy: {mode, generation, provenance} | null`，`null` = 加载中。
  加载中复选框 `disabled` 且不勾选，状态行显示「正在读取授权策略…」——**任何点击都不可能被
  解释成对某个默认值的翻转**；`onToggle` 里再兜一层 `if (!policyRef.current) return;`。
- **localStorage 只写不读**：缓存仅为 `CapabilityCenterPanel` 的占位展示保鲜，不再参与本开关的初始状态。
- **订阅代替单槽**：`ch.onMessage()` 订阅回执 + `ch.onStateChange()` 在 `connected` 时重发 get；
  挂载时先用 `ch.getLatestMessage("permission_auto_mode_response")`（通道按类型缓存的最新一帧）渲染。
- **检查 `send()` 返回值**：`false` 即丢帧，500 ms 后重发 get，不再"发一次就算数"。
- **显式目标模式**：`onToggle(event)` 取 `event.target.checked` 作为目标，发
  `buildAutoModeSetMessage(target, requestId)`——即 `set_auto_mode(true|false)`，不再是 `!enabled`。
- **写后按落盘状态渲染**：不做乐观写；回执到达前显示「保存中…」，到达后显示
  「当前：自动/手动（策略代次 N · provenance）」。
- **代次单调**：`generation` 比当前小的快照被丢弃，迟到的旧 get 回执不能把界面拉回写前状态。

### 3.2 后端（`backend/main.py`）

- 新增 `_authorization_policy_snapshot()`：从 `CapabilityStore`（workflow.db 权威）读出
  `{enabled, mode, generation, provenance, authoritative}`；无 store 时降级并标 `authoritative: false`，
  不谎称权威。`_authorization_auto_mode()` 改为委托它，读源只此一份。
- `permission_auto_mode_get` / `permission_auto_mode_set` 两个 handler 都回发该快照并回显
  `request_id`。**`set` 不再把请求里的 `enabled` 原样回显**，而是写入后**重读**落盘状态
  （与 `chat_turn_timeout_*` 的既有形状一致）。
- `enabled` 字段保留，纯为线上兼容；新增字段是 additive。

## 四、控制（本轮只跑触及文件）

| 套件 | 例数 | 结果 |
|---|---|---|
| `tauri-app/src/components/SettingsPanel.autoMode.test.tsx`（新） | 8 | 绿 |
| `tauri-app/src/components/permissionMode.test.tsx`（改写） | 2 | 绿 |
| `tauri-app/src/components/PrimaryTaskPanel.test.tsx`（回归锁：TaskScope 面板无 Manual/Auto 开关） | 5 | 绿 |
| `SettingsPanel.timeout` / `retiredSupervisor` / `dataDir` / `CapabilityCenterPanel` | 12 | 绿 |
| `backend/tests/test_mmd4_auto_mode_policy_snapshot.py`（新） | 4 | 绿 |
| `backend/tests/test_permission_mode_migration.py` + `test_p4s21_permission_gate_auto_mode.py` | 10 | 绿 |

合计前端 27 例、后端 14 例全绿；`npm run typecheck` 通过。
`eslint` 在 `SettingsPanel.tsx` 上 6→9 条 `react-refresh/only-export-components`，
全部来自新导出的三个纯函数，与既有 `buildChatTurnTimeout*` 同因同类，未新增其它规则违例。

新增用例锁的是行为而非文本：`permissionMode.test.tsx` 原来锁的字符串 `cached === null ? true`
正是 2.1 的病灶，改为锁「设置面板不从缓存取初始值 + 加载态不可交互 + 写入携带显式目标」。

## 五、边界（本轮没做的事）

- `CapabilityCenterPanel` 的 `readCachedAuthorizationMode()` 未改——它是能力中心的占位展示，
  不是权限开关；若后续要统一，应让它也订阅快照。
- 未启动原生应用。前端改动需要重新打包 bundle
  （`.local-test-evidence/2026-09-07/native-build-r8/build.py`），由用户执行后重跑 run5c 复验
  MM-D4：全新 userdata 打开设置页 → 复选框应**已勾选**（auto/gen 0），第一次点击即写入
  `manual / gen 1 / user_explicit`。
- 分支未合回 main。
