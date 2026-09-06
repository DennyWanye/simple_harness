# 已完成项目原 root 的合法续改

最后更新：2026-09-07。独立分支 `feat/completed-scope-new-workspace`，base `cb7ed574`，测试固定源 `1a8e1dd6`。Dirac 已核源码与分批证据，最终限定 ACCEPT。**7 个唯一控制分批 PASS**，不是同一 HEAD 全跑7项，也不累计重复执行为新增控制。本次要求的 Auto/Manual/alreadyBound 三项分别由 r4 两绿及 r5 一绿闭合。

## 已实现及证据边界

新 Run 从真实公开 `task_scope_search` 返回的四字段 candidate 取得旧 complete Scope 与精确 source hash，调用 `context_route.create_new(reuse_workspace_of=..., expected_source_hash=...)` 作为本 Run 首个 Scope。当前公开来源/披露、原 binding/root、实际配置权限及文件系统身份均验证后，创建新 active Scope 并真实绑定原 root。真实文件工具显式 overwrite 已存在文档，下一 MockTransport 物理请求收到工具结果；旧 Scope canonical/binding/历史前缀保留，未重新激活旧 Scope。

同 Run 已绑定旧 Scope 时，在新建前拒绝，并在发布 route 的同事务再次检查既有 evidence ingress 与 task route。实际下一请求得到明确下一 Run 指导，不产生第二 accepted task route、新 Scope 或文件修改。新增的合法 route/tool/terminal 事件逐一对照 Host 原 journal 与 SDK 公共 effect/terminal proof；保留完整集合、参数 hash、原事件前缀检查。

根身份 pin 是新 Host binding S1 的可选字段；未提供时保留原 payload/hash/port 调用。没有新 SDK 协议、数据库 schema 或业务 ledger。旧 H079/M618 制品、主树、原 userdata 未改。

载体借用主 Python 环境及 `primary-079618/installed` 的 H079/M618/S0313，加载本树 Host 源码；不是本树独立安装身份验收，没有模型/native。AUTO 仅配置允许的真实 root。MANUAL 正控通过真实公开 authority challenge 与 service.decide 人类控制调用后 resume 新 Scope；**真实 UI challenge 呈现/决定接线仍未验证且工具失败 carrier 丢结构的缺口保留**，不能称完整 Manual 原生路径。多 root 明确拒绝；foreign_scope 控制是不存在/不归该公开服务的引用，不冒充真实跨账户渗透测试。原 r19 和24轮旅程不追认完成。

## 分批结果与失败分类

| 批次 / 固定源 | 实际结果 | 解释及唯一有效控制 |
|---|---|---|
| r1 / eadea187 | 1P / 5F，8.06s | inode 替换拒绝1绿，后续不重跑。Auto/Manual 先 resume 旧 Scope 再建新 Scope，两个 route 已落地但后续被 Run 单 Scope 约束拒绝，这是原业务红。另3 source 否控的事件不变假设错误另记。 |
| r2 / 8f21e240 | 4P / 2F / 1 deselected，8.53s | stale hash、不存在/不归属 Scope、多 root 共3绿；alreadyBound 当时绿但新增真实 ingress 后重验，不重复计数。Auto 夹具缺生产 evidence ingress；Manual 未显式 overwrite，真实工具正确拒绝已有文件。 |
| r3 / cd30947f | 2F / 5 deselected，1.39s | dummy registration 缺 get，均未入业务。后用真实 ToolRegistry/register_os_tools 保留完整 spec/handler/schema，静态核所有使用点。 |
| r4 / 3bf65253 | 2P / 1F / 4 deselected，7.01s | Auto、公开 Manual 真实正向2绿。alreadyBound 最后误将内层 SDK event hash 等同外层 Host evidence hash，oracle 红保留。 |
| r5 / 1a8e1dd6 | 1P / 6 deselected，1.74s | 仅 alreadyBound 复验，独立公共 proof 核内层 hash，同时核外层 canonical hash 与 Host receipt，未删其他断言。 |

7 unique = r1 inode1 + r2 source 否3 + r4 Auto/Manual2 + r5 alreadyBound1。此前旧指导2控、原生、其他 SDK/control 证据均不加入计数。Dirac 对最终两层 hash oracle、r5 raw/resource 和分批唯一数已限定接受。

资源均默认共享 OS 锁、2GiB/180s/磁盘门，未覆盖配置：

| 批次 | PG / parent exit | elapsed / peak KiB |
|---|---|---|
| r1 | 48995 / 1 | 8.732s / 215200 |
| r2 | 49952 / 1 | 9.16s / 210016 |
| r3 | 50628 / 1 | 1.939s / 184800 |
| r4 | 56932 / 1 | 7.706s / 209952 |
| r5 | 57258 / 0 | 2.377s / 200720 |

所有 `remaining_group_members=[]`、`cleanup_error=null`。r4/r5 最低 disk 4394/4386MiB；最后已向主交槽。系统防熄屏 PID56392 未触碰。本叶无待运行测试，主负责合成及原生。

## 最小复跑命令

在本 worktree 执行。`PY=/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python`，`MAIN=/Users/denny/projects/simple_harness-primary-candidate`；OUT 必须是新的 ignored 目录。已绿不自动复跑。

```sh
"$PY" "$MAIN/scripts/run_resource_bounded.py" --evidence-dir "$OUT" -- \
  "$PY" -I -B .local-test-evidence/2026-09-06/completed-scope-continuation/run.py \
  "$PWD/$OUT/tmp" -k already_bound_run
```

r4同命令 selector 为 `new_active_scope_physically or manual_binding_requires_actual or already_bound_run`。carrier 禁插件自动加载，只选新文件、asyncio插件，关闭 pytest cache；加载主 installed target +本树 backend。未运行全库/新模型/构建/安装。

## ignored 原始证据索引

前缀为 `.local-test-evidence/<日期>/completed-scope-continuation/`。全部旧失败保留本机，未提交 raw。

| 日期/文件 | SHA-256 |
|---|---|
| 2026-09-06/r1/command.log | 4a9f8ed80ef6ef36fc6d44f73347b0c3aa3d0f30e7ab9f31d0df2a5f046f944c |
| 2026-09-06/r1/resource.json | 01089a53147283f24a57dd3dac52459c4f83a8729427e9cbe628d35a9ebf7e23 |
| 2026-09-07/r2/command.log | d107710ee48259d4761e5fa8726ea2c1b3c299ccb68520b0869304d28755b2e1 |
| 2026-09-07/r2/resource.json | 7eb2b11be329b8175cb8fcd4d69348655c148e8705791b57c754867299fa9cad |
| 2026-09-07/r3/command.log | 9851588d3f2dffe0403d9485b48cfdde6979f4e3eb9a73877d56707465af9697 |
| 2026-09-07/r3/resource.json | 8e260399a857a9474110f1cee3e5d904452220f6cbd8df20210cf0f872a886d4 |
| 2026-09-07/r4/command.log | d2dc18b45a00599712c6e8d7c1f7f0a2b803f78bf9f3b76e2a672a43f6b45e98 |
| 2026-09-07/r4/resource.json | 10cb40915bfdecf95a58e6bac9e833303e2fb41399bbb4b1b3aa4542aa2a8dfe |
| 2026-09-07/r5/command.log | 79ef0adf3f78d9555aa6435e632ff04ade50300b836233a67468b3734dbab57b |
| 2026-09-07/r5/resource.json | 558c8fe30f378fc6031da275e36b20848bdc0723e4868a2da566854930f39ad1 |
