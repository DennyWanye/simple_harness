# WBUI-DEF-S08-02 —— 换安装路径后后端永久起不来（能力操作身份含绝对路径）

发现于：r9 / WBUI-S08-settings-view / TC-WB-08 步骤7（数据兼容·升级配方）
发现时间：2026-08-09
严重度：**高** —— 后端 lifespan 直接失败，端口不绑，应用对该 user-data 目录永久不可用，UI 无任何恢复入口。

## 复现

1. 用**安装路径 A** 的构建以 `DESKPET_USER_DATA_DIR=$D` 冷启动一次（会装 17 个 first-party 能力包）。
   本轮 A = `/tmp/wbui-baseline`（644ab16 worktree），`$D` = `/tmp/wbui-upgrade-fixture-userdata`
2. 关掉。
3. 用**安装路径 B** 的构建以同一个 `$D` 启动。
   本轮 B = `/Users/denny/projects/simple_harness`（HEAD=1591735）
4. 后端启动失败，8100 无监听（实测轮询 30s 持续 listen=0），前端停在
   「聊天通道已断开／正在恢复身份…／未连接」，截图 r9-S08-step7-connected-win.png

## 崩溃链

```
starlette lifespan
  → backend/main.py:6277  lifespan
  → backend/main.py:3844  _initialize_capability_runtime()
  → deskpet/capabilities/platform.py:2520  platform.initialize() → self.manager.install(...)
  → deskpet/capabilities/manager.py:932    manager.install()
  → deskpet/capabilities/store.py:5500     create_operation()
  → deskpet/capabilities/store.py:5465     raise CapabilityStoreConflict(
        "operation_idempotency_conflict",
        "operation identity already belongs to another request")
```

## 根因

`store.py:5450-5467` 的幂等复用检查：命中同一个 `idempotency_key` 后，逐字段比对身份，
其中一项是 `canonical_json(dict(record.request)) != request_json`。

而 `request.source.uri` 里存的是**绝对文件系统路径**：

| | idempotency_key | request_json.source.uri |
|---|---|---|
| 路径 A 写入 | `first-party:skill-doc-edit:0.1.0:587163cb…` | `/private/tmp/wbui-baseline/capability-packs/skill-doc-edit` |
| 路径 B 重算 | `first-party:skill-doc-edit:0.1.0:587163cb…`（**相同**） | `/Users/denny/projects/simple_harness/capability-packs/skill-doc-edit` |

idempotency_key 是内容哈希（pack id + 版本 + manifest hash），**与路径无关**，所以两边一致 → 命中旧记录；
但 request 里带了路径 → 身份比对不等 → 冲突 → 整个 lifespan 挂掉。

排除的其它解释（都实测排除，不是猜）：
- **不是中断留下的脏数据**：`capability_operations` 17 条全部 `status='succeeded'`，无在途记录。
- **不是"有既存记录就会撞"**：cold-A 有 24 条（22 succeeded + 2 failed），新版天天启动正常。
- **不是 schema 迁移**：两边 `workflow_schema_migrations` 最大版本都是 29。
- **不是包集合变了**：两个工作树 `capability-packs/` 都是同样 19 个目录；17 个已装包的
  `pack_id` / `idempotency_key` / `operation_id` 逐一比对**全部相同**。
- **不是 pack 内容变了**：内容变了会得到新 idempotency_key，走插入而非冲突。

## 影响面（比本用例大）

触发条件是「同一份 user-data 目录被两个**安装路径不同**的构建先后打开」，不限于版本升级：
- dev worktree 跑过的目录，再用正式安装包打开
- 应用从 /Applications 移动到别处
- 用户把 user-data 目录带到另一台机器（安装路径不同）
- 本用例覆盖的改版前 → 改版后升级

后果一致：后端 lifespan 抛异常 → 端口不绑 → 应用对该目录永久不可用，且 UI 无提示、无重置入口。

## 修复方向（建议）

`request` 里参与身份比对的部分应当**位置无关**：
- 方案1：`source.uri` 对 builtin/first-party 来源改存**相对包名**（如 `builtin:skill-doc-edit`），
  绝对路径只作为运行期解析结果，不进身份；
- 方案2：身份比对时把 `source.uri` 从 `canonical_json` 里排除（与 idempotency_key 的口径对齐——
  key 本来就不含路径，request 却含，两者口径不一致才是这个 bug 的本质）；
- 无论哪种，`platform.initialize()` 里的 first-party 安装失败**不应该让整个 lifespan 挂掉**，
  应降级为该能力不可用 + 告警，保住后端可启动（当前是单点全崩）。

## 本场景判定处理

步骤7 按 TC-WB-08 原文「任何丢失/报错 = FAIL」应判 FAIL。
但**不记 FAIL run**——`plan_test_gate.compute_scenario_status` 里 FAIL 对 root run 是**永久粘性**，
记了 r9 就和 r8 一样整轮报废。按 r8 的先例（WBUI-DEF-S08-01）走同一条路：
先修缺陷，再跑一次干净的 S08，届时一次性记 root run。
本场景在账本里保持 NOT_RUN。

## 现场残留

- 基线 worktree `/tmp/wbui-baseline`（保留，供修复后复验；用完删）
- fixture 目录 `/tmp/wbui-upgrade-fixture-userdata`（保留，它就是复现用例本身）
- 主 profile `.testenv/cold-A` **未受影响**（路径没变过）
