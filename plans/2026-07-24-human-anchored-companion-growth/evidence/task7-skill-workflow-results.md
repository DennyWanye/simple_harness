# Task 7 Skill / Workflow 分片结果

日期：2026-07-25

## 已完成

- 生产 `SkillLoader` 只加载 legacy user Skill；17 个 first-party Skill 通过只读、
  hash-verified managed discovery projection 提供 matcher/assembler/slash 发现，不再向
  CapabilityHub 重复注入裸 instruction。
- matcher cache identity 覆盖 owner/pack/version/manifest/content。
- `skill_invoke` 保持唯一注册点并从 Run frozen resolver 读取；`/<skill>` 返回 typed
  `PreparedSkillInvocationScopeV1`，未知或漂移 Skill fail closed。
- v2 Capability Pack schema 已改为离线自包含校验；17 个 shipped pack 增加
  dev/frozen/content-hash golden，并要求正文 tool refs 与 manifest/frontmatter
  `allowed-tools` 等值。
- Personal Workflow 已冻结 ToolSpec topology、限制 JSON Pointer root、使用稳定 logical
  effect/call id，并定义 checkpoint port。
- `SkillLoader` 的 script 执行路径保持删除；instruction 本身不直接执行任意代码。

## 验证

- Skill/Workflow 聚焦与兼容回归：`87 passed`
- Python 编译检查：通过
- `git diff --check`：通过
- pytest/py_compile 精确残留进程：`survivor_count=0`

## 后续组合边界

- managed projection 只负责 immutable discovery，不拥有 active binding。
- slash typed scope 仍需由 Run catalog frozen resolver 在真实执行时重新校验。
- Personal Workflow 已具备 schema/effect planning/checkpoint seam；完整解释器、child
  snapshot 与恢复协调属于 Task 10。
