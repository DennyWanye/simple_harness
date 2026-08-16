# SDK v0.1.0 发布方案 - GitHub 仓库不存在

**问题：** `git@github.com:DennyWanye/simple-harness-sdk.git` 仓库不存在或无访问权限

**状态：** SDK 代码和 wheel 本地就绪，需要创建 GitHub 仓库

---

## 选项 1：创建 GitHub 仓库并推送（推荐）

### 步骤 1：在 GitHub 创建仓库

1. 访问 https://github.com/new
2. 仓库名称：`simple-harness-sdk`
3. 描述：`Simple Harness SDK - Fault-tolerant workflow execution engine`
4. 可见性：**Private**（根据计划是私有发布）
5. **不要**初始化 README、.gitignore 或 LICENSE（本地已有）
6. 点击 "Create repository"

### 步骤 2：推送代码

```bash
cd /Users/denny/projects/simple-harness-sdk

# 推送分支
git push -u origin codex/sdk-v0.1-foundation

# 创建并推送 tag
git tag -a v0.1.0 -m "Simple Harness SDK v0.1.0 - Foundation Release

Deliverables:
- Fault-tolerant workflow runtime
- Three official profiles (durable_task, personal_v1, capability_build)
- Conformance testing framework (CLI + pytest plugin)
- Public API for external consumers (40 exports)
- Cross-platform pure Python wheel

Test Results: 1122/1122 passing (8.16s)
Wheel Hash: d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91

Known Limitations:
- Product integration deferred to v0.2.0 (T6-STRATEGIC-DECISION.md)
- Desktop E2E tests deferred (requires T6 adapters)
- Platform tests will run on GitHub Actions after tag push

External consumers (AIPhone) can integrate immediately.
See docs/consumers/aiphone-handoff.md for integration guide."

git push origin v0.1.0
```

### 步骤 3：等待 GitHub Actions

推送 tag 后，GitHub Actions 会自动：
- ✅ 构建 wheel 和 sdist
- ✅ 验证版本和 checksums
- ✅ 创建 GitHub Release
- ✅ 在 Linux/macOS/Windows 上测试

### 步骤 4：验证发布

检查 https://github.com/DennyWanye/simple-harness-sdk/releases/tag/v0.1.0

确认包含：
- `simple_harness_sdk-0.1.0-py3-none-any.whl`
- `simple_harness_sdk-0.1.0.tar.gz`
- `SHA256SUMS`
- `BUILD_INFO.txt`

---

## 选项 2：手动创建 GitHub Release（备用方案）

如果不想推送整个代码库，可以手动创建 Release：

### 步骤 1：创建空仓库

同选项 1 的步骤 1

### 步骤 2：推送最小代码

```bash
cd /Users/denny/projects/simple-harness-sdk

# 只推送必要的分支（用于 Release 关联）
git push -u origin codex/sdk-v0.1-foundation

# 创建 tag（本地）
git tag -a v0.1.0 -m "SDK v0.1.0 - Foundation Release"
git push origin v0.1.0
```

### 步骤 3：手动上传 artifacts

1. 访问 https://github.com/DennyWanye/simple-harness-sdk/releases/new
2. Tag version: `v0.1.0`
3. Release title: `Simple Harness SDK v0.1.0 - Foundation Release`
4. 上传文件：
   - `dist/simple_harness_sdk-0.1.0-py3-none-any.whl`
   - `dist/simple_harness_sdk-0.1.0.tar.gz`
5. 创建 SHA256SUMS：

```bash
cd /Users/denny/projects/simple-harness-sdk/dist
sha256sum simple_harness_sdk-0.1.0-py3-none-any.whl > SHA256SUMS
sha256sum simple_harness_sdk-0.1.0.tar.gz >> SHA256SUMS
```

6. 上传 `SHA256SUMS`
7. 在 Release 描述中添加 release notes（从 docs/release/v0.1.0.md 复制）

---

## 选项 3：仅本地使用（临时方案）

如果暂时无法创建 GitHub 仓库：

### 产品仓库已经可以使用 vendored wheel

```bash
cd /Users/denny/projects/simple_harness/backend
uv run python -c "import simple_harness; print(simple_harness.__version__)"
# 输出：0.1.0
```

### AIPhone 可以直接使用本地 wheel

将 wheel 文件发送给 AIPhone 团队：
- 文件：`/Users/denny/projects/simple-harness-sdk/dist/simple_harness_sdk-0.1.0-py3-none-any.whl`
- SHA256：`d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91`
- 文档：`docs/consumers/aiphone-handoff.md`

安装命令：
```bash
pip install simple_harness_sdk-0.1.0-py3-none-any.whl
```

---

## 推荐方案

**我推荐选项 1：创建完整的 GitHub 仓库**

**理由：**
1. ✅ 符合原计划（private GitHub Release）
2. ✅ GitHub Actions 自动测试和发布
3. ✅ 版本管理和 Release notes
4. ✅ 便于 AIPhone 团队下载
5. ✅ 支持未来版本更新

**执行步骤：**
1. 在 GitHub 创建 `simple-harness-sdk` 仓库（私有）
2. 执行上面的推送命令
3. 等待 GitHub Actions 完成
4. 验证 Release

---

## 当前文件位置

所有必要文件都已准备好：

```
SDK 仓库位置：
/Users/denny/projects/simple-harness-sdk/

关键文件：
- dist/simple_harness_sdk-0.1.0-py3-none-any.whl
- dist/simple_harness_sdk-0.1.0.tar.gz
- docs/release/v0.1.0.md
- docs/consumers/aiphone-handoff.md
- .github/workflows/release.yml
- .github/workflows/platform-tests.yml

当前分支：codex/sdk-v0.1-foundation
当前提交：88e19eb
测试状态：1122/1122 passing
```

---

## 下一步行动

**请告诉我你希望使用哪个方案：**

**A) 选项 1** - 创建 GitHub 仓库并自动发布（推荐）
**B) 选项 2** - 创建仓库但手动上传 artifacts
**C) 选项 3** - 暂时仅本地使用

我会根据你的选择继续执行相应步骤。

---

**准备状态：** ✅ 所有代码和文件就绪，等待 GitHub 仓库创建

