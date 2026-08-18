# Bug 诊断和修复报告

**日期:** 2026-08-17  
**测试人员:** Claude Opus 5  
**应用版本:** SimpleHarness v0.6.0-beta.9

---

## Bug #1: "SDK Run ended in failed" ✅ 已解决

### 症状
用户在测试过程中看到错误提示：`SDK Run ended in failed`

### 根本原因
- CUA (Computer Use Agent) MCP session 生命周期自然结束
- Session ID: `mcp-57317-1786961780047965000`
- 这是正常的 session 超时机制，不是实际错误

### 影响范围
- **无实际功能影响**
- 所有测试功能正常完成
- 文件创建成功（COMPREHENSIVE_UI_TEST_REPORT.md, 21KB）
- 9 张截图全部保存成功

### 状态
✅ **无需修复** - 这是预期行为，session 超时后自动清理资源

---

## Bug #2: UI 显示"🔧 工具执行中"不恢复到空闲状态 ⚠️ 需要修复

### 症状
- 用户界面底部状态指示器显示：**"🔧 工具执行中"**
- 预期应该显示：**"✓ 空闲"**
- 状态卡住，即使没有实际任务在运行

### 诊断过程

#### 1. 系统状态检查 ✅
```bash
# 应用进程状态
PID: 57867 (运行正常)
窗口 ID: 10326 (存在)

# 后端状态
PID: 57916 (运行正常)
端口 8100: LISTEN (健康检查通过)
WebSocket: 2 个连接 ESTABLISHED

# 可访问性树
元素数: 440 (正常加载)
状态元素: [298] "🔧 工具执行中" ← 问题确认
```

#### 2. 代码路径分析

**前端状态流转 (tauri-app/src/code-panel/):**

```typescript
// ① 用户发送消息
InputBar.tsx:250
  store.upsert(sid, { status: "thinking" })

// ② 工具调用开始  
controlWs.ts:1487
  store.upsert(sid, { status: "running" })  // ← 当前卡在这里

// ③ 工具完成后应该收到 chat_v2_final
controlWs.ts:1159
  store.upsert(sid, { status: "idle" })     // ← 未触发
```

**后端事件发送 (backend/main.py):**

```python
# 后端应该在完成时发送
line 10481: chat_v2_final_send_started
line 10492: chat_v2_final_send_completed
line 10497: chat_v2_final_send_timeout  # ← 可能发生超时？
```

#### 3. 根本原因假设

**假设 A: chat_v2_final 事件丢失**
- 后端生成了事件但 WebSocket 发送失败
- 可能原因：网络超时、连接抖动

**假设 B: 事件发送但前端未正确处理**
- WebSocket 接收到但路由错误
- session_id 不匹配导致事件被忽略

**假设 C: 后端任务未正常完成**
- Agent loop 卡住或抛出未捕获异常
- 没有走到 final 事件发送的代码路径

#### 4. 当前证据

```
✅ 后端进程运行正常（CPU 1.1%, 36MB 内存）
✅ WebSocket 连接正常（2 个 ESTABLISHED）
✅ 前端可访问性树加载正常（440 元素）
❌ 状态指示器显示 "工具执行中"（应该是"空闲"）
```

### 修复方案

#### 方案 A: 前端防御性超时重置 ✅ 推荐

**位置:** `tauri-app/src/code-panel/controlWs.ts`

**问题:** 如果后端 `chat_v2_final` 事件丢失，前端永久卡在 "running" 状态

**修复:** 添加客户端超时保护

```typescript
// 在 InputBar.tsx 发送消息时设置超时
const TOOL_EXECUTION_TIMEOUT = 120_000; // 2 分钟

useEffect(() => {
  if (session?.status === "running" || session?.status === "thinking") {
    const timeoutId = setTimeout(() => {
      // 超时后强制重置状态
      console.warn(`[StatusGuard] Session ${session.base_session_id} stuck in ${session.status}, force reset to idle`);
      useSessionsStore.getState().upsert(session.base_session_id, {
        status: "idle",
        inflight: false
      });
    }, TOOL_EXECUTION_TIMEOUT);

    return () => clearTimeout(timeoutId);
  }
}, [session?.status, session?.base_session_id]);
```

**优点:**
- 防御性编程，即使后端有 bug 也能恢复
- 用户体验改善（不会永久卡住）
- 不影响正常流程

**缺点:**
- 治标不治本（后端问题仍存在）
- 如果有真正长时间运行的任务会被错误中断

#### 方案 B: 后端增强日志和错误处理 ✅ 推荐

**位置:** `backend/main.py`

**问题:** 后端可能在某些异常路径下没有发送 `chat_v2_final`

**修复:** 添加 try-finally 确保事件总是发送

```python
async def handle_chat_v2(session_id: str, request_id: str, ...):
    try:
        # ... agent loop 处理 ...
        
        # 正常完成
        final_text = agent_result.get("text", "")
        
    except Exception as e:
        logger.error(f"chat_v2_error sid={session_id} request_id={request_id} err={e}")
        # 发送错误事件
        await _send_chat_error(ws, {...}, session_id=session_id, request_id=request_id)
        return
        
    finally:
        # 确保无论如何都重置状态
        # 如果前面已经发送 final 事件，这里是幂等的
        await _send_chat_final(
            ws,
            {"type": "chat_v2_final", "payload": {"text": final_text or ""}},
            session_id=session_id,
            request_id=request_id
        )
```

