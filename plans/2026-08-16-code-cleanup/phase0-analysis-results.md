# Phase 0: 代码精简 - 静态分析结果

**分析时间**: 2026-08-16  
**分析范围**: backend/ + tauri-app/src/ + tauri-app/src-tauri/

---

## 1. 废弃模块分析：backend/agent/

### 1.1 模块引用情况

**主要消费者**:
- `backend/main.py`: 21 处 import（AgentLoop、context_manager、tool_use_shim、supervisor 等）
- `backend/llm/`: 2 处 import（context_report、context_messages）
- `backend/providers/`: 2 处 import（context_messages、context_report）
- `backend/tests/`: 192 处 import（大量测试依赖）

**结论**: ❌ **backend/agent/ 不能直接删除**

根据 ARCHITECTURE.md §2:
> `AgentLoop` 已成为 ReAct Driver 引擎，不再是独立 owner

虽然 `backend/agent/` 被标记为 "Legacy P3 agent code"，但：
1. **AgentLoop 仍是生产 ReAct Driver 的核心引擎**
2. main.py 大量依赖 agent 模块（context_manager、tool_use_shim、supervisor 等）
3. 192 个测试文件依赖 agent 模块

**建议**: 保留 `backend/agent/`，但需要：
- 添加明确的模块文档说明其当前角色（ReAct Driver 引擎）
- 识别 agent/ 内部是否有真正的死代码
- 考虑将 agent/ 重命名为更准确的名称（如 `backend/react_driver/`）以避免混淆

---

## 2. 备份文件（可删除）

### 2.1 main.py 备份文件

```
backend/main.py.bak   (628K, 2026-08-17 00:36)
backend/main.py.bak2  (628K, 2026-08-17 01:00)
backend/main.py.bak3  (629K, 2026-08-17 01:01)
backend/main.py.bak4  (629K, 2026-08-17 01:01)
```

**建议**: ✅ **立即删除**，这些是临时备份文件，不应提交到版本控制。

---

## 3. 调试代码分析

### 3.1 Backend print() 语句

- **总数**: 110 处 print() 语句
- **位置**: backend/deskpet, backend/llm, backend/providers, backend/agent

**需要人工审查**:
- 区分调试 print 和合法的 CLI 输出
- structlog 已是标准日志方案，生产代码不应使用 print()
- 测试代码中的 print() 可保留

### 3.2 Frontend console.log

- **总数**: 9 处 console.log
- **位置**: 全部在音频相关模块（App.tsx, useAudioPlayer.ts, useAudioRecorder.ts）

**分析**:
```typescript
// 音频调试日志（合理）
console.log("[App] TTS barge-in — stopping playback");
console.log("[AudioPlayer] AudioContext resumed:", ctx.state);
console.log("[AudioPlayer] barge-in");
console.log("[Recorder] requesting mic access...");
console.log("[Recorder] mic granted:", stream.getAudioTracks()[0].getSettings());
console.log("[Recorder] AudioWorklet recording started");
console.log("[Recorder] stopping");
```

**建议**: ⚠️ **需要决策**
- 这些是音频系统的诊断日志，可能对调试音频问题有帮助
- 选项 A: 保留（作为生产诊断日志）
- 选项 B: 迁移到结构化日志系统
- 选项 C: 添加条件开关（开发模式显示，生产模式静默）

---

## 4. 注释代码块分析

### 4.1 包含注释代码的文件（部分）

```
backend/config.py
backend/main.py
backend/llm/provider_registry.py
backend/tests/test_*.py (多个测试文件)
```

**需要人工审查**: 
- 区分：
  - 注释掉的死代码（应删除）
  - 示例代码注释（应保留）
  - 临时禁用的功能（需要判断是否恢复或删除）

---

## 5. 未使用 imports 分析

**工具**: 需要运行 `flake8 --select=F401` 或 `pylint` 来系统性检测

