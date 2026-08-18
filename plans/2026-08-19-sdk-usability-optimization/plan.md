<!-- plan-status: finalized -->

# Plan：SDK 易用性优化（harness 0.1.2 切换 + 双 SDK 文档/验证脚本）

> 唯一真相：`plans/2026-08-19-sdk-usability-optimization/acceptance.md` + `assurance-contract.json`（用户已批准 2026-08-19）
> 架构基线：`ARCHITECTURE/SDK_EXTRACTION.md`、`ARCHITECTURE/ARCHITECTURE.md` §1/§2/§20、`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`（均已校准至 HEAD `5b781bf6`）
> 涉及仓库：`simple_harness`（宿主）、`simple-harness-sdk`、`simple-harness-memory-sdk`

## 主要矛盾

**决定成败的核心问题：wheel 身份（版本号 + SHA-256 + 文件路径）在宿主侧硬编码于 6 处以上**
（`desktop_runtime.py:68-69`、`main.py:6750/7031/7038/7859/8407/8471`、`conformance.py:65/212`）。
Slice 1 若只改其中几处，后果不是"编译错误"而是更糟的两种静默态：①fail-closed 校验拿旧 SHA
拒新 wheel → 启动即死；②某条路径拿新 wheel 配旧版本字符串 → 校验被架空（FAIL-5）。
因此 Slice 1 的第一任务不是"换 wheel"，而是**把 wheel 身份收敛为单一事实源**（一个常量模块），
让所有消费点 import 它——这既完成切换，又永久降低下一次切换的漏改面。

次要矛盾（Slice 2/3）：**文档示例与真实 API 的漂移没有机器守卫**——quickstart 示例对着 0.1.1/0.1.2
都跑不起来却长期无人发现。解法是"docs-as-tests"：示例代码必须能被脚本提取执行，release-gate
脚本把这个检查固化为一键门禁。

## 最佳实践调研（含本项目适配分析）

