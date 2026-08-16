# 验收标准：代码精简 - 移除多余无用代码

## 范围

### 包含
- **死代码**：没有任何引用的函数/类/文件
- **注释代码块**：被注释掉的代码段
- **未使用的 import/依赖**：导入但未使用的模块
- **废弃旧实现**：已被新架构替代但未删除的代码（例如 `backend/agent/` vs `backend/deskpet/`）
- **重复逻辑**：多处实现相同功能的代码
- **过度抽象**：只有一个实现的抽象层
- **调试/临时代码**：console.log, print(), 临时 hack

### 作用范围
- 整个 codebase：backend + frontend (tauri-app/src) + tauri (src-tauri)

### 明确不包含
- ✅ 所有通过测试的功能代码
- ✅ 所有测试代码本身（pytest, vitest）
- ✅ 文档和正常注释（解释性注释保留，注释掉的代码删除）
- ✅ 配置文件和脚本（config.toml, package.json, pyproject.toml, scripts/）

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-1 | Backend 构建通过 | `cd backend && python -m pytest` 全部 PASS，无失败/错误 | 必须 |
| AC-2 | Frontend 构建通过 | `cd tauri-app && npm run build` 成功完成，无错误 | 必须 |
| AC-3 | Frontend 类型检查通过 | `cd tauri-app && npm run typecheck` 无类型错误 | 必须 |
| AC-4 | 应用正常启动 | `./scripts/dev.sh` 启动成功，backend 监听 8100，frontend 监听 5173，WebSocket 连接成功 | 必须 |
| AC-5 | 聊天功能冒烟测试 | Sessions view 可访问，可发送消息，收到 AI 响应 | 必须 |
| AC-6 | 技能功能冒烟测试 | Skills view 可访问，可列出技能，可查看技能详情 | 必须 |
| AC-7 | 工件功能冒烟测试 | Artifacts view 可访问，可列出工件（如有） | 必须 |
| AC-8 | 设置功能冒烟测试 | Settings view 可访问，可查看/修改基本设置 | 必须 |
| AC-9 | 死代码移除 | 使用静态分析工具验证：无未被引用的导出函数/类/文件（允许 entry points） | 必须 |
| AC-10 | 注释代码块移除 | 代码审查验证：无大段注释掉的代码（3行以上连续注释代码） | 必须 |
| AC-11 | 未使用 import 移除 | Linter 验证：Python 无 unused imports (F401), TypeScript 无 unused imports | 必须 |
| AC-12 | 废弃旧实现移除 | `backend/agent/` 目录（旧 P3 agent）已删除或明确标记为 legacy 且不被新代码调用 | 必须 |
| AC-13 | 重复逻辑识别记录 | 生成重复代码检测报告，记录发现的重复逻辑位置（若存在） | 建议 |
| AC-14 | 调试代码移除 | 代码审查验证：production 路径无 console.log/print/debugger（测试代码除外） | 必须 |

## 非功能 / 边界

### 错误态
- 若某模块被错误删除导致构建失败，立即恢复该模块
- 若某功能受影响，优先保留功能代码，标记为"需人工确认"而非直接删除

### 幂等性
- 清理操作应可重复执行，不会因第二次运行产生不同结果

### 性能
- 构建时间不应显著增加（±10% 可接受）
- 应用启动时间不应显著增加（±10% 可接受）

### 兼容性
- 保持现有 API 契约不变（backend WebSocket 协议、IPC 接口）
- 保持配置文件格式兼容

### 回滚能力
- 所有删除操作通过 git commit，可随时 revert
- 分阶段提交：死代码清理 / import清理 / 废弃模块删除 分别提交

## Assurance contract 摘要

- **Profile**: standard（代码质量改进，非安全敏感，但影响范围大）
- **受保护资产**: 
  - ASSET-1: 应用核心功能（聊天/技能/工件/设置）
  - ASSET-2: 所有现有通过的测试
  - ASSET-3: 构建和部署流程
- **可信假设**:
  - TRUST-1: 现有测试套件覆盖了核心功能路径
  - TRUST-2: 静态分析工具（pylint, eslint）能准确识别未使用的 imports
  - TRUST-3: 无引用的函数/类确实是死代码（无动态调用/反射使用）
