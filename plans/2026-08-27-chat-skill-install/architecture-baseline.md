# Chat Skill Install 架构基线

> 2026-08-29 状态说明：下文“原始链路”是实施前基线。当前实现已收敛到
> `ProjectSkillInstallService`、Manager-owned Project binding 与 canonical verification Run；当前生产事实和
> 验收边界见 [`results.md`](results.md) 及 `ARCHITECTURE/`。保留本文件用于解释为何不能继续修补 legacy
> directory installer。

## 校准范围

- 旧锚点：`91d22247947c152c1bf5393a840553b6172628cc`
- 新锚点：`362e51496d06fe14f8cfdc1909f25381ee427e3b`
- 本次 diff 的结构性变化主要是 Project-scoped Session/Run admission 与 SDK runtime
  catalog；Skill URL installer 本身仍留在 legacy UI WebSocket 分支。

## 解剖麻雀：原始用户请求的实际链路

1. Project-bound Session 已经能从 `SessionProjectBinding` 冻结 `project_id` / `project_root` /
   `project_identity`，并在 Run admission 复验（`backend/deskpet/session/project_binding.py:305-338,390-433`）。
2. 聊天 Run 通过 SDK runtime catalog 搜索可执行 Tool；目录中没有 Skill URL installer，所以
   原始提示词的 tool search 只能落到 shell 类能力。
   process-wide ToolRegistry 其实已注册 `capability_install`，但 SDK product allowlist 未投影它
   （`backend/deskpet/capabilities/tools.py:652-704`；`backend/deskpet/sdk_adapters/tools.py:31-44`）。
3. 独立的设置页 WebSocket 处理器会把 GitHub 内容 stage 到 `<userdata>/_skill_staging`，
   然后 finalize 到 `<userdata>/skills`（`backend/main.py:586-610,13284-13382`）。
4. 生产 `_SkillLoader` 没有任何目录或 scope 源（`backend/main.py:2572-2593`）；它不是
   Manager-owned Capability authority（`backend/deskpet/skills/loader.py:4-25`）。
5. 所以现有“final path + reload”成功响应既没有 Project binding receipt，也没有新 Run
   exact manifest/content hash resolve/page-in 证明；它不能支撑聊天成功宣称。

## 主要矛盾

产品同时存在“可下载的 legacy UI installer”和“可执行的 Manager-owned
Capability authority”，但两者没有同一个 Project-scoped install service 衔接；聊天和设置页
又不共享入口、确认与 receipt 语义。另外，现有 Project capability scope key 只基于
workspace 路径，没有消费 Session/Run 已冻结的 `project_id/revision/identity`。这是权威和
身份合约分裂，不是提示词匹配问题。

## 架构判定

- 复用 `GitPackSource` 的 staging-only fetch、subdirectory/symlink 护栏与 package validator；
  不复用 legacy `finalize_batch()` 的 partial-success copy 作为发布原语。
- 定义 raw Skill repo 到 immutable Capability Pack 的 canonical conversion：冻结 resolved revision、
  确定单/多 Skill pack 粒度、排序文件集与 exact digest，再交给 Manager publish/bind。
- 扩展 trusted Tool execution context 与 Capability Project scope，使 binding key 绑定
  `project_id + project_revision + project_identity`，并定义旧 path-key binding 的非权威兼容语义。
- 在已有 `capability_install` + Manager lifecycle 之上收敛一个 Project-scoped application
  service，不从零重写 lifecycle。
- chat typed Tool 与 Settings WebSocket 必须是同一 service 的两个 adapter。
- legacy Skill Store 与 Capability Center 的 install surface 必须归并/退役到该 service，不新增
  第三套 owner。
- 单 Skill 与多 Skill 都先返回一个与 URL/revision/list digest/project/expiry 绑定的
  staged intent，一次确认后再原子 publish/bind。
- 安装成功必须以 Manager receipt、Project catalog refresh 以及新 Run exact hash
  resolve/page-in 为证据；任何目录写入或 reload 都不是成功门。
- 旧用户 Skill inventory 继续作为非权威兼容数据，不隐式升级或跨 Project 可见。

## 体检

- 职责：当前 downloader 同时承担 legacy finalize，但没有 Capability publish 职责，边界不完整。
- 依赖：UI 直接依赖安装器而不是 application service，导致 chat 无法复用。
- 技术债：multi-repo 无确认直接 finalize，成功响应又与 runtime usability 脱节。
- 可行性：Project identity、Run admission、Capability Manager/Store、GitPackSource 与 typed
  `capability_install` 已存在；缺口是 raw Skill canonicalization、Project identity-aware scope、
  共享编排服务与薄 adapters，验收范围技术上可行。
