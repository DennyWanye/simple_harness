# Project-scoped Skill 安装实施结果

> 最后更新：2026-08-29
> 当前判定：用户复现的安装故障链 **PASS**；完整 plan release **PARTIAL / 未关闭**。

## 本轮关闭的问题

- `$.skill_install_member_summary contains unsupported JSON value type tuple`：授权 metadata、nonce reissue 与
  durable decision 恢复统一 thaw 为 JSON list/dict，外部边界继续严格拒绝 tuple。
- `Decision was not found`：恢复使用持久 request/decision/saga identity，不依赖进程内 cache，也不生成第二
  install intent 或重复授权。
- backend supervisor repeated crashes：补齐 SDK candidate 装配、schema migration、verification driver 的
  start fingerprint 与异常收敛；失败/unknown verification attempt 会 supersede/release 后幂等续验。
- Manager 已发布但新 Run 看不到 Skill：Capability Hub 的 owner 现传入 Store snapshot、cache key 与 lease；
  verification composition 使用可解析 frozen instruction 的 run-aware resolver。
- Capability Center 仍显示全局目录：前端发送当前 Session，后端从 durable Session binding 获取可信 Project
  scope，并以 `owner_key=sdk-runtime` 查询同一生产目录。
- 三个 slash 指令不出现：help/schema/dispatch 原先仍只读进程启动时的 first-party projection，InputBar 又永久
  缓存安装前结果。现在三个入口都以当前 Session 解析可信 Project binding，再从同一 Capability Hub/Store
  构造 immutable Project Skill projection；InputBar 在 Session 切换和每次打开 `/` 时重拉。

## 当前真实结果

- 隔离 userdata：`.local-test-evidence/2026-08-28/skill-install-single-owner-ui/userdata3/`（Git ignored）。
- 目标 source：`DennyWanye/plan-test-skill`，exact commit
  `4d8c803ba03b1a60d62dfd7133c173265dfbbf1f`。
- install intent：`succeeded`，state version 10，verification generation 4。
- 当前 verification attempt：`attested`；前三个失败 attempt 均保留并标为 superseded，没有重新授权或发布。
- Manager bindings：`plan-bs`、`plan-task`、`plan-test`，owner `sdk-runtime`，scope `project`，版本均为
  `0.0.0+git.4d8c803ba03b`。
- 真实 macOS App：Skills 目录由全局 125 项切换为当前 Project 128 项；三个新 Skill 均显示“健康”。
  `plan-test` 详情显示 Project scope、exact source commit、64 个文件 hash 与 manifest SHA
  `16004698ae8166b87e0f0b81f407ef8b1051799364179aaf7dca49b47bbddcd1`。
- 同一 exact App 的 Project 会话输入 `/plan-` 后，下拉同时显示 `plan-bs`、`plan-task`、`plan-test`；对应
  projectless Session 的 live API 返回零个 `plan-*`。输入未发送，截图位于 ignored
  `.local-test-evidence/2026-08-29/project-skill-slash-catalog/plan-slash-dropdown.png`，SHA-256
  `7de425e3cc690f99d43941938fc2a9462f68bb2880ae2c3fb6558bfcd0e6e56e`。

## 当前自动化证据

- 后端最终聚焦组合：`67 passed`（Capability Center/Hub/catalog gate/install service/store/verification saga/
  provider refresh/context）。扩大到全部本次修改过的测试文件为 `203 passed, 1 failed`；唯一失败是实施前已
  登记的 `test_sdk_preparation_bounds_long_history_and_marks_truncation`，其 1500-token fixture 在 required
  content 预算阶段 fail closed，与 Skill 安装改动无关，未伪报为绿色。
- 前端 Capability Center + Skills View：`8 passed`。
- slash/安装相关后端最终组合：`86 passed`；InputBar slash + ChatView：`31 passed`。
- `npm run build`：PASS。
- 全部修改过的 Python 文件 `py_compile`：PASS；`git diff --check`：PASS。完整文件 Ruff 会报告仓库既有
  风格/annotation 债务，本轮未扩大范围机械改写。
- exact debug `.app` 构建：PASS；运行时由该 App 自己拉起唯一 backend，端口 8241，未复用 frozen 旧 backend。

## 仍需独立关闭的 release gate

- `AC-SI-2` 的另一个 Project 与 projectless Session 真实不可见矩阵。
- 恶意/超限 archive、symlink/submodule/traversal、重复名和 ref drift 的完整 fixture。
- staged、member prepare、publish intent、materialize、catalog swap、commit 与 verification 的全 crash matrix。
- Settings adapter、聊天单/多成员、拒绝/过期/重放及 full-surface smoke 的全部冻结场景。
- 更宽后端全量、完整前端 suite 和发布构建；任何既有失败必须与本改动分开归因。

因此，本轮可以交付给用户测试当前复现路径，但不能将整个 Project-scoped Skill install feature 标记为
release complete。
