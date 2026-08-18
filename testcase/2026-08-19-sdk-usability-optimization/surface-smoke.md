# TO-R1 全表面冒烟清单 — 启动装配改动后的 full-surface 触发

> 绑定：**TO-R1**（change-risk，对应失败模式 **FAIL-2**：0.1.2 与宿主 10-Port 用法不兼容导致生产链路回归）
> 触发依据：acceptance.md 测试义务矩阵——Slice 1 改启动装配链路 → full-surface smoke 强制。
> 执行前提：应用已通过 `./scripts/dev.sh` 启动，后端在 `127.0.0.1:8100`，Vite 在 `127.0.0.1:5173`。
> 通用断言口径：每项打"最小一枪"，响应**不得**为 404 / 500 / 显式降级态（degraded / budget_exceeded / fail-closed 拒绝）。连接级失败（ECONNREFUSED / WS 握手失败）视同 FAIL。

---

## §0 既有脚本复用总表（不重造轮子）

| 复用脚本 | 覆盖表面 | 跑法 |
|----------|----------|------|
| `scripts/verify_sdk_wheel.py` | §1 vendor 工件完整性（TO-S1-1 顺带复核） | `python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl` |
| `scripts/e2e_smoke.py` | §2 /health、§3 /metrics、§6 WS budget_status、§6 WS provider_test_connection、§5 WS chat | 后端需 `DESKPET_DEV_MODE=1`（secret 旁路）；`python scripts/e2e_smoke.py`（依赖 `websockets` 包） |
| `scripts/e2e/e2e_text_chat.py` | §5 主聊天 WS 入口三轮对话 | `python scripts/e2e/e2e_text_chat.py --secret <secret>`（DEV_MODE 下 secret 任意） |
| `scripts/test_websocket_interactions.py` | §5 chat + tool_use / chat_v2_final 观察 | `python scripts/test_websocket_interactions.py`（注意：脚本内硬编码 secret 可能过期，失败时先核 secret 再判 FAIL） |
| `scripts/smoke_test_app_startup.py` | SDK import + 后端独立启动 | **互斥警告**：该脚本自己 `uv run python main.py` 拉后端，与 dev.sh 同时跑会撞 8100 端口。只能在 dev.sh 未运行时单独使用，且其 "old harness imports" 检查项若因切片演进失效，以 import 错误是否涉及 simple_harness SDK 为准判 |

---

## §1 vendor 工件 / SDK 身份（预检，冒烟前）

| # | 表面 | 最小一枪 | 断言 |
|---|------|----------|------|
| 1.1 | vendor wheel 完整性 | `shasum -a 256 backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl` | 输出与 SDK 仓库 `dist/` 同版本 wheel SHA 逐字符一致（TO-S1-1 顺带复核；预期值见 plan「已核实的关键事实」） |
| 1.2 | 校验脚本 | `python scripts/verify_sdk_wheel.py backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl` | exit 0（PASS），非校验拒绝 |
| 1.3 | 装入 venv 的版本 | `cd backend && uv run python -c "import simple_harness; print(simple_harness.__version__)"` | 输出 `0.1.2` |

## §2 后端 health / ready

| # | 表面 | 最小一枪 | 断言 |
|---|------|----------|------|
| 2.1 | `/health` | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/health` | `200`（e2e_smoke.py 第 1 项同口径，body 含 `"status":"ok"`） |
| 2.2 | ready 端点 | **需执行期从路由注册表枚举**——既有脚本只见 `/health` 与 `/metrics`，未发现独立 ready 路径线索。执行期枚举后端路由表确认有无 `/ready`（或等价）；有则打一枪 | 若存在：非 404/500；若枚举确认不存在：记录"无独立 ready 端点，health 即就绪信号"，不判 FAIL |

## §3 /metrics（可观测面）

| # | 表面 | 最小一枪 | 断言 |
|---|------|----------|------|
| 3.1 | `/metrics` | `curl -s http://127.0.0.1:8100/metrics \| head -5` | 200 且返回 Prometheus 文本（DEV_MODE 旁路鉴权；e2e_smoke.py 第 2 项同口径） |

## §4 SDK runtime ready slot

