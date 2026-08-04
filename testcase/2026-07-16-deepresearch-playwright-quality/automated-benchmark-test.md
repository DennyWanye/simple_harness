# DeepResearch v5 固定场景自动化与 benchmark

> 平台：Windows 11 x64 only
> 状态：**PENDING**
> 禁止依赖：Windows 10、Hyper-V、VM、ISO、Windows Sandbox、系统重启

## 前置

- v5 为新 run 出厂默认路径；Search Gateway 与 bundled Playwright 默认 ON。
- 使用固定 fixture、虚拟时钟和隔离临时目录，不访问用户浏览器 profile。
- 真实网络 benchmark 失败与产品逻辑失败分开记录；不得把外部网络受限改写成 PASS。
- 所有生成式 LLM 调用由 `LLMBudgetLedger` 记录 role、usage、耗时与增益，不保存 prompt。

## TC-AUTO-EDU — SC-EDU-01 政策教育完整性

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 以固定原问题创建 `policy_education` v5 run。 | 生成稳定 brief，包含现状、规模/趋势、城乡或区域均衡、教师/财政、双减/课后服务/负担、国家下一步计划六类核心维度。 |
| 2 | 执行固定 Search Gateway fixture 与真实网络 benchmark。 | 中文相关候选分数非零且严格高于无关候选；每个核心维度先获得 reserved slots。 |
| 3 | 检查 source family、证据准入与覆盖矩阵。 | 国务院/教育部/财政部原文优先；转载不重复计分；每个核心维度可追溯到 passage 和 URL。 |
| 4 | 执行报告综合与质量门。 | 只有总分≥80且无硬失败才为 `completed`；否则为 `partial`/`insufficient_evidence`，不得生成伪完整结果。 |
| 5 | 进行人工专业 review。 | 首屏为 3～7 条直接判断，正文区分已发布任务、分析与未来不确定性，缺失“双减”等维度时明确披露。 |

## TC-AUTO-AI — SC-AI-01 技术情报报告

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 以固定 AI 原问题创建新 run。 | profile 为 `technology_intelligence`，研究维度稳定且不是原子事实列表模板。 |
| 2 | 执行检索、分析和报告综合。 | 证据按维度平衡，关键判断有就近支持，引用不会跨维度错配。 |
| 3 | 运行质量门和人工 review。 | 报告包含价值排序、近期变化、成熟度、采用判断、风险/不确定性；用户正文不泄露内部诊断。 |

## TC-AUTO-PW — SC-PW-01 动态页受控渲染

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 用 fixture server 返回静态抽取为 JS 空壳、bundled Chromium 可渲染正文的页面。 | 静态 FetchExtractService 首先执行并按内容质量判定为空壳。 |
| 2 | 继续同一抓取请求。 | 仅该 URL 调用 bundled Playwright；使用随包 executable，不探测全局 cache、不访问 CDN。 |
| 3 | 检查抽取与清理。 | 有效正文进入证据，page/context 关闭；并发≤2，无孤儿 Chromium/driver。 |

## TC-AUTO-BLOCK — SC-BLOCK-01 CAPTCHA 拒绝

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 提交固定百度安全验证/CAPTCHA fixture。 | Playwright 可以打开页面，但页面被标记为安全验证而非正文。 |
| 2 | 执行 evidence admission 与报告链路。 | 验证页不进入 ranked evidence、citation、coverage 或 source quality 分；报告不出现验证页文本。 |
| 3 | 检查 fallback。 | 不用 Edge/CDP 绕过拒绝，也不尝试破解、登录或提交表单。 |

## TC-AUTO-RESCUE — SC-RESCUE-01 缺口定向救援

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 构造仅一个核心维度 `uncovered`、其他维度充分覆盖的 checkpoint。 | gap queue 只包含缺失维度。 |
| 2 | 让确定性查询本轮无增益。 | 系统记录本轮 query/result/coverage delta，不重复健康维度。 |
| 3 | 在 ledger 预算允许时触发一次 query-strategy LLM，并返回可用官方定向查询。 | 调用有明确 role/触发原因/usage，返回查询只服务缺失维度。 |
| 4 | 返回有效官方来源并重新评估。 | 缺口转为 covered 后立即从队列移除；trace 显示覆盖增益，后续不再重复查询。 |