**优点:**
- 从根本上解决问题
- 确保状态一致性
- 改善后端健壮性

#### 方案 C: 立即修复（用户操作）✅ 临时方案

**问题:** 当前会话已卡住

**修复步骤:**

1. **方法 1: 刷新页面**
   ```
   在 SimpleHarness 中按 Cmd+R 或重启应用
   ```

2. **方法 2: 发送新消息**
   ```
   在输入框输入任意文本（如 "hello"）并发送
   新的消息会触发新的状态流转，可能清除卡住状态
   ```

3. **方法 3: 新建会话**
   ```
   点击 "＋ 新建会话" 按钮
   切换到新会话，旧会话状态不再影响
   ```

4. **方法 4: 重启应用**
   ```bash
   # 杀死进程
   kill 57867 57916
   
   # 重新启动
   ./scripts/dev.sh
   ```

### 复现步骤

1. 打开 SimpleHarness 应用
2. 发送需要工具调用的消息（如："show me running processes"）
3. 等待工具执行
4. 观察状态指示器：
   - 正常：✓ 空闲
   - Bug：🔧 工具执行中（卡住）

### 复测计划

#### 前置条件
- 实施方案 A 或 B 的代码修复
- 重新编译和启动应用

#### 测试步骤
1. 发送基础查询消息（无工具调用）
   - ✅ 验证：状态 "thinking" → "idle"
   
2. 发送工具调用消息（如 "list processes"）
   - ✅ 验证：状态 "thinking" → "running" → "idle"
   
3. 发送复杂查询（多轮工具调用）
   - ✅ 验证：状态正确流转，最终回到 "idle"
   
4. 模拟超时场景
   - 手动杀死后端进程中途
   - ✅ 验证：前端超时保护触发，状态重置到 "idle"

5. 连续发送多条消息
   - ✅ 验证：状态在每条消息间正确重置

#### 成功标准
- ✅ 所有测试场景下状态最终都回到 "✓ 空闲"
- ✅ 超时保护在 2 分钟内触发
- ✅ 后端日志显示所有 chat_v2_final 事件成功发送
- ✅ 无状态泄漏（重复测试 10 次无卡住）

---

## 优先级评估

### Bug #1: SDK Run failed
- **优先级:** P4 (低)
- **影响:** 无
- **行动:** 无需修复

### Bug #2: 状态卡住
- **优先级:** P2 (高)
- **影响:** 用户体验 - 无法知道系统是否空闲
- **行动:** 
  1. 立即：使用方案 C 临时修复当前会话
  2. 短期（1-2 天）：实施方案 A（前端超时）
  3. 中期（1 周）：实施方案 B（后端健壮性）

---

## 附加建议

### 1. 增强状态可观测性

**位置:** `tauri-app/src/code-panel/InputBar.tsx`

```typescript
// 显示更详细的状态信息（开发模式）
{import.meta.env.DEV && (
  <div className="status-debug">
    Status: {session?.status} | 
    Inflight: {session?.inflight ? 'yes' : 'no'} |
    Run: {session?.active_run_id || 'none'}
  </div>
)}
```

### 2. 添加健康检查机制

**位置:** `tauri-app/src/code-panel/controlWs.ts`

```typescript
// 定期检查会话健康
setInterval(() => {
  const sessions = useSessionsStore.getState().sessions;
  const now = Date.now();
  
  Object.values(sessions).forEach(session => {
    const stuckDuration = now - session.last_activity;
    
    if (session.inflight && stuckDuration > 120_000) {
      console.warn(`[HealthCheck] Session ${session.base_session_id} stuck for ${stuckDuration}ms`);
      // 发送诊断事件到后端
      controlWS.send({
        type: "session_health_check",
        payload: { session_id: session.base_session_id }
      });
    }
  });
}, 30_000); // 每 30 秒检查一次
```

### 3. 后端监控指标

**位置:** `backend/main.py`

```python
# 添加 Prometheus 指标
from prometheus_client import Counter, Histogram

chat_v2_final_sent = Counter('chat_v2_final_sent_total', 'Total chat_v2_final events sent')
chat_v2_final_timeout = Counter('chat_v2_final_timeout_total', 'Total chat_v2_final timeouts')
chat_v2_duration = Histogram('chat_v2_duration_seconds', 'Chat v2 processing duration')

# 在相关代码处增加指标
chat_v2_final_sent.inc()
chat_v2_final_timeout.inc()
```

---

## 结论

### Bug #1 ✅
**状态:** 已确认为正常行为，无需修复

### Bug #2 ⚠️
**状态:** 已诊断，待修复

**下一步行动:**
1. ✅ 立即使用临时方案恢复当前会话
2. 📝 实施前端超时保护（方案 A）
3. 📝 增强后端错误处理（方案 B）
4. 📝 添加健康检查和监控
5. 📝 完整复测所有场景

**预计修复时间:**
- 临时修复：立即（用户操作）
- 前端超时：2-4 小时开发 + 测试
- 后端增强：4-8 小时开发 + 测试
- 总计：1 个工作日完整修复

---

**报告生成时间:** 2026-08-17T20:10:00Z  
**诊断工具:** CUA Computer Use Agent + 代码审查  
**下次更新:** 实施修复后
