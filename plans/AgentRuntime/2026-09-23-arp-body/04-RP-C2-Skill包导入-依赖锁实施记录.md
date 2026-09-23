# ARP-EXEC-1.1.1 主体施工 RP-C2：Skill 包导入与依赖锁

日期：2026-09-23。分支 `arp-1.1.1`，上一片 RP-C1 提交 `534f2ab0`。

## 1. 交付物

| 模块 | 内容 | 规格条款 |
|---|---|---|
| `arp/skills.py`（新） | `inspect_bundle`：zip 只读检查——≤1024 文件、单文件≤8MiB、解压总≤64MiB、SKILL.md≤128KiB；拒非常规文件（符号/硬链接）、路径逃逸/非规范/保留名（`rules.safe_skill_path`）、大小写碰撞、重复条目、读出字节多于声明（zip bomb）；每文件 sha256 取自实际字节。`parse_frontmatter`：首行必须 `---`、必须闭合、≤16KiB；PyYAML `SafeLoader` 严格子类拒 alias/anchor/tag/merge key/重复键/非字符串键，去掉隐式 timestamp 等解析器（日期样式保持字符串）；只允许 name/description/license/compatibility/metadata(str→str)/allowed-tools；name `[a-z0-9]+(-[a-z0-9]+)*` 1–64（ARP 可移植规则）。`SkillImporter.import_bundle`：核 `SkillInstallCommand`（包字节 hash 必须等于 artifact pin、scope 必须是本 namespace、`expected_catalogue_revision` 必须等于当前 epoch）；NATIVE：根 `skill.json` 严格按 Skill schema，`files` 与包逐文件对（不列 skill.json 自身），size/sha256 必须一致，schema/能力/runner ref 在目录精确解析，SCRIPT 的 script_path 必须是 SCRIPT 角色文件；SKILL_MD：转成 INSTRUCTIONS 候选（能力 `sdk.skill.instructions`，required_tool_refs 为空，allowed-tools 只记在导入结果里不进定义，scripts/ 目录一律 ASSET）；两者同存且 name/description 不一致 → `SKILL_MANIFEST_CONFLICT`。定义内容寻址：同体重放同修订不推 epoch，异体开新修订。包字节原样存 `root/bundles/<digest>.zip`（临时文件 + fsync + rename）。`resolve_dependencies`：精确 ref 闭包，未安装 → `unresolved` 且 `complete=false`（仍 QUARANTINED）；同逻辑 id 两版本 → `DEPENDENCY_DIAMOND_CONFLICT`；环 → `DEPENDENCY_CYCLE`；深度≤16、节点≤128；锁 hash 覆盖 root/scope/nodes/edges/unresolved，不含自身；写 `arp_dependency_locks`（同 hash 复用）。`read_file`：按定义 sha256/size 核对后返回字节。`details`：`SkillDetailsPage`，文件分页 ≤64 项/≤64KiB，cursor 绑定修订 | §9.1–9.5、9.10；SKILL-CATALOGUE §1–§2 |
| `arp/bootstrap.py` 改 | 新增 INSTRUCTIONS 能力 `sdk.skill.instructions` 与其 schema、评估策略 pin `builtin:skill-eval-v1`；`BootstrapReport` 带这三个 pin | SKILL-CATALOGUE §3 |
| `arp/runtime.py` 改 | 装配 `state.skills = SkillImporter(...)`，包目录 `root/bundles` | |
| `pyproject.toml` / `uv.lock` | 新增可选依赖组 `skill-import = ["pyyaml>=6.0,<7"]`；解析器按需导入，缺失时 `SOURCE_UNAVAILABLE` 具名拒绝 | §9.3 |

新增测试 `test_arp_skills.py`（33 项）：SKILL.md-only 导入为 QUARANTINED INSTRUCTIONS 候选（角色、零工具、锁完整、文件回读核 hash、重放不推 epoch、详情分页、目录页 file_count 且无文件列表、epoch/hash 守卫）；10 组前言拒绝（无首行 `---`、未闭合、重复键、anchor/alias、tag、未知执行键、非可移植 name、merge key、非字符串 metadata、>16KiB）；允许子集与日期保持字符串；7 组包拒绝（`..` 逃逸、大小写碰撞、保留名、符号链接、单文件 >8MiB、SKILL.md >128KiB、>1024 文件）；原生包逐文件核对、未列文件/hash 不符/前言与 manifest 不一致/幽灵依赖不完整/diamond/SCRIPT 指向非 SCRIPT 文件；核验后补 13 项：同包多次导入版本不变、同命令号换包冲突、重送命令跳过 epoch 守卫、锁不完整不得进试用、9 组隐藏内容/不可读包（前置脚本、尾随字节、注释、条目间空隙、加密标志、bzip2 压缩、CRC 损坏、截断、目录条目带内容）、目录条目走路径检查且必须为空、深嵌套前言。

