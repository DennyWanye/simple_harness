# DeepResearch v5 Windows 桌面真人 E2E

> 状态：**PARTIAL / FAIL（2026-07-17 多领域真人矩阵已执行；质量门存在阻断缺陷）**
> 驱动：Computer Use，Xiaomi 屏幕，Windows 11 x64 only

## 硬纪律

1. 每个动作前声明 `坐标=(x,y)|动作=...|期望=...`，再做真点击/输入。
2. 中文使用剪贴板粘贴；先真点击输入框聚焦，再粘贴并发送。
3. 每个 case 按“动作前截图 → 真动作 → 关键截图 → 后端/workflow DB/log 判定”取证。
4. 禁止用直接 WebSocket 注入、后端 import、pytest、脚本回放或 boot log 代替 UI 证据。
5. 失败至少尝试 3 种不同 workaround 才可标环境受限；跳过须由用户确认。
6. 启动 Tauri 时让其自行 spawn 唯一 backend/vite；不得手工占用默认端口。

## UI 共通预期

- compact card 无需展开即可看到覆盖维度/总数、有效/第一方来源、当前缺口、质量分/硬失败、已用时/软检查点/续租原因和预计终态。
- 每个 committed stage 恰有一个 stable bubble；默认折叠，展开严格显示“做了什么/得到什么/舍弃什么/仍缺什么/下一步”，`none` 显示为“无”。
- 用户层只显示 provider 命中/空/超时/cooldown 聚合，不显示 raw query、URL、正文、Cookie、prompt、token、异常栈或模型推理。
- 重复/乱序 event、重连和历史加载不刷屏、不回退状态。

## TC-UI-EDU — SC-EDU-01 原问题与持续进度

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 截图主界面；声明坐标后点击输入框，粘贴“帮我调研一下，现在中国小学现在的教育现状和国家下一步计划”，点击发送。 | 新 v5 run 出现单一总体卡；不会先显示伪造的 generate-now 能力。 |
| 2 | 等待 brief/dimensions committed，截图 compact card。 | 能看到六类核心维度总体覆盖、来源、缺口、用时和预计终态；能力由后端投影后才显示“立即用现有证据生成”。 |
| 3 | 声明坐标后展开一个已完成阶段 bubble。 | 展开内容含五个安全字段；无 raw query/URL/正文/秘密数据；折叠/展开有 aria 状态。 |
| 4 | 等待 rescue/lease 变化并连续截图。 | 300 秒健康进展显示为软检查与续租，不显示“卡死”；缺口工作与覆盖变化一致。 |
| 5 | 等待终态并打开最终报告。 | 只有质量全过才是 completed；否则诚实 partial/insufficient。教育报告首屏 3～7 条结论，覆盖/披露六维度并优先官方原文。 |

## TC-UI-AI — SC-AI-01 技术情报报告

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 新 Session 真输入固定 AI 原问题并发送。 | 创建独立 v5 run，不与教育 run 的进度、Cookie、localStorage 或缺口状态串线。 |
| 2 | 展开两个不同完成阶段并截图。 | 每步 action/result/discarded/remaining/next 可理解，stable bubble 不重复。 |
| 3 | 打开终态报告并人工 review。 | 有价值排序、近期变化、成熟度、采用判断、风险与不确定性，不是原子事实平铺；关键判断有就近引用。 |

## TC-UI-NOW — SC-NOW-01 重复点击、重连与强杀重启

> 2026-07-17：步骤 1 与 30 秒 settle 主链已由真实 run `6ea6a3a449c047e98750a84276760bb2` PASS；按钮 accepted，command 0.227 秒内 observed，settle 后唯一 `insufficient_evidence` 终态且硬失败 0。步骤 2～4 与显式 cancel-settle 按钮分支仍 PENDING。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 新 run 进入第二轮 rescue，截图并声明坐标后点击“立即用现有证据生成”。 | 按钮进入 pending→accepted/observed；停止启动新补证，显示当前收敛阶段和 30 秒倒计时。 |
| 2 | 立刻再次点击原位置或用 Enter/Space 重复触发。 | 重复触发被禁用/幂等接受，不出现第二个决策、报告或 Artifact。 |
| 3 | accepted 后断开再恢复 WebSocket，截图重连前后。 | 总体卡、阶段 bubble 和 action 状态一致恢复，不重复消息、不回退。 |
| 4 | 另起一个 run，在 accepted-but-not-observed 窗口强杀 DeskPet，然后从产品入口重新启动。 | durable decision 恢复并继续 settle；不从头补证，不生成两个报告。 |
| 5 | 若 30 秒仍未终态，声明坐标后点击“停止当前收敛并生成”。 | cancel-settle 幂等，正在进行的有界原子操作收敛后形成唯一三态终态。 |

