# S5 — Joint wheel conformance 与 SDK release candidate

<!-- slice-status: completed -->

## Release unit

- MUST AC：AC-1、AC-8（2/8）
- Tasks：10/10
- 高风险系统：package dependency、cross-version conformance、release identity（3/3）
- 依赖：S1～S4 receipts

## 文件影响

| 仓库/文件 | 修改 |
|---|---|
| Harness `pyproject.toml`, `src/simple_harness/__init__.py` | version 0.3.0、metadata |
| Memory `pyproject.toml`, `src/simple_harness_memory/__init__.py` | version 0.4.0、`[harness]` extra |
| 两仓 `tests/artifact/`, `tests/conformance/` | joint exact-wheel、standalone、future consumer fixture |
| 两仓 `.github/workflows/ci.yml`, release workflows | authoritative artifact handoff和联合matrix |
| 两仓 README/API/Quickstart/CHANGELOG/BUILD_INFO | 公共契约、版本、hash、迁移说明 |

## Tasks

### S5-T1 — 冻结semver与public snapshot [AC-1, AC-8]

- Harness 0.3.0、Memory 0.4.0；changelog列明fresh schema和Adapter retirement是breaking change。
- 两仓version source、metadata、docs、public snapshots一致，不保留虚假兼容声明。

### S5-T2 — Standalone Memory install [AC-8]

- clean venv只安装Memory基础wheel，在import blocker下import/build development manager；metadata不含必选Harness。
- 安装`memory[harness]`时解析到Harness 0.3.x；错误major/minor组合失败。

### S5-T3 — Future consumer fixture [AC-1, AC-8]

- 新建产品中立fixture，实现Provider/Tool/Auth/identity，使用SDK内置
  `CurrentMessageContextProvider + ConsumerRuntimePolicies.local_default()`，传`MemoryManager`给
  ConsumerRuntimePorts；rich-context变体再实现正式Context provider。fixture不得import Adapter、query/sink、
  Memory backend internals或手调recall/append。
- 跑personal/family identity、SDK-only `share_fact`（same replay/cross-principal conflict/forget cascade）、
  automatic recall、non-Memory Context frozen replay、completed turn、restart、
  erasure replay rejection和memory=None。rich-context fixture为root与两个continuation物化三个不同source ref，
  验证claim前持久化、同ref同hash、同continuation换ref冲突及未传ref的current-message fallback。

### S5-T4 — Python 3.11–3.13 exact-wheel matrix [AC-8]

- 每个版本建clean venv，只从candidate目录安装同一Harness/Memory wheel bytes；记录distribution origin、
  version、wheel SHA、SQLite version/capability。
- mypy fixture、pytest conformance、source/editable/path negative checks均通过；Harness-free Memory clean-wheel
  future-consumer fixture直接import并调用`MemoryPrincipal`与`MemoryManager.share_fact`，不得依赖任何产品Adapter。

### S5-T5 — Authoritative build/provenance [AC-8]

- 各repo只build一次authoritative wheel/sdist，twine check；生成canonical BUILD_INFO/SHA256SUMS。
- downstream下载/验证原bytes，不允许release workflow重build；artifact hash写入joint manifest。

### S5-T6 — Platform lanes [AC-8]

- 复用现有candidate workflow跑Windows x64、macOS ARM64、Linux ARM64适用lane；Linux ARM64 core gate升级为
  committed-turn pair/restart/identity receipt。
- 平台缺模型资源时使用显式fixture embedder，不触发下载。

### S5-T7 — Docs与integration status [AC-8]

- 两仓README Quickstart只展示`memory=MemoryManager`；API reference说明ownership/failure/scope；迁移表覆盖
  query/sink/Adapter/manual prepare→official path。
- Integration Status写明simple_harness为本轮实测消费者；AIPhone/K6仅“interface ready/not integrated”，
  不声称它们已测试。

### S5-T8 — Candidate gate与promotion准备 [AC-8]

- 两仓full tests/lint/type/build/reuse/source provenance/release verifier；各自finalize receipt。
- 只生成待promotion candidate和tag plan；实际外部upload/tag在S6真UI通过后执行。

## Required scenarios

| ID | 必须证明 |
|---|---|
| S5-C1 | Memory standalone无Harness；extra联合安装正确 |
| S5-C2 | 3.11/3.12/3.13消费同一exact wheels |
| S5-C3 | future consumer无Adapter/manual lifecycle完成全链 |
| S5-C4 | source/version/wheel/SHA/BUILD_INFO一致 |
| S5-C5 | docs不误报AIPhone/K6已集成 |
| S5-C6 | root/continuation source refs独立、crash可重放且最小消费者fallback无需产品缓存 |
