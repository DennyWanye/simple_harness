---
id: TC-HM-09
purpose: Verify TaskScope creation, immutable multi-root binding revisions, and per-effect workspace authority
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A3, HM-TO-A8, HM-TO-R2, HM-TO-R4]
tags: [human-memory, taskscope, multi-root, binding, auto-mode]
entrypoint: context_route and project effect
revision: 3
---

# TC-HM-09 — TaskScope 创建、多根绑定与权限

## Fixture

- 配置 workspace root 的真实后代两个、外部合法目录一个、workspace root 本身、公共父目录、文件、symlink 越界目录和可制造 filesystem identity drift 的目录。
- 未配置 workspace 时另跑 macOS/Linux 默认 `~/SimpleHarnessWorkSpace` lane。
- crash/retry 使用 `fixtures/fault-matrix.json` 的 `taskscope-init-binding` lane；fixture SHA-256
  `b4dcb2f39a2e5c2f7afeeb1dd496aa94587fe075c8772ea44fab880115bc74da`。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 未明确目录地提出多步骤文件任务，在 provisioning/checkpoint 各点中断并重试。 | Host 自动建唯一 TaskScope 和 managed task_home；主对话 ID 不变，lost-ACK 不重复目录/Scope。 |
| 2 | 明确指定第一个目录提出任务。 | TaskScope 绑定 exact canonical root，而不是其 Git 根或公共父目录；产生 binding-set revision。 |
| 3 | Manual 模式追加第二 root。 | effect 前要求用户确认；确认后 append 新 revision，旧 root 不被替换。 |
| 4 | Auto 模式追加配置 workspace 的真实后代。 | 无目录追加确认即可产生新 revision；mode 来自可信 Run snapshot，不豁免其他高风险动作。 |
| 5 | 让模型自行开启 Auto，并依次提议 workspace root 本身、公共父目录、symlink 越界、文件、静默改绑和 identity drift。 | 全部在 effect 前 fail closed；现有 binding 不变，记录稳定拒绝原因。 |
| 6 | 在每个 root 执行不同 canary effect。 | 每个 effect envelope 绑定 exact task_scope_id、root ref 和当时 revision；无可信 binding 的路径零写入。 |

## 决定性证据

- TaskScope/provision receipt、目录清单、canonical/filesystem identity、binding revisions、per-effect envelope 与逐 root canary hash。