- **范围内失败**:
  - FAIL-1: 误删仍在使用的代码（通过静态分析漏检）
  - FAIL-2: 删除后破坏隐式依赖（side effects, monkey patching）
  - FAIL-3: 删除废弃代码导致回退路径不可用
- **范围内对手**: 无（非对抗场景）
- **明确范围外条件**:
  - OOS-1: 不处理代码重构/架构优化（仅删除，不改写）
  - OOS-2: 不处理性能优化
  - OOS-3: 不处理代码风格统一（除非是明显的调试代码）
- **最大可接受影响**: 某个非核心功能暂时不可用，但可通过 git revert 快速恢复

## 测试场景矩阵

本任务为确定性清理操作，不涉及输入语义敏感功能，无需场景矩阵。验收依赖自动化测试 + 手工冒烟测试。

## 测试义务矩阵（Test Obligation Matrix）

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---------------|------|-------|------|-------------------|-----------------|
| TO-D1 | delivery | AC-1 | — | 运行 `pytest` 全量测试 | 证明 backend 功能完整性 |
| TO-D2 | delivery | AC-2 | — | 运行 `npm run build` | 证明 frontend 构建正确 |
| TO-D3 | delivery | AC-3 | — | 运行 `npm run typecheck` | 证明 TypeScript 类型完整 |
| TO-D4 | delivery | AC-4 | — | 启动应用并验证端口监听 | 证明应用可正常启动 |
| TO-D5 | delivery | AC-5 | — | 手工测试：发送消息并接收响应 | 证明核心聊天功能可用 |
| TO-D6 | delivery | AC-6 | — | 手工测试：访问 Skills view 并列出技能 | 证明技能功能可用 |
| TO-D7 | delivery | AC-7 | — | 手工测试：访问 Artifacts view | 证明工件功能可用 |
| TO-D8 | delivery | AC-8 | — | 手工测试：访问 Settings view 并修改设置 | 证明设置功能可用 |
| TO-D9 | delivery | AC-9 | — | 运行死代码检测工具（如 vulture for Python） | 证明死代码已清理 |
| TO-D10 | delivery | AC-11 | — | 运行 linter 检查 unused imports | 证明未使用 import 已清理 |
| TO-D11 | delivery | AC-12 | — | 检查 `backend/agent/` 是否存在或是否有引用 | 证明废弃代码已清理 |
| TO-R1 | change-risk | — | FAIL-ROUTE | WebSocket 连接建立和消息收发 | 清理可能影响 IPC 层 |
| TO-R2 | change-risk | — | FAIL-IMPORT | Backend 模块导入链完整性 | import 清理可能破坏导入链 |
| TO-R3 | change-risk | — | FAIL-REGRESSION | 四个核心 view 的路由和渲染 | 代码清理可能影响 UI 层 |

**类型说明**：
- `delivery`：直接证明 MUST AC，required
- `change-risk`：防范本次改动的受影响范围内风险，有明确风险时 required
- `exploratory`：探索性测试，不 required

**风险适用性判断**：
- ✅ 入口层风险（TO-R1）：WebSocket/IPC 是关键入口，必须验证
- ✅ 导入链风险（TO-R2）：删除 imports 可能破坏模块加载
- ✅ UI 层风险（TO-R3）：前端代码清理可能影响路由和组件
- ❌ 并发/幂等：本次清理不涉及共享可变状态
- ❌ LLM 对抗：本次清理不涉及 LLM 输出驱动
- ❌ 冷启动：现有测试已覆盖启动路径

## 完成的定义（DoD 摘要）

- ✅ 全部"必须"条款（AC-1 至 AC-12, AC-14）通过验证
- ✅ 所有 delivery 类型的 test obligation 都有对应的 PASS 结果
- ✅ 所有 change-risk 类型的 test obligation 都有对应的 PASS 结果
- ✅ 无回归：现有功能保持工作
- ✅ 文档已同步：ARCHITECTURE/ 文档中若引用已删除模块需更新
- ✅ 提交整洁：分阶段提交，commit message 清晰描述删除内容