| 实践 | 来源/依据 | 本项目适配分析 |
|------|-----------|----------------|
| **单一事实源常量**（version/pin 集中定义，消费点 import） | 发布工程通例（如 setuptools-scm 单点版本）；本项目 `runtime_paths.py` 已是 fail-closed 校验单点 | **照用**。现状 6+ 处硬编码是历史切片（B1/B2/B3）各自落地的残留。新建 `sdk_adapters/sdk_candidate.py` 导出 `SDK_VERSION` / `SDK_WHEEL_FILENAME` / `SDK_WHEEL_SHA256` / `build_candidate_identity()`，所有消费点改为 import。前提条件完全满足（纯 Python 常量，无循环依赖风险——runtime_paths/composition 已在同包） |
| **docs-as-tests**（文档代码块可被提取执行，如 rustdoc doctest / pytest-markdown-docs） | Rust/Python 文档测试实践 | **改造后用**。不引入新测试框架依赖；Slice 2/3 的 release-gate 脚本直接用 `awk`/python 提取 quickstart 的 ```python 块到临时文件执行。前提：示例必须自包含（fake provider，不依赖真实 LLM key）——0.1.2 minimal-consumer 已是 mock provider 设计，满足 |
| **Facade / Consumer Adapter 层**（10-Port 内核 API 外包一层 3-Protocol 简易 API） | GoF Facade；SDK v0.1.2 已实现 `consumer_adapter.py` | **已落地，本次只做"推广为推荐入口"的文档层工作**。不重构宿主 sdk_adapters 到 consumer 层（acceptance 明确排除，避免无收益 churn） |
| **Release smoke gate**（发布前干净环境一键验证） | 发布工程通例（twine check / smoke venv） | **照用**。干净 venv + 装 wheel + 跑最小示例 + conformance CLI，exit code 结构化。前提：本机可联网建 venv（TRUST-2），满足 |

**放弃的备选**：① 把宿主 sdk_adapters 重构到 consumer layer——工作量大、对"外部用户易接入"目标无直接贡献，acceptance 已排除；② 引入 pytest-markdown-docs 等框架——增加依赖，awk 提取已够用；③ SDK 版本号从 wheel METADATA 动态读取替代常量——运行时读 dist-info 增加启动 IO 与失败面，fail-closed 设计哲学下显式常量更优。

## 关联验收标准

- Slice 1 覆盖 S1-AC-1..6；Slice 2 覆盖 S2-AC-1..4；Slice 3 覆盖 S3-AC-1..4。无遗漏。

## Assurance / 信任与失败边界

- Profile=standard；contract 见 `assurance-contract.json`。
- 入口链与 trust boundary：宿主启动装配（`main.py` → `composition.py` → `runtime_paths.py::verify_sdk_candidate`）是 fail-closed 边界；Slice 1 改动不得削弱 `verify_sdk_candidate` 的任何检查项。
- 数据流/持久化：Slice 1 不动 `execution-v1.sqlite3` schema；0.1.1→0.1.2 为纯新增 API，无数据迁移。
- 范围内失败：FAIL-1（错 bytes）→ 双 SHA 核对 + uv.lock 锁定；FAIL-2（不兼容回归）→ pytest 基线对比 + conformance + 真机 COLD-1；FAIL-3（文档说谎）→ 脚本化执行示例；FAIL-4（脚本误报）→ 脚本自身先跑一遍已知坏场景验证会 FAIL；FAIL-5（校验削弱）→ 负向测试（篡改 wheel 必拒）。
- 停止追踪点：SDK 内部实现缺陷（OOS-4）；非 macOS 平台（OOS-2）。

## 已核实的关键事实（plan 依据，2026-08-19 核实）

- 0.1.2 wheel SHA-256 = `387c8d1d97c0f89e4664347fb57ca6a43a0e7fa772b07a0f34c6f3a6e86efd4c`（SDK 仓库 `dist/`）。
  **来源修正（challenger R1）**：dist/ 的 BUILD_INFO.txt/SHA256SUMS 只记录 0.1.2 之前的 0.1.0 provenance；0.1.2 wheel 含 `consumer_adapter.py`（由 `91df02d` 之后的 `cb1f245` 引入），实为 SDK 仓库 HEAD（`896b685`）本机构建，dist/ 未入 git。**byte-for-byte SHA 核对是真正的完整性锚点**；Slice 2/3 将在 SDK 仓库补 0.1.2 的 BUILD_INFO/SHA256SUMS 记录。
- 0.1.2 对 0.1.1 为**纯新增**：模块文件无删除（仅新增 `runtime/consumer_adapter.py`、`runtime/ports.py`）；顶层 `__all__` 完全一致；`RuntimePorts` 16 个字段逐一相同。
- 宿主 0.1.1 硬编码点（生产代码）：`desktop_runtime.py:68-69`；`main.py:6750`、`:7031`、`:7038`、`:7859`、`:8407`、`:8471`；`conformance.py:65`（`_WHEEL` 路径）、`:212`（identity 字符串；SHA 运行时从文件算出自适应）。
- **测试套件同样是身份消费点（challenger R1）**：`backend/tests/sdk_adapters/test_runtime_paths.py:19-25` 与 `test_composition.py:31-33` 用 0.1.1 identity 做**正向**测试——0.1.2 装入 venv 后这两个文件必红，必须纳入 SSOT 清扫。
- `scripts/verify_sdk_wheel.py:21-24` 的 `EXPECTED_HASH` 只有 0.1.0/0.1.1 条目——复用前必须更新；且更新方式是从 `sdk_candidate.py` import 常量，**不得**制造第三个硬编码副本。
- minimal-consumer "Run completed: None" 根因已定位（challenger R1 spike）：`kernel.py:1127 wait_idle(...) -> None` 不返回终态，示例打印 None 但仍 exit 0——**修复在示例侧**（终态断言 + exit code），无需改 SDK 代码；另 `demo.py:30,55-59` 硬编码 db 路径与 `run_id='run-001'`，重跑会撞持久库主键，需每次新 run-id + 干净 db。
- `conformance.py` 的 `VENDORED_SDK_SHA256` 是 `hashlib.sha256(_WHEEL.read_bytes())` 运行时计算——换路径即自适应，但 identity 版本字符串仍硬编码。
- `backend/vendor/README.md` 记载 active candidate 信息，并引用 `scripts/verify_sdk_wheel.py`。
- 0.1.2 conformance CLI（`testing/cli.py:57-77`）**强制要求 `--host MODULE:FACTORY --suite --artifact-sha256`**；wheel 内不附带任何 consumer-side host——Slice 3 的 release gate 必须先造一个可 import 的 minimal host，否则该步退化为假 PASS（FAIL-4）。
- 注释性引用（不影响功能，顺手更新）：`tools/__init__.py:10`、`tool_catalog/providers.py:147`、`workflows/definitions/sdk_v2|sdk_v7/__init__.py` docstring。

---

# Slice 1 — harness SDK v0.1.2 宿主切换（高风险）

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `backend/deskpet/sdk_adapters/sdk_candidate.py` | **新建**：wheel 身份单一事实源 | 新增常量 + `build_candidate_identity()` |
| `backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl` | 新 vendor 工件 | 从 SDK 仓库 dist/ 拷贝，SHA 核对 |
| `backend/pyproject.toml` | 依赖声明 | `>=0.1.2` + source path 换 0.1.2 |
| `backend/uv.lock` | 锁定 | `uv lock` 重生成 |
| `backend/deskpet/sdk_adapters/desktop_runtime.py` | 桌面运行时桥 | 68-69 行常量删除，改 import |
| `backend/main.py` | 启动装配 | 6 处 0.1.1 引用改 import 常量 |
| `backend/deskpet/sdk_adapters/conformance.py` | conformance 适配 | `_WHEEL`/identity 改 import 常量 |
| `backend/tests/sdk_adapters/test_runtime_paths.py` | 既有正向测试 | 0.1.1 identity → import sdk_candidate 常量 |
| `backend/tests/sdk_adapters/test_composition.py` | 既有正向测试 | 同上 |
| `scripts/verify_sdk_wheel.py` | wheel 校验脚本 | `EXPECTED_HASH` active 项改从 sdk_candidate import |
| `backend/vendor/README.md` | vendor 审计文档 | active candidate 更新为 0.1.2 |
| `backend/tests/sdk_adapters/test_sdk_candidate.py` | **新建**：单一事实源 + fail-closed 负向测试 | 新增 |

## 任务清单（按依赖排序）

### Task 1 — 新建 wheel 身份单一事实源  [覆盖 S1-AC-2, S1-AC-3 部分]
- 改动文件：`backend/deskpet/sdk_adapters/sdk_candidate.py`（新建）
- 修改方式：
  ```python
  """Single source of truth for the vendored SDK wheel identity."""
  SDK_VERSION = "0.1.2"
  SDK_WHEEL_FILENAME = "simple_harness_sdk-0.1.2-py3-none-any.whl"
  SDK_WHEEL_SHA256 = "387c8d1d97c0f89e4664347fb57ca6a43a0e7fa772b07a0f34c6f3a6e86efd4c"

  def sdk_wheel_path() -> Path:  # backend/vendor/<filename>
      ...
  def build_candidate_identity() -> SdkCandidateIdentity:  # 组装 runtime_paths.SdkCandidateIdentity
      ...
  ```
- 验证（本任务内只做可独立成立的部分）：模块可独立 import（不拉 main.py）；常量与 vendor 实际文件一致（Task 2 完成后）。
  **完整 `verify_sdk_candidate` 正向校验依赖 Task 2（wheel 落盘）+ Task 3（装入 venv），归入 Task 5 的测试断言**——本任务不得在该环境前置条件未满足时声明验证通过。
- 依赖：无

### Task 2 — vendor 0.1.2 wheel + 双 SHA 核对 + 校验脚本更新  [覆盖 S1-AC-1]
- 改动文件：`backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl`（新增）、`scripts/verify_sdk_wheel.py`（更新）
- 修改方式：`cp` SDK 仓库 dist/ 的 0.1.2 wheel；`shasum -a 256` 输出必须与 Task 1 常量逐字符一致；保留 0.1.0/0.1.1 历史工件（审计链不删，README 标注 historical）。
  `verify_sdk_wheel.py` 的 active 预期 hash **从 `backend/deskpet/sdk_adapters/sdk_candidate.py` import**（repo 根脚本 sys.path 注入即可），0.1.0/0.1.1 条目保留为 historical 记录——不得制造第三个硬编码 SHA 副本。
- 验证：`python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl` PASS；对 0.1.1 历史 wheel 的校验行为不退化。
- 依赖：Task 1（import 其常量）

### Task 3 — pyproject + uv.lock 切换  [覆盖 S1-AC-1]
- 改动文件：`backend/pyproject.toml`（`:161` requirement → `>=0.1.2`；`:174` source path → 0.1.2 文件名；注释更新）、`backend/uv.lock`（`uv lock` 重生成）
- 验证：`cd backend && uv sync` 成功；`.venv/bin/python -c "import simple_harness; print(simple_harness.__version__)"` 输出 `0.1.2`。
- 依赖：Task 2

### Task 4 — 消费点全部切到单一事实源  [覆盖 S1-AC-2, FAIL-5]
- 改动文件：
  - `desktop_runtime.py:68-69`：删除 `_SDK_VERSION`/`_SDK_WHEEL_SHA256`，`:344` 改用 `build_candidate_identity()`
  - `main.py:6750/7031/7038/7859/8407/8471`：版本字符串/wheel 路径/identity 全部改 import `sdk_candidate`
  - `conformance.py:65/212`：`_WHEEL` 改 `sdk_wheel_path()`，identity 改 `build_candidate_identity()`
  - **测试套件同改（challenger R1 必改项）**：`backend/tests/sdk_adapters/test_runtime_paths.py:19-25` 与 `test_composition.py:31-33` 的 0.1.1 identity 正向测试改 import `sdk_candidate` 常量——否则 Task 3 装 0.1.2 后这两个文件必红
  - 注释性引用顺手更新（`tools/__init__.py:10`、`tool_catalog/providers.py:147`、`sdk_v2/sdk_v7` docstring 的 "0.1.1 cutover" 措辞）
- 验证：`grep -rn '"0\.1\.1"' backend --include="*.py" | grep -v __pycache__` 仅剩注释/历史说明；`grep -rn "simple_harness_sdk-0.1.1" backend scripts` 仅剩 vendor README 历史段与 verify_sdk_wheel 的 historical 条目。
- 依赖：Task 1

### Task 5 — fail-closed 负向测试 + 单一事实源测试  [覆盖 S1-AC-2，TO-S1-2]
- 改动文件：`backend/tests/sdk_adapters/test_sdk_candidate.py`（新建）
- 修改方式：①断言 `build_candidate_identity()` 通过 `verify_sdk_candidate`；②篡改：构造错误 SHA 的 identity → 校验必 raise；指向不存在/错版本 wheel → 必 raise；③断言 `desktop_runtime`/`main`/`conformance` 三处 import 的是同一常量对象（防再分裂）。
- 验证：pytest 该文件全绿。
- 依赖：Task 1、2、3、4（verify_sdk_candidate 检查已安装 distribution 版本与 direct_url origin，必须等 0.1.2 装入 venv）

### Task 6 — API 兼容机器校验  [覆盖 S1-AC-3，TO-S1-3]
- 改动文件：`testcase/2026-08-19-sdk-usability-optimization/api_compat_check.py`（新建脚本，纳入 testcase）
- 修改方式：对 0.1.1 与 0.1.2 两个 wheel 做：顶层 `__all__` diff、模块文件清单 diff、`RuntimePorts` 字段 diff——断言"无删除项"（允许新增）。本 plan 已预跑通过（见"已核实的关键事实"），脚本固化为可复跑门禁。
- 验证：脚本 exit 0。
- 依赖：Task 2

### Task 7 — pytest 全量回归 vs 基线  [覆盖 S1-AC-4，TO-S1-4]
- 基线：phase-2 锁定的 `baseline-snapshot`（当前 HEAD 全量 pytest 结果文件）。
- 验证：`cd backend && python -m pytest`；failed 集合 ⊆ 基线 failed 集合（无新增）。
- 依赖：Task 3、4

### Task 8 — conformance 套件  [覆盖 S1-AC-5，TO-S1-5]
- 验证：运行宿主 adapter conformance（`backend/deskpet/sdk_adapters/conformance.py` 入口，20 case），结果不劣于切换前基线（20/20）。
- 依赖：Task 4

### Task 9 — 全表面冒烟 + 真机 COLD-1  [覆盖 S1-AC-6，TO-R1，TO-R2]
- 触发依据：启动装配改动 → full-surface smoke 强制（config 分级触发策略）。
- 步骤：①`./scripts/dev.sh` 干净用户数据目录冷启动；②日志确认 `sdk_runtime_ready` slot 发布、无 fail-closed 拒绝；③全表面冒烟脚本对每个用户可达入口打最小一枪；④真机主聊天发"你好，介绍下你自己"，断言非空 assistant 回复到达前端（computer-use 真人测试，MANUAL_TEST=required）。
- 依赖：Task 3、4

### Task 10 — 文档回写与提交  [DoD]
- 改动文件：`ARCHITECTURE/SDK_EXTRACTION.md`（§0 状态更新为 0.1.2 active）、`ARCHITECTURE/PROJECT_STATUS.md`（里程碑）、`backend/vendor/README.md`（active candidate）、宿主 CHANGELOG
- 验证：文档内 SHA/版本与 Task 1 常量逐字符一致。
- 依赖：Task 7、8、9

---

# Slice 2 — harness SDK 消费者体验（simple-harness-sdk 仓库）

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `examples/minimal-consumer/` | 最小消费者示例 | 修复"Run completed: None"未收尾问题 |
| `docs/quickstart.md` | 快速上手 | 示例代码对齐 0.1.2 真实 API |
| `docs/integration-guide.md`、`docs/api/ports.md` | 接入指南 | consumer layer 升为推荐入口 |
| `CHANGELOG.md` | 变更记录 | 0.1.2 post 条目 |

## 任务清单

### Task 1 — 复现并修复 minimal-consumer  [覆盖 S2-AC-2]
- 现状：`plans/2026-08-17-sdk-ease-of-integration/adapter-layer-success-report.md` 记录 `Run completed: None` / `⏸️ Task in state: None`。**根因已由 challenger spike 确认**：`kernel.py:1127 wait_idle(...) -> None` 不返回终态，示例把它当结果打印且不做终态断言、始终 exit 0——修复在示例侧，**不改 SDK 代码**。
- 修改方式（`examples/minimal-consumer/demo.py`）：
  ① 终态断言：启动后通过 `client.query(run_id)` / context 读取真实 RunState，断言达到 `COMPLETED`（或示例显式声明的终态），**不达标则 exit 非 0**——让"跑通"在 exit code 上可见；
  ② 可重跑：`run_id`/session 每次运行生成新值（如 `run-{uuid4().hex[:8]}`），db 路径默认放临时目录（或运行前显式删除旧 `execution.db`）——消除持久库主键冲突；
  ③ 修掉 `Run completed: None` 打印（打印 query 到的真实终态）。
- 验证：连续执行 `python examples/minimal-consumer/main.py`（或 demo.py）**两次**均 exit 0 且 stdout 显示 COMPLETED（第二次证明可重跑）。
- 依赖：无

### Task 2 — quickstart 对齐真实 API + 安装路径写实  [覆盖 S2-AC-1, S2-AC-4]
- 现状：quickstart.md 的 demo 用 `build_runtime(ports)`、`AuthorizationResult.allow()` 等不存在/不符的 API；安装段写 `pip install simple_harness_sdk-0.1.1-py3-none-any.whl`（:15/:21/:275，integration-guide.md:54/:702 同样陈旧），且**从未说明外部用户从哪里获得 wheel**。
- 修改方式：
  ① 以 Task 1 修复后的 minimal-consumer 为蓝本重写 quickstart 示例（`build_consumer_runtime` + 3 Protocol）；
  ② **代码块可执行性约定**：全文只保留**一个**自包含可运行 ```python 块（最小示例），其余片段统一改为非执行标记（如 ```python fragment 或明示"片段，不可直接运行"）——extraction 脚本只执行可运行块，选择规则在 quickstart 开头注明；
  ③ 安装段写实且**唯一来源**（challenger R2 核实：GitHub 只有 v0.1.0 Release，0.1.2 未发布、dist/ 未入 git）：获取途径 = clone SDK 仓库后本机构建（`git clone <repo> && cd simple-harness-sdk && uv build`，wheel 产出在 `dist/`），安装行 = `pip install dist/simple_harness_sdk-0.1.2-py3-none-any.whl`（仓库根相对路径，逐字可提取执行）；
- 验证：见 Task 4 脚本化提取执行。
- 依赖：Task 1

### Task 3 — 文档推广消费者入口  [覆盖 S2-AC-3]
- 改动文件：`docs/quickstart.md`（主入口）、`docs/integration-guide.md`（开头声明推荐路径 + 安装段同步修正）、`docs/api/ports.md`（Core Ports 一节标注"多数消费者只需 3 个 Protocol，10-Port RuntimePorts 为高级用法"）
- 验证：三处文档 grep 核对无互相矛盾（推荐入口命名与版本号一致）。
- 依赖：Task 2

### Task 4 — 从零跑通脚本化验证  [覆盖 S2-AC-1, S2-AC-4，TO-S2-1/4]
- 改动文件：`examples/minimal-consumer/verify_from_zero.sh`（或 scripts/ 下）
- 修改方式：干净临时目录 → **本地 clone SDK 仓库到该目录**（解决 wheel 供给：`git clone /Users/denny/projects/simple-harness-sdk`，仓库自带源码，wheel 由下一步构建产出）→ 从 quickstart 文档**逐字提取构建与安装命令执行**（解析文档中的 `uv build` / `pip install dist/...` 行，不是脚本自带路径——安装段过期/说谎会立刻打红门禁，堵 FAIL-4）→ 提取 quickstart 的可运行 ```python 块执行 → 跑 minimal-consumer（两次）→ 结构化 PASS/FAIL。
- 验证：本机执行 PASS；**负向自检**：故意把 quickstart 安装段改错一处，脚本必须 FAIL（防 FAIL-4 误报），随后还原。
- 依赖：Task 2、3

