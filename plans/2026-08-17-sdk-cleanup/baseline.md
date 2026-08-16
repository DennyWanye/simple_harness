# 绿色基线（Phase 3 执行前）

## 执行时间

2026-08-17（任务启动当日）

## Git HEAD

```
1ec95c37 feat(sdk): complete SDK v0.1.1 integration and product adapters
```

## pytest 基线

运行命令：
```bash
cd backend
.venv/bin/python -m pytest \
  --ignore=tests/harness_simplification \
  --ignore=tests/test_context_os_payload.py \
  --ignore=tests/test_deepresearch_quality_benchmark.py \
  --ignore=tests/test_deepresearch_report_quality_acceptance.py \
  --ignore=tests/test_deepresearch_v4_intelligence.py \
  --ignore=tests/test_session_model_run_visibility_smoke.py \
  --tb=no -q
```

结果：
```
79 failed, 6636 passed, 47 skipped, 9 deselected, 3 warnings in 538.31s (0:08:58)
```

### 已知 pre-existing 失败（与 harness 无关，不是本次改动范围）

| 文件 | 失败原因 |
|------|---------|
| `tests/test_ppt_full_page_workflow.py` (4 tests) | PPT 布局逻辑回归，已有失败 |
| `tests/test_window_use_tool.py` (7 tests) | Windows 原生 API 测试，macOS 不支持 |
| `tests/test_workflow_eval_cli.py` (8 tests) | workflow eval CLI 已有失败 |
| 其余约 60 个失败 | 需确认，目测为已有失败 |

### 与 harness 相关的测试状态

- **ImportError**: 0 个（基线中无 harness 模块导入失败）
- **harness 相关 collection ERROR**: 排除的 11 个文件（`scripts.acceptance`/`scripts.bench`/`scripts.e2e` 缺失，pre-existing）

## 排除的测试文件（pre-existing collection errors，与 harness 无关）

| 文件 | 错误原因 |
|------|---------|
| `tests/harness_simplification/*.py` (6 files) | `No module named 'scripts.acceptance'` / `scripts.bench` |
| `tests/test_context_os_payload.py` | `No module named 'scripts.e2e'` |
| `tests/test_deepresearch_*.py` (3 files) | `No module named 'scripts.acceptance'` |
| `tests/test_session_model_run_visibility_smoke.py` | `No module named 'backend'` |

## 验收门槛（Phase 5 目标）

本次清理完成后，pytest 必须满足：
- ✅ passed ≥ 6636（不得新增失败）
- ✅ 零个 `ImportError` 涉及 `deskpet.harness.*`
- ✅ `grep -r "deskpet\.harness" backend/ --include="*.py" | grep -v "\.pyc"` 返回 0 行
- ✅ SDK adapters 测试全部通过

## 下一步

执行 plan.md Phase 1（引用分析）→ Phase 2（清理 main.py）→ Phase 3（删除核心模块）
