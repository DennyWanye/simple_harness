# Host 内嵌编排 SDK 锁文件换为 release 轮子

最后更新：2026-10-28（本地验收完成，随本次提交交付）。

## 范围与结论

在 `sdk/simple-harness-sdk` 使用本机 uv 0.12.8，将 Service SDK 0.3.20 的来源改为 GitHub v0.3.20 release 轮子，补齐轮子 SHA-256，并同步工作流依赖身份常量。89 个锁条目的名称、版本及重复条目数量均保持不变；除 Service 包及 Host 对它的来源元数据外，其余包和锁文件顶层内容逐项相同。无需回退重锁。

只在给定仓库根目录内操作。用户给定的外部输出目录未使用；临时环境、缓存、下载轮子及原始日志统一放在本机忽略目录 `.local-test-evidence/2026-10-28/host-uvlock-release/`。下文将它记为 `$AGENT_TASK_OUT_DIR`。第一次启动基线命令因证据父目录尚未建立而未执行 pytest；补建仓库根目录下的证据目录后重跑，结果如下。一次辅助版本核验脚本对无 version 的虚拟条目使用字典索引失败，修正后按 89 个条目的版本多重集和完整包内容重新核验通过。

## 改前与改后锁条目

改前（旧候选轮子已作废，锁文件没有记录轮子哈希）：

```toml
[[package]]
name = "simple-harness-service-sdk"
version = "0.3.20"
source = { registry = "../../.local-test-evidence/2026-10-25/unified-session/phase3/live8/inputs" }
wheels = [
    { path = "simple_harness_service_sdk-0.3.20-py3-none-any.whl" },
]
```

改后（原有 dependencies 与 realtime 可选依赖保持不变）：

```toml
[[package]]
name = "simple-harness-service-sdk"
version = "0.3.20"
source = { url = "https://github.com/DennyWanye/simple-harness-service-sdk/releases/download/v0.3.20/simple_harness_service_sdk-0.3.20-py3-none-any.whl" }
wheels = [
    { url = "https://github.com/DennyWanye/simple-harness-service-sdk/releases/download/v0.3.20/simple_harness_service_sdk-0.3.20-py3-none-any.whl", hash = "sha256:95a5ec79e6b12d7958f282e11c5e833f904f51505220a9d704698c14b2547bbb" },
]
```

uv 同时把 Host 的 `requires-dist` 中 Service 来源写为 URL，并新增从 release 轮子读取的 Service `package.metadata`（含其 memory/realtime extras 声明）；未激活 memory extra、未新增相关锁包。`git diff --stat sdk/simple-harness-sdk/uv.lock` 为 1 个文件、14 行增加、3 行删除。

`pyproject.toml` 新增 `[tool.uv.sources]`，`realtime = ["simple-harness-service-sdk[realtime]==0.3.20"]` 原样保留。uv 接受 URL 来源与版本约束，无须删除版本号，也没有拒绝该组合的错误信息。

## 哈希与工作流身份

- release 轮子 SHA-256：`95a5ec79e6b12d7958f282e11c5e833f904f51505220a9d704698c14b2547bbb`。uv 锁定值与重新下载后的 `shasum -a 256` 完全一致。
- 改前 `uv.lock` SHA-256：`fa152589f65dbbef0258163beff2fe313c91654544056866dc625aa75350fdde`。
- 改后 `uv.lock` SHA-256：`5fdec4bf373c4201f11a8922a0e66dfe81b77262f64708c597a09f3c64a8b6e3`。
- `SDK_DEPENDENCY_LOCK_HASH` 旧值：`89cf7198a98887e61fd3fc02e4310aa5844225ddbcef48d6a22f229f1aa8f921`。
- `SDK_DEPENDENCY_LOCK_HASH` 新值：`5fdec4bf373c4201f11a8922a0e66dfe81b77262f64708c597a09f3c64a8b6e3`。

