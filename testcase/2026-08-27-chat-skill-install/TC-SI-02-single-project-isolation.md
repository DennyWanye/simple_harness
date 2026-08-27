---
id: TC-SI-02
purpose: Verify a single Skill install is bound to one exact Project identity and remains absent from another Project and projectless runs
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-SI-2, TO-SI-3, TO-SI-R2]
tags: [skill-install, project-scope, isolation, single]
entrypoint: bound Project chat
revision: 1
---

# TC-SI-02 — 单 Skill 与 Project 三域隔离

## 前置

- 隔离 user-data 中注册不同 filesystem identity 的 Project A、B，并保留 projectless Session。
- 使用冻结 exact commit、仅含一个唯一名称 Skill 的 HTTPS GitHub fixture。

## 步骤与预期

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 在 A 输入 `Install this skill into this project: <single-skill HTTPS GitHub URL>`。 | typed installer stage 一项候选；确认卡只显示 A 与 exact digest。 |
| 2 | 点击允许，等待成功后在 A 创建 fresh Run 并用自然语言触发该 Skill。 | A 的 fresh Run 可发现、冻结并 page-in；receipt 可追溯 `project_id + revision + root identity`。 |
| 3 | 分别在 B fresh Run 和 projectless fresh Run 搜索并触发同名 Skill。 | 两域均不可发现/调用，不借用 A catalog，也不回退 legacy user inventory。 |
| 4 | 在 A 以同 URL/exact revision 重做安装。 | 返回既有 receipt/already-settled；binding、目录与操作计数均不增加。 |
| 5 | 令同路径对应不同 Project identity，再建 Run。 | 新 identity 不继承 A binding；同名/不同 revision 不产生隐式 precedence。 |

## 通过条件

- A 可用且 B/projectless/同路径异 identity 均不可用；重放无重复副作用。
- 证据包含三域 fresh Run catalog 摘要、receipt/hash、安装目录前后清单和操作计数。

