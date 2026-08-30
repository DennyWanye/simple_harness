---
id: TC-GS-09
purpose: Verify automatic permission is the fresh default, explicit overrides persist, and mandatory gates remain effective
status: active
surface: desktop-ui
type: hybrid
obligations: [TO-A9, TO-R3, HM-TO-R4]
tags: [permission, auto-default, provenance, authorization]
entrypoint: primary conversation and permission settings
revision: 3
---

# TC-GS-09 — 自动权限默认与安全边界

## 步骤与预期

使用 `verification/policy-fixture.json` 的确切 Tool 与 canary。四个独立 root 分别为 fresh-auto、auto-negative、user-explicit fresh、cold-restart fresh；continuation 复用 fresh-auto lineage而不计独立 root。每个记录 root_run_id 与 policy revision。

| 步骤 | 真人操作 | 预期结果 |
|---:|---|---|
| 1 | 全新 profile 打开唯一主对话，查看权限模式并启动 fresh Run。 | 显示并持久化 `auto/factory_default`；fresh Run 使用该值。 |
| 2 | 在 fresh-auto 同一 root continuation 执行 auto-eligible 受控 effect与合法 `skill_install`；另建 auto-negative fresh root 执行 deny/unavailable。 | continuation 可按既有 policy 自动完成；Skill 完整预检并生成 `approval_kind=auto`、`provenance_version=1` 的 durable auto-approved receipt，绑定 exact intent/content/member stamp、principal、policy generation、grant fingerprint、run/root/call/effect 与真实 handoff hashes，且无 manual SDK decision/人工确认；第二个独立 root 保持拒绝/不可用门禁。 |
| 3 | 显式切换到 UI 支持的 manual 模式并启动 user-explicit fresh Run。 | CAS 写入 `manual/user_explicit`，UI 与后续 Run 立即采用覆盖值。 |
| 4 | 完全冷重启并打开同一主对话，启动第四个独立 cold-restart fresh Run。 | 用户覆盖值保持，不被 factory default 或 init 覆盖；第四个 root 使用相同 user-explicit revision。 |
| 5 | 在 auto lane 分别触发非法 Skill source、deny、unhealthy/platform-ineligible。 | source validation 在自动批准前稳定拒绝；deny 与 unavailable 均零 effect 并显示真实原因。 |

## 决定性证据

- 模式/provenance/revision、fresh/continuation lineage、auto-v1 receipt hash payload、确认 UI、拒绝与 effect count、冷重启 incarnation。