## TC-UI-RECONNECT — 普通运行中重连、历史加载与重复事件

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 在教育 run 运行中截图总体卡和已展开 bubble。 | 记录当前覆盖、来源、stage instance 和 action 状态。 |
| 2 | 用可逆方式让 WebSocket 断开，再恢复连接。 | UI 显示可理解的连接状态；恢复后同一 run/stage 使用稳定 key。 |
| 3 | 对比重连前后并滚动历史。 | 覆盖矩阵、补证轮、lease、bubble 数量和顺序一致；重复/乱序 event 不刷屏。 |
| 4 | 正常关闭并重新启动应用，加载该 Session 历史。 | 持久化状态与重连后相同，终态 late event 不覆盖最终业务状态。 |

## TC-UI-CONTINUE — SC-CONTINUE-01 partial 后继续研究

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 打开一个真实 `partial` 终态，截图旧报告 hash/时间和缺失维度。 | 显示“继续补充调研”；原 report/operation 可定位。 |
| 2 | 声明坐标后点击“继续补充调研”，并快速重复点击一次。 | 只创建一个 child operation；总体卡表明复用旧证据并仅处理缺失维度。 |
| 3 | 在 child 运行中重连一次。 | parent lineage、旧证据数、缺口状态和新租约一致恢复。 |
| 4 | 等待新终态并再次检查旧报告。 | 新报告/operation 可追踪；旧 run/report 不变，没有覆盖或重复 Artifact。 |

## TC-UI-HISTORY — v4 历史兼容与 v5 默认开启

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 从 Session 历史打开一个 v4 run。 | 原 progress/Artifact/terminal 可读，不伪造 v5 覆盖字段或新 control。 |
| 2 | 在新 Session 发送新的 DeepResearch 请求。 | 默认创建 v5 run且能力 ON，无 shadow/灰度话术。 |
| 3 | 在两个 Session 间来回切换。 | v4 与 v5 状态各自稳定，消息、重试/操作能力按版本正确显示。 |

## TC-UI-MATRIX — 多领域输入广度与高证据锚点

以下问题必须按**不同语义类别**统计；重试和 continuation 不增加问题数。

| 类别 | 固定真人输入 | 必须结果 |
|---|---|---|
| AI 技术情报 | “可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？” | 进入 `technology_intelligence`，不是 generic 模板；若有报告，按价值/成熟度/风险组织。 |
| 消费决策 | “请调研并比较 iPhone 17 Pro 与小米 15 Ultra……” | 独立 v5 root run；证据不足时不得给出无依据购买结论。 |
| 近期政策 | “请调研 2026 年中国新能源汽车以旧换新政策……” | 区分现行政策与未来推断，优先国务院/发改委/商务部原文。 |
| 冷门低证据 | “请调研 2026 年中国县域小学部署本地离线 AI 作业批改机器人……” | 无可靠学校/教育局材料时必须 `insufficient_evidence`，不得编造案例和价格。 |
| 高证据官方统计 | “请进行深度调研：基于国家统计局官方资料，分析 2024 年中国人口……” | 应能接纳国家统计局官方材料；若抓取多份文档仍 0 admitted evidence，判 evidence admission FAIL。 |

附加路由变体：将高证据题写成“请基于国家统计局官方资料，调研……”；仍应确定性进入 DeepResearch，不得漂移到普通 ReAct `web_search/web_fetch`。

### 2026-07-17 实际结果

- 五类问题均以真实坐标点击/输入发送；五个 v5 run 均为 `parent_run_id = null` 的独立 root。
- 五个 v5 run 全部 `insufficient_evidence`，质量分 0、有效/第一方来源 0、核心覆盖 0；每个 snapshot 实际记录 14～23 个 IO documents 和 20～24 个 query fingerprints，但 passage refs 均为 0，所有维度包含 `winning_relevance_below_threshold`。
- AI 固定题生成的是 `generic_*` 五维度，未进入 acceptance 要求的 `technology_intelligence`。
- “请基于……调研”变体没有创建 v5 run，而是进入普通 `web_search → web_fetch`；改成“请进行深度调研”后才创建 v5 root。
- 普通工具路径在用户消息流显示了 `web_fetch` 参数和完整 URL，违反安全诊断投影要求。
- v5 UI `elapsed_seconds` 为 126～181 秒，而 workflow DB 根 run 墙钟耗时为 65～92 秒，稳定接近 2 倍。
- fail-closed 三态行为 PASS：系统没有在零 admitted evidence 时伪造完整报告，每个终态均提供 `continue_research`。

详细 run、耗时和截图见计划证据目录 `evidence/win11/manual-matrix-20260717/results.md`。

## 结果

`TC-UI-EDU/RECONNECT/CONTINUE` 已有真人证据；`TC-UI-MATRIX` 的旧失败基线已由后续三类源码复验关闭；
`TC-UI-NOW` 单次点击与 settle 主链 **PASS**，其重复点击/重连/强杀分支及 `TC-UI-HISTORY` 仍为 **PENDING**。不得把 fail-closed 正确等同于研究质量通过。
