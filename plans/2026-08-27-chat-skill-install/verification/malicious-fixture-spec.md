# Malicious Skill repository fixture 规范

所有 fixture 必须通过产品支持的 HTTPS GitHub source 进入，或仅在自动化 transport seam 中以等价 archive bytes 注入；不得把错误结果、模型回复或 UI terminal 直接注入。每个 fixture 固定 owner/repo/exact commit、archive SHA-256、成员清单和测试前 Project/catalog hash。

| fixture_id | 唯一畸变 | stage 预期 stable code | 零副作用断言 |
|---|---|---|---|
| SI-FX-URL-HTTP | `http://github.com/...` | `skill_source_https_required` | 无网络跟随、无 staging |
| SI-FX-HOST | HTTPS 非 GitHub host/redirect | `skill_source_host_not_allowed` | 不读取 redirect body |
| SI-FX-TRAVERSAL | archive member `../escape/SKILL.md` 或绝对路径 | `skill_archive_path_unsafe` | escape canary 不存在 |
| SI-FX-CASEFOLD | `a/SKILL.md` 与大小写等价冲突路径 | `skill_archive_path_collision` | 无候选/无 publish |
| SI-FX-SYMLINK | symlink/special mode 指向 archive 外 | `skill_archive_entry_unsupported` | link target 未读取/写入 |
| SI-FX-SUBMODULE | gitlink/submodule 伪装 Skill 目录 | `skill_archive_entry_unsupported` | 不递归拉取子仓库 |
| SI-FX-COUNT | 文件数超过上限 1 | `skill_archive_file_limit` | staging 精确清理 |
| SI-FX-BYTES | compressed/actual bytes 超限 1 | `skill_archive_size_limit` | 流式读取有界终止 |
| SI-FX-RATIO | zip bomb ratio 超限 | `skill_archive_compression_ratio` | 不解压到正式路径 |
| SI-FX-NO-SKILL | 无 `SKILL.md` | `skill_candidate_not_found` | 无确认卡 |
| SI-FX-MANIFEST | frontmatter 缺 name/description 或非法类型 | `skill_manifest_invalid` | 整批拒绝 |
| SI-FX-TOOL | `allowed-tools` 含未知/禁止工具 | `skill_allowed_tool_invalid` | 整批拒绝 |
| SI-FX-DUP-NAME | batch 内重复 normalized name | `skill_name_collision` | 整批拒绝 |
| SI-FX-FIRSTPARTY-COLLISION | 与可见 first-party 同名、digest 不同 | `skill_name_collision` | 不覆盖 first-party |
| SI-FX-REF-DRIFT | stage 后 branch 移动 | 无错误；仍发布 staged exact commit | receipt/hash 等于 stage，不读新 HEAD |

故障注入矩阵必须逐个覆盖 `staged`、每 member prepare、`publish_intent`、`files_materialized`、`catalog_swapped`、`committed-before-ACK`、runtime verification。每轮使用新隔离 Project/user-data；断言 operation 最终为 full-old/full-new/fenced-unknown，禁止 mixed visible set。

远程 fixture URL/commit 由测试运行时 JSON 提供；若未准备，SI-M4 保持 `NOT_RUN`，不得改用本地 `file://` 或任意公网仓库冒充。