## 2. 实现决定

1. **YAML 依赖**：规格要求用锁环境的 PyYAML SafeLoader 子类；SDK 锁环境原无 PyYAML，加为可选依赖组而非硬依赖，Host（已有 pyyaml）安装时带上 `skill-import`。
2. **策略 pin 不在目录解析**：`requested_permission_policy_ref / verification_policy_ref` 是 policy pin，目录没有 POLICY 种类；本片只验 pin 形状，真实策略解析留给 RP-C3 试用/准入。
3. **NATIVE 包的 SKILL.md**：不以 `---` 开头视为纯说明正文，不解析前言；SKILL_MD 格式必须有前言。
4. **allowed-tools**：只作建议记录在导入结果（`frontmatter`）里，定义的 `required_tool_refs` 为空；目录建议与执行授权分离。
5. **包字节逐一入账**（核验后加）：第一个本地文件头必须在偏移 0，各条目首尾相接，随后紧接中央目录、末尾记录，注释为空；加密标志与 STORED/DEFLATED 以外的压缩方式一律拒绝；目录条目也走 `safe_skill_path` 且大小必须为 0；zip 库的 `BadZipFile / NotImplementedError / RuntimeError / EOFError` 统一转 `SKILL_PATH_INVALID`，CRC 不符转 `SOURCE_HASH_CONFLICT`，YAML `RecursionError` 转 `SKILL_FRONTMATTER_INVALID`。
6. **导入命令账本**（核验后加）：新表 `arp_skill_import_commands(namespace_id, command_id, bundle_hash, skill_id, skill_revision)`，同命令号同包 → 重放（跳过 `expected_catalogue_revision` 守卫），同命令号换包 → `SOURCE_HASH_CONFLICT`；新命令号才核 epoch。版本比对先把 `version` 置为最新修订号再算内容 hash，同体复用修订，异体 `+1`。
7. **试用门**（核验后加）：`catalogue.transition` 对 SKILL 进 TRIAL/ADMITTED 前要求该修订存在 `complete=1` 的依赖锁，否则 `DEPENDENCY_UNRESOLVED`。

## 3. 测试与回归

- ARP 定向：`tests/agents/arp` → **302 passed**（核验修复后重跑）。
- legacy `tests/agents`（排除 arp）16 failed / 172 passed / 6 skipped；`tests/execution` 17 failed / 144 passed；与基线相同。

## 4. 独立核验

一轮，opus 5.5 只读审阅，只报阻断级。结论：5 条阻断，全部处置。

| # | 审阅意见 | 处置 |
|---|---|---|
| 1 | 同一个包每导入一次就多一个版本号（比对前 `version` 已是 1，修订>1 时 hash 永不相等） | 先把 `version` 置为最新修订号再比对；测试：同包三次导入仍是修订 2、epoch 不动 |
| 2 | 命令号不幂等：同命令号换包会再装一次；epoch 守卫先于幂等判断 | 新表 `arp_skill_import_commands`；同命令号同包重放并跳过 epoch 守卫，换包 `SOURCE_HASH_CONFLICT` |
| 3 | 依赖锁不完整也能进试用 | `transition` 对 SKILL 进 TRIAL/ADMITTED 要求 `complete=1` 的锁，否则 `DEPENDENCY_UNRESOLVED` |
| 4 | 加密/未知压缩方式未拒；zip/YAML 库异常裸露 | 显式拒加密标志与非 STORED/DEFLATED；库异常统一转具名 ArpError |
| 5 | 包里可以藏未列出的内容（前置数据、注释、条目间隙、目录条目带内容） | `_verify_layout` 逐字节入账；目录条目走路径检查且大小为 0 |

## 5. 未做

- RP-C3：`begin_trial / admit / suspend / resume / retire` 命令与评估绑定（`SkillEvaluationBinding`、原 Assurance CheckPolicy 接口冻结）、`skill.load`（E 段装载、SkillUse 回执）、`skill.execute`（INSTRUCTIONS 返回内容；SCRIPT 经原 executor；WORKFLOW → `WORKFLOW_UNAVAILABLE`）、`ToolDispatchAdapter` 权限交集。
- Host 侧 `agent_skill_install / details` verbs 的 DTO 解码与 artifact 读取（Host 有 artifact 存储；SDK 侧以字节 + artifact pin 接口）。