---

# Slice 3 — memory SDK 文档 + 双 SDK release-gate 脚本

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `simple-harness-memory-sdk/README.md` | 用户接入文档 | extras 对应关系 + hash embedder 前提 |
| `simple-harness-memory-sdk/scripts/verify_quickstart.sh` | release gate | 新建一键验证 |
| `simple-harness-sdk/scripts/verify_release_gate.sh` | release gate | 新建一键验证（复用 Slice 2 Task 4 产物） |

## 任务清单

### Task 1 — memory SDK README 修正（quickstart 必须可逐字运行）  [覆盖 S3-AC-1, S3-AC-2]
- 现状（challenger R1 确认的矛盾）：README:22-48 quickstart 用 `enable_world_model=True`，而 world model 需要 `[world]` extra——**按现状逐字跑 README quickstart 在基础安装下会失败**。
- 修改方式：
  ① 先读 `pyproject.toml` 核实 extras 真实定义（`[embeddings]`/`[world]` 名称以实际为准）；
  ② **重构 quickstart**：基础示例只用 `pip install -e .` 即可运行的能力（append + recall + facts，`enable_world_model` 不开启），保证该 ```python 块逐字可执行；
  ③ world model / BGE-M3 拆到独立"可选能力"小节，各自标注所需 extra 与权重下载前提；
  ④ 明确默认 HashEmbedder 为确定性哈希伪向量、语义召回质量有限，生产建议装 embeddings extra。
- 验证：README 每条 extras 声明与 pyproject `[project.optional-dependencies]` 逐条一致；quickstart 块被 Task 2 脚本逐字提取执行通过。
- 依赖：无

### Task 2 — memory SDK release-gate 脚本（逐字提取 README，不写释义版）  [覆盖 S3-AC-4]
- 改动文件：`scripts/verify_quickstart.sh`
- 修改方式：干净 venv → `pip install -e .` → **从 README 逐字提取 quickstart ```python 块执行**（禁止写"等效示例"——释义版会让 README 说谎而门禁照绿，FAIL-3/4）→ 结构化 PASS/FAIL + exit code。
- 验证：本机执行 PASS；负向自检：改坏 README 示例一处必须 FAIL，随后还原。
- 依赖：Task 1（README 定稿后示例以 README 为准）

