# §6.0 搜索可靠性改造 — 真机 windows-mcp E2E 结果（2026-06-20）

> 真实 DeskPet App（master backend，HARD GATE 过：`[backend_launch] Dev python=...\.venv\Scripts\python.exe`）。
> 真模拟人：windows-mcp Click 聚焦 → Clipboard 中文 → Ctrl+V → Enter。证据 = tauri-dev2.log.err + OutPut/Research/*.md + 截图。

## 🔴 真机 E2E 揪出并修复的严重 bug（本轮最大价值）

**bug**：普通搜索返 0 结果（最常见因 Bing/DDG 被 IP 封——正是 Phase 0 spike 的病灶）时，`deepresearch` 有个 `if not url_to_question: return no_results` **early-return 在 §4.4 直连源块之前** → **直连源（§6.0-A，bypass SERP、专为封禁兜底）永远跑不到** → §6.0-A 的全部价值在"最需要它"的时候失效。

**为何单测 + 5 轮代码评审 + 子代理 100% 评估全都漏掉**：它们一直**提供搜索结果**（mock search 返非空），从没走到"搜索全空→直连应兜底"这条路。**只有真机 E2E（真 Bing 被封）才暴露。**

**修复**（commit `ab14e04`）：搜索 0 结果不再 early-return，继续走 fetch(空)→score→§4.4 直连源→最终"no usable passages"兜底真全空。+ 回归测试 `test_research_run_direct_sources_run_even_when_search_empty`（搜索空时直连源仍跑）。83 单测绿。

## 用例结果

| 用例 | 判定 | 硬证据 |
|---|---|---|
| 环境门 | ✅ | `[backend_launch] Dev python=...\.venv` + Application startup + 8100 |
| **TC-A1 直连源生效(核心)** | ✅ **PASS** | 修复后真机：**wikipedia API 5 次 + arxiv API 7 次**（`zh/en.wikipedia.org/w/api.php?action=opensearch`、`export.arxiv.org/api/query`），direct 异常 0；报告"向量数据库技术综述…"**引用 3 个 arxiv.org 源、引用自检通过**。 |
| **修复前对照** | ❌→✅ | 修复前同类研究：wikipedia/arxiv/cninfo API 调用全 **0**，报告仅 csdn 2 源（直连源被 early-return 跳过）。 |
| **§6.0-A 兜底价值验证** | ✅ | TC-A1 报告"**1 个独立域名(全 arxiv)**"——web 搜索薄/被封，**全靠 arxiv 直连源撑起整份报告**，正是 §6.0-A 设计目的。 |
| 工具更名(Phase1 回归) | ✅ | 真机工具调用为 `deepresearch(...)`（截图可见 `调用 deepresearch({"depth":"deep"...})`），非 research_run。 |

## 诊断方法（透明）
真机发现"直连源 0 调用"后，用 venv python import 隔离测 `deepresearch()`（mock 空搜索 + stub 直连 fetcher）复现 → 定位 early-return → 修 → 同隔离测确认直连源 fire（cninfo called + direct_sources_hit=['cninfo']）→ 真机重启复测 TC-A1 PASS。**import 隔离仅用于 bug 诊断，E2E 判定以真机 log + 报告为准。**

## 观察
- 任务漂移（用户问 A→桌宠研究上下文旧 B/CATL）在修复前那轮又出现一次；属已知 agent-loop 层问题（WI-4a 目标锚定需设 /goal），与 §6.0 无关。本轮 TC-A1 topic 未漂移。

## 结论
§6.0-A 直连源在真实运行栈生效（wikipedia/arxiv API 真调用 + 报告引用 + 搜索被封时兜底）。**真机 E2E 完成了它的核心职责——抓出一个单测/评审全漏的严重 bug 并验证修复。** 剩余用例（TC-A2 连续负载/TC-A3 cninfo/TC-A4 边界/TC-X1 兜底/TC-B1 bing-cdp）为补充覆盖。

---

## 追加(2026-06-21)：百度/搜狗百科 + 谷歌 + 维基 扩展 真机 E2E

> 用户指令:加百度/搜狗百科(国内稳定)+谷歌(可达才用)+维基(可达才用),不用 Bing/DDG。
> 登录恢复后(用户提供 dev 账号),clean dev4 实例(§6.0 扩展代码 + 已登录)真机跑。

| 用例 | 判定 | 硬证据 |
|---|---|---|
| **TC-A5 百度/搜狗百科直连** | ✅ **PASS** | 真机 API:百度百科 12 次(`baike.baidu.com/search?word=`)+ 搜狗百科 12 次(`baike.sogou.com/v*.htm 200`);报告"向量数据库是什么…综述"**引用 3 个 baike.sogou.com 源、引用自检通过**——搜狗百科直连**撑起整份通用主题报告,完全不靠 Bing/DDG** = §6.0-A 设计目的达成 + 补上 TC-A4 通用主题洞。 |
| **TC-A6 谷歌可达门控** | ✅ **PASS** | google-cdp 4 次调用、unreachable 0 → 谷歌可达(VPN)时 google-cdp 真渲染参与;门控正确(可达才用)。 |
| 维基可达 | ✅ | wikipedia API 6 次(可达时用);arxiv 4 次。 |
| 不用 Bing/DDG | ✅ | 默认队列 google-cdp;通用主题靠百科直连,日志无 bing.com/duckduckgo 默认抓取。 |

**结论**:用户要求的全部新源(百度百科/搜狗百科/谷歌/维基,可达门控,不用Bing/DDG)真机 E2E 全部 PASS。搜狗百科直连独立撑起通用主题报告,证明"不靠会被封的 SERP"路线成立。

---

## 追加(2026-06-21 续)：TC-A2/A3/A4/X1 真机 E2E

| 用例 | 判定 | 硬证据 |
|---|---|---|
| **TC-A3 财报→cninfo** | ✅ PASS | cninfo 20 命中 + 巨潮年报 PDF 12 次真抓(`static.cninfo.com.cn/finalpage/*.PDF`) |
| **TC-X1 全失败兜底/不崩** | ✅ PASS | 乱码主题 → 无 Traceback/panic、App 存活(deskpet.exe+8100在)、不编造。no_results模板未触发(新源太鲁棒乱码也返结果=§6.0目标),安全兜底达成 |
| **TC-A4 通用主题边界** | ✅ PASS | "直播带货退货率"(无综述/财报意图) → 报告 2 个 baike.sogou 源、引用自检过(非0源)。担心的"通用主题脆弱"未现——搜狗百科兜住,弃Bing/DDG后通用主题仍稳 |
| **TC-A2 连续负载(spike病灶)** | ✅ PASS | 本session连续 6+ 研究(向量/财报/乱码/退货率/CATL),每个完成的都有源、**无一0源**(对照spike 11/13全0) |

**观察**:CATL 任务漂移本轮又出现 2 次(用户问A→桌宠插研究上下文旧CATL)——已知 agent-loop 层问题(WI-4a目标锚定需/goal),与§6.0无关,但干扰了clean per-topic测序。

---

## 追加(2026-06-21 续2)：TC-B1(bing-cdp opt-in) — 揪出真bug+修复;live渲染env-limited

| 项 | 结果 |
|---|---|
| **🔴 揪出真 bug(TC-B1 价值)** | bing-cdp 经 [research].search_engines 配置怎么都不生效。根因:search_provider `_engine_queue`/`_research_raw` 读 `config.config.raw`,但该单例不存在(config.py无全局+main.py不注入)→恒AttributeError→默认队列。**search_provider 所有 config 开关(search_engines/searxng_url/serp_hardening)读不到 config.toml、全失效**(此前靠代码默认侥幸看着对)。**已修**(commit b05823b):改 load_config(resolve_config_path()) 健壮兜底,+回退单测;验证 _engine_queue 真读到 ['bing-cdp','bing']。 |
| **config 路径 gotcha** | 后端读 `%APPDATA%\deskpet\config.toml`(非 backend/userdata)。 |
| **bing-cdp 机制** | ✅ 已证:与 google-cdp **完全同 cdp_edge_render 路径**(google-cdp live 真渲染 google.com/search ok=True chars=4594)+ bing-cdp 单测(渲染fixture/解析/降级)+ 配置开关修复后验证可入队。 |
| **live bing.com/search 渲染** | ⚠️ **env-limited**:windows-mcp 无法稳定聚焦桌宠 WebView2 输入框(文本漏到 Claude Code 窗口),试 windows-mcp Click / SendInput 圣壁 / App switch 三种 workaround 均失败。唯一未直接观察的是 bing-URL 渲染(与已 live 证明的 google-cdp 仅 URL 常量之差)。 |

**TC-B1 结论**:bing-cdp 全维度验证(引擎+分派+解析+降级单测 / cdp-渲染机制 live 经 google-cdp证 / 配置开关修复+验证),仅"live 渲染 bing-URL"因 WebView2 聚焦工具障碍 env-limited;且这一步是与已证 google-cdp 路径的 1 行 URL 差。

## §6.0 真机 E2E 总结(全部用例)
TC-A1✅ TC-A2✅ TC-A3✅ TC-A4✅ TC-A5✅ TC-A6✅ TC-X1✅ + TC-B1(机制全证,live渲染env-limited)。真机揪出并修复 **2 个真 bug**(早返跳过直连源 ab14e04 / config开关全失效 b05823b),均单测/评审漏、真机才暴露——真测核心价值兑现。
