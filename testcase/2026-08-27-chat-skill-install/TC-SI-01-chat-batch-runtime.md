---
id: TC-SI-01
purpose: Verify a natural chat request installs a multi-Skill GitHub repository through the typed installer and proves exact runtime page-in
status: active
surface: desktop-ui
type: hybrid
obligations:
  - TO-SI-1
  - TO-SI-3
  - TO-SI-5
  - TO-SI-R1
tags: [skill-install, chat, batch, runtime, permission]
entrypoint: bound Project chat
revision: 1
---

# TC-SI-01 — 聊天批量安装与 runtime 证明

## 前置

- 使用隔离 user-data，注册 Project A；保存 Project 目录、`.claude/skills`、`.codex/skills` 与 managed inventory 的安装前清单/hash。
- GitHub fixture 为冻结 exact commit 的三 Skill 仓库；记录 URL、commit、候选名称和预期 digest。
- 真实 Provider 可用；UI 操作、日志和只读状态对账均用同一 correlation 标记。

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 在 Project A 新 Session 输入 `请你安装这个skill到当前项目：https://github.com/DennyWanye/plan-test-skill`。 | 创建新 root；首个安装副作用是 typed `skill_install`，无 `run_shell`/文件复制安装；完成前不声称成功。 |
| 2 | 查看确认卡，暂不点击。 | 一张卡列出 exact commit、3 个排序候选、权限摘要、Project A、digest 和 expiry；managed catalog/Project 源码均未改变。 |
| 3 | 真点击允许一次并等待。 | 整批只有一次安装确认；显示 Manager receipt 与 runtime verification Run；确认后 publish + catalog refresh 小于 2 秒。 |
| 4 | 新建 Project A Run，输入 `/plan-test 请先概述你会执行的阶段，不要实施代码`。 | Run catalog resolve 到 receipt 中相同 manifest/content/scope hash；Skill body 实际 page-in，回复体现该 Skill 阶段而非通用猜测。 |
| 5 | 对账目录与终态。 | 3 个成员均可见；Project 源码、`.claude/skills`、`.codex/skills` 无变化；只有 verification terminal 成功后 UI 才出现安装成功。 |

## 通过条件

- 步骤 1～5 全部满足；至少 2 个独立完整 root run，其中 1 个位于累计 ≥10 轮历史的 Session。
- primary evidence：动作前后 UI capture、root/session/tool/permission/receipt/verification correlation、目录清单、exact hashes、耗时。

