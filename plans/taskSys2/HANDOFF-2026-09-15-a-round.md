# A 轮交接（下一 session 只读拆 Flash 96）

**已完成（2026-09-15 17:05 CST）：** 只读拆解见 [testPhase1-a96-flash256k-dissection-2026-09-15.md](testPhase1-a96-flash256k-dissection-2026-09-15.md)。S/R 为协议未执行；D/F 过关无知识复用。不要再拆同一批 96，除非修 R 信封后冻新身份做数例。

最后核查：2026-09-15 16:55 CST。用户要把工作交给其他 session。本 session **停止**新实现、新模型调用、重跑。Qwen 96 **按用户决定不补**。

可复制给新 session 的指令在文末。

## 1. 接手结论

A 轮不是「过关」，是两套分开的身份：

| 身份 | 结果 | 能不能当正式 A 轮 Qwen 96 |
|---|---|---|
| N5 Qwen 官方 slack 干净/攻击各一例 | 两例 official utility true；攻击未成功；系统工具观察晋级；KnowledgeUsed 0 | 能当 N5 切片，不是 96 矩阵 |
| Qwen `a96-qwen256k-v2` | **停在 16/96**（操作员停机改 Flash） | **不能**。用户已决定不补。分析时当截断样本，题族不齐 |
| Flash `a96-flash256k-v1` | **96/96 收条**；official utility **19/96**；未知用量 0 | **不能**说成 Qwen A 轮。这是用户另冻的 Flash 256K 身份，也不是 B 轮 512K |

**下一 session 优先：只读拆 Flash 96。** 重点 S=0/24、R=0/24（大量 `ValueError: self-selection has no output`）、D 11/24、F 8/24。弄清是模型不会写规定信封，还是接线/提示问题。不重跑、不加预算、不混 Qwen、不开 512K。

拆完才能决定：修 R/S 后冻**新**身份小复验，还是如实写「这套 Flash 256K 在 S/R 上不可用」。

## 2. 仓库与代码

| 仓库 | 路径 | HEAD（与 origin/main 一致） |
|---|---|---|
| Host | `/Users/denny/projects/simple_harness` | `00d37ea6` docs: record Flash 256K A96 96/96 receipts |
| SDK | `/Users/denny/projects/simple-harness-sdk` | `69d679c` feat(agentdojo): promote successful tool returns as system observations |

工作区交接编写前干净。不要 reset。SDK 公开仓：凭据、用量明细、ignored 证据不要写进去。

生产代码仍是 SDK `69d679c`（含 AgentDojo 系统工具观察晋级）。其后 Host 提交多为文档。

## 3. 必须保持的约束

- 主 session 模型设置不要改。不用 plan-test。不打包、不发布。
- **Qwen 96 不补。** 不把 Flash 19/96 写成 Qwen 成绩。不把 16 例 Qwen 当成满矩阵。
- 正式块不混模型。B 轮仍是闲时 Flash **512K**，这次 256K 不能代替。
- 失败保留，不重跑追 PASS，不加预算。未知用量停该身份的继续扩跑（Flash 96 未知用量是 0）。
- 原始收据只在 `.local-test-evidence/`（gitignore）。Git 只写文字结论、run id、相对路径、SHA。NAS 未配，**不要删**原始证据。
- 源码 UI 可能仍在 backend **18140** / Vite **15173**（v61 快照）。不要再起第二套。A96 进程应已全部退出。
- 权限：auto 默认、不弹窗。报告要有总体 / 当前阶段 / 距上次增量三张表。
- 下一 session 只读拆解：不要打印/复制 `.env` 或 API key。

## 4. 三张进度表

### 总体

| 包 | 已完成切片 | 未关闭 | 时间/用量 |
|---|---|---|---|
| N5 | 确定性正负控；Qwen 官方干净/攻击各一例 | 完整对照集；自然多 Task KnowledgeUsed | 确定性 7.11 秒；Qwen 17 调用 85419 tokens；攻击未成功 |
| N4/A96 Qwen | 12 题 dev hash 已核对 | **不补** 剩余 80 | 停在 16/96；3 未知用量（含超时） |
| N4/A96 Flash256K | **96/96** | S/R 全灭的机制解释；不是 512K | 2120 调用 / 28574880 tokens；utility 19/96 |
| N1 | 共享容量等 | 长时恢复、512K 资格 | 见旧 handoff |
| N2/N3 | 负控、契约修复 | 困难消费收益 | N2 v4 FAIL 保留；小对照 900s smoke FAIL 保留 |
| N6–N8 / B512K | 局部 | 完整 Gaia2 judge、B 轮 | 费用上限未定 |

