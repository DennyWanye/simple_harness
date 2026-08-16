# SDK v0.1.0 发布状态 - 权限受阻

**日期：** 2026-08-15  
**问题：** GitHub token 缺少 `workflow` scope，无法推送包含 `.github/workflows/` 的代码

---

## 当前状态

### ✅ 完全就绪
- SDK 代码：1122/1122 测试通过
- Wheel 已构建：`dist/simple_harness_sdk-0.1.0-py3-none-any.whl`
- SHA256：`d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91`
- 所有文档完成（8 份）
- 产品仓库已推送

### ⚠️ 权限问题
```
Token scopes: 'admin:public_key', 'gist', 'read:org', 'repo'
缺少: 'workflow'
```

错误信息：
```
refusing to allow an OAuth App to create or update workflow 
`.github/workflows/ci.yml` without `workflow` scope
```

---

## 解决方案

### 方案 A：更新 GitHub CLI 权限（最简单）

```bash
# 刷新 token 添加 workflow scope
gh auth refresh -h github.com -s workflow

# 然后推送
cd /Users/denny/projects/simple-harness-sdk
git push -u origin codex/sdk-v0.1-foundation
git tag -a v0.1.0 -m "SDK v0.1.0 - Foundation Release"
git push origin v0.1.0
```

**注意：** `gh auth refresh` 会打开浏览器让你重新授权，需要你手动确认。

### 方案 B：创建新分支不含 workflows

```bash
cd /Users/denny/projects/simple-harness-sdk

# 创建临时分支，移除 workflows
git checkout -b release/v0.1.0-no-workflows codex/sdk-v0.1-foundation
git rm -r .github/workflows/
git commit -m "temp: remove workflows for initial push"

# 推送临时分支
git push -u origin release/v0.1.0-no-workflows

# 创建 tag
git tag -a v0.1.0 -m "SDK v0.1.0 - Foundation Release"
git push origin v0.1.0

# 之后手动在 GitHub 上添加 workflows 文件
```

### 方案 C：直接创建 GitHub Release（推荐）

跳过推送 workflows，直接创建 Release：

```bash
cd /Users/denny/projects/simple-harness-sdk

# 使用 gh CLI 创建 Release
gh release create v0.1.0 \
  dist/simple_harness_sdk-0.1.0-py3-none-any.whl \
  dist/simple_harness_sdk-0.1.0.tar.gz \
  --title "Simple Harness SDK v0.1.0 - Foundation Release" \
  --notes-file docs/release/v0.1.0.md \
  --repo DennyWanye/simple-harness-sdk

# 生成 SHA256SUMS
cd dist
sha256sum simple_harness_sdk-0.1.0-py3-none-any.whl > SHA256SUMS
sha256sum simple_harness_sdk-0.1.0.tar.gz >> SHA256SUMS

# 添加到 Release
gh release upload v0.1.0 SHA256SUMS --repo DennyWanye/simple-harness-sdk
```

---

## 推荐执行顺序

**我推荐方案 C（直接创建 Release）**，因为：

1. ✅ 不需要修改 token 权限
2. ✅ 立即发布 wheel 供使用
3. ✅ 可以稍后手动添加 workflows
4. ✅ GitHub CLI 有足够权限创建 Release

**执行步骤：**

```bash
cd /Users/denny/projects/simple-harness-sdk

# 1. 创建并推送 lightweight tag（不需要 workflow scope）
git tag v0.1.0
git push origin v0.1.0

# 2. 创建 Release 并上传 artifacts
gh release create v0.1.0 \
  dist/simple_harness_sdk-0.1.0-py3-none-any.whl \
  dist/simple_harness_sdk-0.1.0.tar.gz \
  --title "Simple Harness SDK v0.1.0 - Foundation Release" \
  --notes "$(cat docs/release/v0.1.0.md)" \
  --repo DennyWanye/simple-harness-sdk

# 3. 生成并上传 SHA256SUMS
cd dist
echo "d9a1d4f94f826cdf97fb1c23085c85e727400a92f725c7022b0ebf63a18f4d91  simple_harness_sdk-0.1.0-py3-none-any.whl" > SHA256SUMS
sha256sum simple_harness_sdk-0.1.0.tar.gz >> SHA256SUMS
gh release upload v0.1.0 SHA256SUMS --repo DennyWanye/simple-harness-sdk
```

---

## 后续工作

Release 创建后：

1. **验证 Release：** https://github.com/DennyWanye/simple-harness-sdk/releases/tag/v0.1.0
2. **添加 workflows（可选）：** 
   - 方案 1：更新 token 权限后推送
   - 方案 2：通过 GitHub web UI 手动添加文件
3. **通知相关方：**
   - AIPhone 团队可以下载使用
   - 产品团队继续 v0.2.0 集成

---

## 文件位置

所有就绪的文件：

```
SDK 代码：
/Users/denny/projects/simple-harness-sdk/

Wheel 和 sdist：
/Users/denny/projects/simple-harness-sdk/dist/simple_harness_sdk-0.1.0-py3-none-any.whl
/Users/denny/projects/simple-harness-sdk/dist/simple_harness_sdk-0.1.0.tar.gz

Release notes：
/Users/denny/projects/simple-harness-sdk/docs/release/v0.1.0.md

Workflows（待推送）：
/Users/denny/projects/simple-harness-sdk/.github/workflows/release.yml
/Users/denny/projects/simple-harness-sdk/.github/workflows/platform-tests.yml
```

---

## 我能做什么

如果你同意方案 C，我可以立即执行：

1. ✅ 推送 lightweight tag
2. ✅ 使用 gh CLI 创建 Release
3. ✅ 上传 wheel 和 sdist
4. ✅ 生成并上传 SHA256SUMS

**请告诉我是否执行方案 C？**

---

**状态：** ⏸️ 等待权限或方案确认  
**准备度：** ✅ 100% 就绪，只差最后推送步骤