### Task 3 — harness SDK release-gate 脚本（先造 conformance host）  [覆盖 S3-AC-3]
- 前置事实（challenger R1）：0.1.2 的 conformance CLI 强制 `--host MODULE:FACTORY --suite --artifact-sha256`，wheel 内不带 consumer-side host——不解决这个，conformance 步就是假 PASS。
- 改动文件：
  ① `examples/minimal-consumer/conformance_host.py`（**新建**）：以 minimal-consumer 的 3 个 Protocol 实现为基础暴露 `build_host` factory，至少覆盖 provider + tool 两个 suite（参照宿主 `conformance.py` 的 host 形态，但保持 consumer 级简单）；纳入 SDK 包可被干净 venv import 的路径（脚本用 PYTHONPATH 指向 examples 目录）；
  ② `scripts/verify_release_gate.sh`（新建）：干净 venv → 装 dist/ 0.1.2 wheel → minimal-consumer → `python -m simple_harness.testing --host conformance_host:build_host --suite provider,tool --artifact-sha256 <0.1.2 SHA>` → 结构化 PASS/FAIL；
  ③ SDK 仓库补 0.1.2 的 BUILD_INFO/SHA256SUMS 记录（修 TRUST-4 的 provenance 缺口）。
- 验证：本机执行 PASS；计时 ≤10 分钟；负向自检：把 host 改坏一处，conformance 步必须 FAIL。
- 依赖：Slice 2 Task 4