| # | 表面 | 最小一枪 | 断言 |
|---|------|----------|------|
| 4.1 | ready slot 发布 | 在 dev.sh 输出中 `grep "sdk_runtime_ready"` | 出现该行且无后续 fail-closed 拒绝 / 装配 traceback（与 cold-1-manual-test.md Step 3 同一份日志证据可复用） |
| 4.2 | fail-closed 未误伤 | `grep -iE "sha.*mismatch|verify_sdk_candidate|fail.closed" <dev.sh 日志>` | 无命中（命中即 FAIL，同步怀疑 S1-AC-2） |

## §5 主聊天 WebSocket 入口

| # | 表面 | 最小一枪 | 断言 |
|---|------|----------|------|
| 5.1 | `/ws/control` chat 单轮 | 复用 `scripts/e2e_smoke.py`（第 5 项 chat→chat_response）或 `scripts/e2e/e2e_text_chat.py` | 收到 `chat_response`（或 `chat_v2_final`）且 text 非空、无 budget_exceeded；60s 超时 |
| 5.2 | WS 握手本身 | 上一条脚本连得上即证明；如需独立验证：`websockets.connect("ws://127.0.0.1:8100/ws/control?secret=dev&session_id=smoke")` | 握手成功（非 404/403/连接拒绝） |

## §6 四视图对应的后端面

> 既有脚本中未出现 Skills / Artifacts / Sessions / Settings 的专用 HTTP 路由线索（grep scripts/ 仅见 `/health`、`/metrics`、`/ws/control`、`/ws/audio`）——四视图很可能主要走 `/ws/control` 复用消息通道。以下每项给"已证实可打的一枪"+ 需执行期枚举的部分。

| # | 视图 | 最小一枪 | 断言 |
|---|------|----------|------|
| 6.1 | Settings | 复用 e2e_smoke.py 第 4 项：WS `provider_test_connection`（垃圾 key → `ok:false`） | 收到 `provider_test_connection_result` 且 `ok is False` 带 error 字段——证明 Settings 的 provider 配置通道活着（坏消息通道 != 404/500，结构正确即过） |
| 6.2 | Sessions | WS `budget_status`（e2e_smoke.py 第 3 项，payload 含 spent/remaining 等字段）证明会话控制面活着；Sessions 列表的专用路由**需执行期从路由注册表枚举**（如存在 `/api/sessions*`，打一枪 GET） | budget_status payload 四字段齐全；若枚举出 sessions 路由：非 404/500 |
| 6.3 | Skills | **需执行期从路由注册表枚举**（候选形态 `/api/skills*` 或 WS 消息类型）；枚举出后打最小一枪（列表类 GET 或 WS list 消息） | 非 404/500；返回结构化列表（空列表也算过） |
| 6.4 | Artifacts | **需执行期从路由注册表枚举**（同上，候选 `/api/artifacts*`）；打最小一枪 | 非 404/500；返回结构化列表（空列表也算过） |
| 6.5 | `/ws/audio` | 脚本中确认该路径存在（test_workflow_tool_integration 等引用）；最小一枪 = WS 握手 | 握手非 404；若鉴权要求导致 403 属预期可记录为 SKIP（本 slice 未触碰音频面，记录即可） |

## §7 执行期路由枚举方法（供 2.2 / 6.2–6.4 使用）

执行期用以下任一方式枚举真实路由表（black-box，不读实现文件内容，只列注册表）：

```bash
# 方式 A：FastAPI OpenAPI  schema（DEV_MODE 下通常可访问）
curl -s http://127.0.0.1:8100/openapi.json | python3 -c "import json,sys; [print(p) for p in sorted(json.load(sys.stdin)['paths'])]"

# 方式 B：路由注册表直接枚举（在后端 venv 内，仅列路由不读实现）
cd backend && uv run python -c "
import main  # noqa -- 仅取 app 对象
for r in main.app.routes:
    print(getattr(r, 'methods', {'WS'}), getattr(r, 'path', r))"
```

枚举输出存证据，与 6.1–6.4 的断言结果一一对应打勾。

---

## 总结判（TO-R1 PASS 条件）

- §1–§5 全部通过；
- §6 中：6.1/6.2 的 WS 项通过；6.2–6.4 的枚举项若路由存在则全非 404/500，若枚举确认不存在则在证据中显式记录"该视图无专用 HTTP 路由，走 /ws/control 通道"；
- 全部结果（命令 + 输出摘录 + PASS/FAIL 勾选）记入 `evidence/surface-smoke-<timestamp>.md`。

任何一项 FAIL → 触发 FAIL-2 嫌疑，Slice 1 不得宣告完成，回到实现轨排查。