常量进入工作流 manifest，派生的 `workflow_manifest_hash` 随之变化；开发期不做旧数据兼容。检索根目录 tests 与 SDK tests 中的 `workflow_manifest_hash`、`expected_manifest_hash`、旧常量及旧锁哈希，并检查工作流测试的 64 位十六进制字面量，没有发现 durable_task 或其它官方工作流把旧派生哈希钉死的断言，因此需更新的固定 manifest 哈希断言为 0 项。

关联检索命中逐项核对：`test_dependency_lock_identity.py`、`test_public_composition.py`、`test_exact_wheel_consumer.py` 均动态生成身份，后两者还有 `"0" * 64` 伪造身份拒绝反例；`test_capability_build_public_execution.py` 使用 registration 当场生成的身份。仅附加运行轻量且直接相关的 `test_public_composition.py`，未扩大到整个 artifact 或 integration 套件。

## 执行命令与测试结果

以下命令除特别说明外都在 `sdk/simple-harness-sdk` 执行。pytest 的 `TMPDIR`、`UV_CACHE_DIR` 和 `--basetemp` 均指向仓库内证据目录；uv 也使用该临时目录和独立缓存，没有修改项目 `.venv`。

```sh
# Run from the repository root.
AGENT_TASK_OUT_DIR="$PWD/.local-test-evidence/2026-10-28/host-uvlock-release"
mkdir -p "$AGENT_TASK_OUT_DIR/tmp" "$AGENT_TASK_OUT_DIR/uv-cache"
cd sdk/simple-harness-sdk
export TMPDIR="$AGENT_TASK_OUT_DIR/tmp"
export UV_CACHE_DIR="$AGENT_TASK_OUT_DIR/uv-cache"
.venv/bin/python -m pytest tests/unit/workflow/test_dependency_lock_identity.py -q --basetemp="$AGENT_TASK_OUT_DIR/pytest-baseline"
# Add the release URL/hash regression test, then run it before the fix.
.venv/bin/python -m pytest tests/unit/workflow/test_dependency_lock_identity.py -q --basetemp="$AGENT_TASK_OUT_DIR/pytest-red"
# Back up uv.lock and pyproject.toml inside the ignored evidence directory before editing.
uv lock
# Run from the repository root.
cd ../..
git diff --stat sdk/simple-harness-sdk/uv.lock
git diff -- sdk/simple-harness-sdk/uv.lock
# Continue in sdk/simple-harness-sdk.
cd sdk/simple-harness-sdk
shasum -a 256 uv.lock
UV_PROJECT_ENVIRONMENT="$AGENT_TASK_OUT_DIR/venv-check" uv sync --frozen --no-dev --extra realtime
"$AGENT_TASK_OUT_DIR/venv-check/bin/python" -c 'import importlib.metadata as m; print(m.version("simple-harness-service-sdk"))'
curl --fail --silent --show-error --location 'https://github.com/DennyWanye/simple-harness-service-sdk/releases/download/v0.3.20/simple_harness_service_sdk-0.3.20-py3-none-any.whl' --output "$AGENT_TASK_OUT_DIR/simple_harness_service_sdk-0.3.20-py3-none-any.whl"
shasum -a 256 "$AGENT_TASK_OUT_DIR/simple_harness_service_sdk-0.3.20-py3-none-any.whl"
.venv/bin/python -m pytest tests/unit/workflow/test_dependency_lock_identity.py tests/orchestrator/host/test_realtime_service_loopback.py tests/unit/workflow/test_public_composition.py -q --basetemp="$AGENT_TASK_OUT_DIR/pytest-green"
.venv/bin/ruff check src/simple_harness/workflow/dependency_lock.py tests/unit/workflow/test_dependency_lock_identity.py
```