## TC-AUTO-LEASE — SC-LEASE-01 软检查点续租

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 用虚拟单调时钟推进到 300 秒，并在每个周期增加第一方 family 或核心覆盖。 | 300 秒只触发质量/进展检查，不取消或强制降级。 |
| 2 | 提交 progress observation。 | 记录可量化增益与 120 秒续租原因，只启动仍有价值的 gap work。 |
| 3 | 在 900 秒前达到质量线。 | 立即停止 rescue，生成符合质量门的三态终态，不继续空转。 |

## TC-AUTO-PLATEAU — SC-PLATEAU-01 连续无增益提前停止

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | provider 正常响应，但两个连续 rescue 周期不增加覆盖、第一方 family 或质量分。 | plateau counter 每个 committed 周期恰增一次，重放不重复增加。 |
| 2 | 提交第二个无增益周期。 | 在 900 秒前停止启动新搜索、LLM 和浏览器操作。 |
| 3 | 进入交付。 | 根据已有证据交付 `partial` 或 `insufficient_evidence`，并保存安全 checkpoint。 |

## TC-AUTO-CAP — SC-CAP-01 900 秒安全上限与继续研究

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 用虚拟时钟和持续增益数据推进到 900 秒。 | 到达上限后不再启动新 upstream；不是单个 handler 睡眠或中间强杀。 |
| 2 | 让当前原子操作在有界时间内完成。 | 已完成证据被提交，安全 checkpoint 完整，随后三态交付。 |
| 3 | 从终态触发 continue operation。 | 新 operation 复用旧证据、source family、覆盖状态和 checkpoint，获得新租约；原 run/report 不变。 |

## TC-AUTO-NOW — SC-NOW-01 立即生成幂等控制

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 在第二轮 rescue 且 brief/dimensions 已 committed 时提交 `generate_now`。 | capability 可见；durable decision 通过 CAS 接受，并阻止新 rescue upstream。 |
| 2 | 重复提交同一动作，并模拟重连和 accepted-but-not-observed 重启。 | 同一 decision 只消费一次；状态恢复为 accepted/observed/settled/consumed 的正确阶段。 |
| 3 | 执行 settle。 | 目标 30 秒内进入三态终态；超时进入 `cancel_settle` 能力，不生成两个报告/Artifact。 |

## TC-AUTO-CONTINUE — SC-CONTINUE-01 继续补充调研 lineage

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 准备一个可恢复的 `partial` 终态与有效 snapshot。 | `continue_research` capability 可见，snapshot/pin/retention 均有效。 |
| 2 | 提交 continue 并模拟并发双击。 | child operation 原子创建一次，parent/run lineage 不可变。 |
| 3 | 执行 child。 | 只研究未覆盖维度；复用旧证据/cache；原报告不覆盖；新 operation 可追踪。 |

## TC-AUTO-COMPAT — v1～v4 历史与 v5 默认开启

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 加载 v1/v2/v3/v4 checkpoint、Session、Artifact 和 progress fixture。 | 旧数据可读/恢复，旧 run 不被 v5 节点或质量状态误执行。 |
| 2 | 显式创建 v4 override run。 | 仍使用冻结的 v4 identity/terminal payload，字节兼容。 |
| 3 | 使用默认配置创建新 run。 | 进入 v5，Search Gateway/Playwright/default capability 为 ON；不需要 Hyper-V 或重启。 |

## 结果

所有 testcase：**PENDING**。执行后把命令、退出码、日志、trace、机器 audit、报告
hash 和人工 review 链接写入 `RESULTS.md`；不得仅写“测试通过”。