**预期发现**:
- SDK 切换后可能留下旧的 import 路径
- 重构后未清理的 import

**下一步**: 运行自动化检测工具

---

## 6. 重复代码分析

**工具**: 可使用 `pylint --disable=all --enable=duplicate-code` 或 `jscpd`

**已知高风险区域**:
- `backend/deskpet/sdk_adapters/` 与旧 harness 代码可能有重复
- 多个 workflow definitions 可能共享相似模式

**下一步**: 运行重复代码检测工具

---

## 7. 过度抽象分析

**需要人工审查的模块**:
- 新增的 `backend/deskpet/product_state/` (5 个文件)
- 新增的 `backend/deskpet/tool_catalog/` (4 个文件)
- `backend/deskpet/sdk_adapters/product_workflows/` (5 个文件)

**审查标准**:
- 抽象层是否只有一个实现？
- 接口是否被多处使用？
- 是否为未来扩展预留了不必要的灵活性？

**下一步**: 逐模块审查使用情况

---

## 8. 优先级建议

### 立即可执行（低风险）

1. ✅ **删除 .bak 文件** (backend/main.py.bak*)
2. ✅ **运行 flake8/pylint 检测未使用 imports**
3. ✅ **清理明显的注释代码块**（需人工确认）

### 需要深入分析（中风险）

4. ⚠️ **审查 110 处 print() 语句**（区分调试代码 vs CLI 输出）
5. ⚠️ **决策 frontend console.log 处理方式**
6. ⚠️ **运行重复代码检测**

### 需要架构决策（高风险）

7. ❌ **backend/agent/ 处理**（不能简单删除，需要重新定位或重命名）
8. ❌ **过度抽象审查**（需要理解业务需求）

---

## 9. 风险评估

### 高风险操作
- 删除 `backend/agent/`：❌ 会破坏核心 ReAct Driver
- 批量删除 print()：⚠️ 可能删除合法的 CLI 输出
- 删除"注释代码"：⚠️ 可能误删示例代码

### 低风险操作
- 删除 .bak 文件：✅ 安全
- 删除未使用 imports：✅ 自动工具可靠
- 删除明显的临时调试代码：✅ 人工确认后安全

---

## 10. 下一步行动

根据 plan-test 流程，Phase 0 完成后应进入 Phase 1（写 plan）。

**建议 Phase 1 计划**:

### 阶段 1: 安全清理（构建不受影响）
- 删除 .bak 文件
- 运行并修复 flake8 F401（未使用 imports）
- 验证：pytest + npm build 全部通过

### 阶段 2: 调试代码清理（需人工审查）
- 审查并清理 print() 语句
- 处理 frontend console.log
- 清理注释代码块
- 验证：pytest + npm build + 四个 view 冒烟测试

### 阶段 3: 架构优化（需设计决策）
- backend/agent/ 重新定位或文档化
- 重复代码合并
- 过度抽象简化
- 验证：完整回归测试

**每个阶段独立提交，出问题可单独 revert。**

---

## 附录：检测命令

```bash
# 未使用 imports
cd backend && python -m flake8 --select=F401 . | tee ../plans/2026-08-16-code-cleanup/flake8-unused-imports.txt

# 重复代码（Python）
cd backend && python -m pylint --disable=all --enable=duplicate-code --min-similarity-lines=5 deskpet/ agent/ llm/ providers/

# 注释代码块统计
find backend -name "*.py" -exec grep -l "^#.*\(def\|class\|import\)" {} \; | wc -l

# print() 语句位置
grep -rn "print(" backend/deskpet backend/llm backend/providers backend/agent --include="*.py" | grep -v "# print" > plans/2026-08-16-code-cleanup/print-statements.txt

# 前端 console.log
grep -rn "console\.log\|debugger" tauri-app/src --include="*.ts" --include="*.tsx" | grep -v "node_modules" > plans/2026-08-16-code-cleanup/frontend-debug.txt
```
