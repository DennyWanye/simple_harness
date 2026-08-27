# FULL_SURFACE_SMOKE 范围

本 slice 修改共享 catalog、权限与启动装配，因此按 fail-closed 升级为 full-surface。每个入口各一枪，结果写到 ignored evidence 后由 `scripts/acceptance/skill_install_surface_smoke.py` 校验。

| surface id | 最小动作 | 必须事实 |
|---|---|---|
| chat-batch | 提交 SMOKE-SI-CHAT-BATCH-ZH，停在确认卡 | typed installer、3 members、一次确认、无 shell |
| chat-single | 提交 SMOKE-SI-CHAT-SINGLE-MIXED 并批准 | receipt + verification Run + exact one visible |
| chat-natural | 提交无 install/skill 关键词的等价表达 | 正确意图路由，不进 generic shell fallback |
| settings-url | Settings 从 URL 添加，拒绝 | 与 chat 同 service/correlation schema，零 publish |
| runtime-page-in | 新 Run 提交 SMOKE-SI-RUNTIME | exact hash page-in + non-empty Skill-specific result |
| first-party | 记录 first-party inventory 并代表性 page-in | 17 个 first-party 均存在；代表项 hash/调用成功 |
| capability-pack | 搜索、describe、activate 一个既有 pack | catalog 与 activation 正常 |
| permission | 对普通受控写操作分别 deny/allow | permission card 和 continuation 正常 |
| project-scope | A/B/projectless 分别搜索 Project Skill | 仅 A 可见 |
| ordinary-shell | 在允许范围执行只读 `pwd` | 普通 shell 行为未被 installer gate 错拦 |
| startup | fresh userdata 和既有 userdata 各完整启动一次 | backend ready、catalog rehydrate、无 crash loop |

结果 JSON 必须为 `{"surfaces":{"<id>":{"status":"PASS","evidence":"<relative ignored path>","facts":{...}}}}`。任何缺失或非 PASS 都使 smoke checker exit 1；checker 不替代真实 UI/Provider 证据。