---

## 执行顺序与并行

- Slice 1 → Slice 2 → Slice 3 串行（Slice 3 Task 3 依赖 Slice 2 产物）；Slice 内 Task 标注依赖的可并行。
- 每个 Slice 独立过 phase-3 完成度审计 + phase-4 门禁 + 提交；三个 Slice 都完成后统一 phase-5/final。
- 三仓库各自提交：宿主（Slice 1）、simple-harness-sdk（Slice 2 + Slice 3 Task 3）、simple-harness-memory-sdk（Slice 3 Task 1/2）。
- **既有脏文件处置（challenger R1）**：simple-harness-memory-sdk 当前有 4 个 modified gate 产物
  （`plans/phase2-embeddings/verification/run-1/*`，与本 program 无关）。Slice 3 开工前先用独立的
  housekeeping 提交处理（inspect diff 后作为既有 gate 产物更新单独 commit），不混入本 program 提交，
  保证 DoD「三仓库各自 git status 干净」可验证。

## 回滚策略

- Slice 1 回滚 = `git revert` 宿主提交（pyproject/uv.lock/常量模块指回 0.1.1），旧 wheel 保留在 vendor/ 不删，回滚零数据风险。
- Slice 2/3 为文档/示例/脚本，回滚 = git revert，无生产影响。