### 当前（交给你的切片）

| 项 | 事实 |
|---|---|
| 任务 | 只读拆 `a96-flash256k-v1` 的 96 份 result.json |
| 主问题 | S 0/24、R 0/24（24 个 ValueError，样本为 self-selection 无输出）、D 11/24、F 8/24 |
| 进程 | 无 run-a96-flash6 / appworld_service |
| 磁盘 | 约 25 GiB |
| 输出 | 文字诊断 + 是否值得冻新身份；更新 ARCHITECTURE；**不要**开新 96 |

### 本交接编写前的增量（上一 session）

| 事项 | 结果 |
|---|---|
| 停 Qwen A96 | 16 收条；1 例在飞打断，见 operator-stop.json |
| Flash 6 路 96 | 收齐；worker 全 0；未知 0；官方 19 true |
| 用户决定 | 不补 Qwen 96；两套分开写 |

## 5. 证据路径（仅本机）

根：`/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-15/a96/`

**Flash（主分析对象）** `matrix-flash256k-v1/`（约 800 MiB）

- `identity.json` — experiment_id `a96-flash256k-v1`，model `deepseek-flash`，workers 6
- `summary.json` / `progress.json` — completed 96，official_true 19，valid_success 16，errors 24，calls 2120，tokens 28574880，unknown 0
- `episodes/<run_id>/result.json` — **96 份**；用量在 `known_usage_lower_bound`（provider `api.deepseek.com`）
- 失败例可能有 `exception-trace.txt`（R：`self-selection has no output`）
- 监督脚本：`../run-a96-flash6.py`（6 worker，端口 18250–18255，现应无监听）

**Qwen（截断，不补）** `matrix-v2/`（约 140 MiB）

- `progress.json` — completed 16，pending 80
- `operator-stop.json` — 为切 Flash 而停
- `episodes/` — 16 份 result.json；另有 1 个无 result 的打断目录

**N5 Qwen 官方对** `../n5-blackboard/` — slack/user_task_0 clean+attack，`audit.json`

**12 题单** `appworld-taskset.json` — 已核 public instruction SHA-256，dev split

凭据：Flash 用 `.env` 的 `DEEPSEEKER_APIKEY`，官方 `https://api.deepseek.com/v1`，model `deepseek-flash`。Qwen 用 `BaseURLLOCAL` / `MODELLOCAL=qwen38-flash-next`。不要把 key 写入文档。

## 6. 只读拆解要回答的问题

1. S 的 24 例：Agent 交了什么（空、短、无 complete_task、官方评分失败）？是能力还是工具/提示？
2. R 的 24 例：有多少是 `self-selection has no output`？Flash 是否根本没吐规定 JSON 信封？
3. D/F 过的 19 例：有无 `knowledge_reuse_events` / KnowledgeUsed？过关是不是单 Task、没用黑板？
4. 按题：`4fab96f_1` `50e1ac9_1` `68ee2c9_1` 为 0/8，是否全臂失败？
5. 结论只能是：接线/提示 bug（可冻新身份小复验）**或** 模型+协议不匹配（保留 19/96，不追跑）。不要第三种「再跑 96 看看」。

抽样读 result.json 的 runtime/official/error_type/elapsed，不要把整份 prompt 抄进 Git。

## 7. 阅读顺序

1. 本文件  
2. [Flash 96 结论](testPhase1-a96-flash256k-2026-09-15.md)  
3. [N5](testPhase1-n5-blackboard-2026-09-15.md)  
4. 旧总交接 [HANDOFF-2026-09-15.md](HANDOFF-2026-09-15.md)（约束仍有效；N5「未实现」和 A96「未开始」已被本文件覆盖）  
5. `ARCHITECTURE/index.md` 顶部  

## 8. 给新 session 的指令（可整段粘贴）

> 请接手 `/Users/denny/projects/simple_harness/plans/taskSys2/HANDOFF-2026-09-15-a-round.md`。只读拆 Flash A96（`a96-flash256k-v1`，96 份 result.json）。优先解释 S=0/24 和 R=0/24（self-selection 无输出）。不要重跑、不要补 Qwen 96、不要混身份、不要开 Flash 512K、不要打包。证据在 ignored `.local-test-evidence/2026-09-15/a96/matrix-flash256k-v1/`。结论写进 plans 并更新 ARCHITECTURE；原始 JSON 不要 commit。按总体 / 当前 / 距上次三张表报告。未知用量、Qwen 16/96 截断、N5 攻击未成功都必须保留。
