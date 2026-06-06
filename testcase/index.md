# DeskPet 手工测试用例索引

> **用途**: 登记本仓库所有**手工测试用例**（人工一步步执行、带预期结果的 testcase）。
> 每新增一份手工测试文档，在下表追加一行，写清**测试范围**与**目的**。
> 自动化测试（pytest/vitest/cargo）不在此登记，那些放各 `plans/*/0X-TDD.md`。
>
> **最后更新**: 2026-06-06

---

## 手工测试用例清单

| 文档 | 被测功能 / 范围 | 测试目的 | 用例数 | 是否需 windows-mcp |
|---|---|---|---|---|
| [status-file-manual-test.md](./status-file-manual-test.md) | STATUS 全局项目状态文件 + README/CLAUDE.md 链接 + STATUS 更新纪律 | 验证状态文件结构完整、两处入口链接可达、更新纪律已正式落地且可执行 | 6 (TC-01~06) | 否（纯文档/链接核对） |
| [tool-layer-manual-test.md](./tool-layer-manual-test.md) | 整个 deskpet 工具层：工具注册/发现、用户级触发（PPT/Excel/Word/PDF/OCR/图片/整理/网页）、artifact 信封、receipt 落盘、verify gate、circuit breaker、permission gate、toolset 门控、tool_search、agent_parallel、workspace 沙箱 | 验证「用户真的用得了」（A 类 UI 真测）+「内部契约/开关对」（B 类配置核对）；覆盖 last-mile D1~D12 + v3 WI-T2.1/T5.1 + companion v1 agent_parallel | 17 (A1~A5 + B1~B12) | **A 类 5 例需**（UI 真测+截图）；B 类 12 例为配置/脚本核对 |
| [goal-completion-manual-test.md](./goal-completion-manual-test.md) | goal-completion 升级 FP-3/4/5：FP-3 自我纠错闭环（verify 拦伪完成→结构化反思→真重规划→二次产物 / goal_text 对照 / 未来时不误判 / relay 降级 / 重试上界）、FP-4 记忆+人格（重启后跨会话召回 / 改偏好下轮反映 / 偏好冲突 / Pin 不衰减 / B-10 双写钩 / 人格红线不污染完成判定 / flag-OFF 字节基线）、FP-5 Skills（强匹配正文自动载 / 压缩后追问原目标 / 技能自创卡→真坐标保存→SKILL.md 落盘→新 session 复用 / 拒绝 / 5min 超时 / trivial 不弹 / 压缩后重挂） | 覆盖三个 FP 的全部手测门验收点 + 边界/易错场景（未来时态、relay 故障、死循环上界、偏好冲突、flag-OFF 负向、超时 reject、trivial turn）；招牌真机链 TC-3.1/4.1/5.3 | 22 (FP-3 7 + FP-4 7 + FP-5 8) | **是**（绝大多数为 windows-mcp 真模拟人 ✅，少数内部时序/降级/超时为 🟡log+后端核对） |

---

## 约定

- **命名**: `<功能简称>-manual-test.md`，跨迭代的大功能可带日期前缀 `YYYY-MM-DD-`。
- **每份文档结构**: 顶部写被测功能/对应 commit/环境 → 测试前置 → 逐条 TC（目的 + 步骤表 + 预期结果 + 判定）→ 结果汇总表。
- **预期结果**: 每一步都要有可观测的预期，避免"应该没问题"这类无法判定的描述。
- **windows-mcp 真测纪律**: 凡涉及桌宠 UI 交互的用例，必须按 `CLAUDE.md` 手工测试纪律走真模拟点击 + 截图证据，不能用单测/协议层替代（见 CLAUDE.md「🔒 手工测试纪律」）。
- **结果存档**: 执行后的截图/日志证据放 `plans/manual-results-<date>/`，本目录只放**用例定义**。
