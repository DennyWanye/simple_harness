# 升级造数配方（upgrade fixture）— Workbench UI 改版

> 依据：oracle-clarifications.md 裁决第 10 条（E2/E3 基线锚点配方）。
> 消费方：仅 TC-WB-12 步骤 7（冷启动基线锚点）。TC-WB-08 步骤 7 的旧数据升级
> 验证已于 2026-08-09 经用户裁决退役，下文第三、四节仅保留历史，不得作为当前发布结论。
> 基线 commit：**644ab16**（改版前）。共享测试目录下文以 `$FIX` 指代（执行时取绝对路径，如
> `/tmp/wbui-upgrade-fixture-userdata`，全程不得混用其他 user-data）。

## 一、基线构建准备

```bash
git worktree add /tmp/wbui-baseline 644ab16
cd /tmp/wbui-baseline
# 按仓库既有 dev 启动方式（dev.sh）完成一次预热启动（首启含编译），随即正常退出。
```

## 二、冷启动基线锚点（TC-WB-12 步骤 7 ①）

预热完成后，用同一命令**第二次启动**并秒表计时：

- 起点：dev.sh 进程拉起（回车时刻）。
- 终点：主窗可点击（对任一 UI 元素的一次点击有响应）。
- 记录秒数，写入 `plans/2026-08-04-workbench-ui/baseline.md` 补记行（格式：
  `冷启动基线（644ab16，预热后第二次启动）：<N>s @ <机器/日期>`）。

## 三、历史：旧数据造数（已退役，不执行）

以共享 user-data 启动基线构建：

```bash
DESKPET_USER_DATA_DIR=$FIX ./dev.sh   # 以仓库实际 dev 启动命令为准
```

在 UI 内完成（全部真实操作，不许直接写库）：

1. **1 个 provider 配置**：设置面板添加一个可用 provider（记录 id 与 default_model）。
2. **1 个密钥**：为该 provider 录入密钥并保存（记录录入方式；不留明文截图）。
3. **2 个会话**：新建会话 A 发送 `升级造数-A`，新建会话 B 发送 `升级造数-B`，各等回复完成。
4. 正常退出（托盘「退出」）。

造数完成后快照留证：`ls -la $FIX` 与 `$FIX/config.toml` 的 endpoints 行（脱敏）入账。

## 四、历史：改版构建同 env 核对（已退役，不执行）

回到改版后工作区（主 worktree / HEAD）：

```bash
DESKPET_USER_DATA_DIR=$FIX ./dev.sh
```

核对项（全部满足才 PASS）：

| # | 核对 | 预期 |
|---|---|---|
| 1 | 侧栏会话列表 | 会话 A/B 都在，可加载，`升级造数-A`/`-B` 原文与回复完整 |
| 2 | 设置页 Provider 区 | 步骤三的 provider 配置在位（id/default_model 一致） |
| 3 | 密钥生效 | 用该 provider 发一条消息真实往返成功（以往返成功为密钥在位证据，不要求回显） |
| 4 | 无迁移崩溃 | 启动全程无崩溃/无数据迁移报错（companion.db 007 迁移属预期变更，静默成功即可） |

## 五、改版侧冷启动对照（TC-WB-12 步骤 7 ②）

改版构建同样预热一次后测**第二次启动**计时（口径同第二节），与 baseline.md 锚点比较：
改版值 ≤ 基线值 + 3s 即 PASS（裁决第 4 条）。

## 六、清理

```bash
git worktree remove /tmp/wbui-baseline
# $FIX 保留至本轮验收结束后删除（保留期内是复测夹具）
```