| 检查 | 通过 | 失败 | 结论 |
|---|---:|---:|---|
| 改动前依赖身份基线 | 2 | 1 | 旧常量与旧锁文件不一致，预期红灯 |
| 新增 release 来源测试后、修复前 | 2 | 2 | 新测试在旧 registry 来源处失败，预期红灯 |
| 修复后定向 pytest（三份文件） | 35 | 0 | 无跳过，含正式工作流打包注册与 Service 回环 |
| Ruff 首次 | 0 | 1 | 新增导入后需补第三方/项目导入分组空行 |
| Ruff 修正后（两份改动的 Python 文件） | 2 文件 | 0 | 全部通过 |
| 冻结安装、版本、轮子哈希 | 3 项 | 0 | 新环境 CPython 3.14.7，安装版本 0.3.20；轮子哈希一致 |
| 锁文件结构核对 | 1 项 | 0 | 89 条锁记录版本不变，其余包无改动 |

任务给定的 `tests/unit/host/test_realtime_service_loopback.py` 不存在，实际文件为 `tests/orchestrator/host/test_realtime_service_loopback.py`，已运行该文件。任务中的 `docs/orchestration-host.md` 实际在 SDK 子目录，已按真实路径修改。核验后使用 `shutil.rmtree` 删除本轮创建的 `venv-check` 并确认目录不存在；其它原始证据保留本机。

## 文档与交付边界

接入文档改为从 GitHub release 下载附件并用 `--find-links` 安装，说明锁文件的 URL 与 SHA-256；交付回执仅把本任务对应留项改为已完成，其它留项保留。按仓库 AGENTS.md，同步更新 `ARCHITECTURE/AGENT_ORCHESTRATION.md` 和 `ARCHITECTURE/PROJECT_STATUS.md` 的局部完成事实，不新增整阶段或发版里程碑。

本地只提交本任务九份文件，提交说明为中文。不 push、不打 tag、不运行被禁止的 Git 命令。未改 `backend/uv.lock`、工具说明、提示词、已发布模板或任何密钥配置；未重启进程、未跑全套测试、未做 GUI/付费模型验收、未重发 SDK、未重生成部署清单、未修改原 `.venv`。本任务验收已完成，原回执中的其它工作仍待处理；本轮未覆盖已安装 Host 或已发布轮子的重新打包部署。

## 本地证据索引

原始证据均在 `.local-test-evidence/2026-10-28/host-uvlock-release/`，Git 只记录下表的相对索引与 SHA-256。

| 相对文件 | SHA-256 |
|---|---|
| `01-baseline.log` | `a32977c2d84d13eab5568304c7cf9cf8d8bc8e5bce9b85a62343f971762e529d` |
| `02-new-test-red.log` | `172cb231a1602f3e9b87d86d58cd21104e379727f9b628f6a0c4f6420433e8f7` |
| `03-uv-lock.log` | `83700726be5a69d11da3a13090564dda58429b8a049032a3032d9c05c97cf387` |
| `04-uv-sync.log` | `7d80c8a8c12ae67391a1687fe0c89e7ad77eab4bee57f16a86da8b7ae6ddb07c` |
| `05-installed-version.log` | `52f0f7238f6b810629ffd4feb4abfd7c5bc147dbd1372191f8843b2016db5c39` |
| `06-wheel-sha256.log` | `7a0804a2163632d8ad2514f67c6fd4e9bdac5896ae9b460a7cba505620d37eca` |
| `07-lock-comparison.log` | `ebe6336893f56624b5a09fceeb68082cae38ed6a6a3ccc4f4ca515b9e4563e03` |
| `08-related-tests.log` | `46a20d12c3c15b15a708c4e797823674dd68c97e62b68d339ce082e72c464aa8` |
| `09-ruff.log` | `b4b39d4b11482cafe62fe01a1bfeca3fe3112109fd789d39eb88f9a3fcdaafaf` |
| `10-ruff-green.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `11-manifest-assertion-search.log` | `994e7d13cd6f129d5b20b382c09e2ed41ee1cb02cc373faa20e8439e74560c58` |
