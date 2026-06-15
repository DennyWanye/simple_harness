# DeepSearch Phase-2 手工测试用例

> **被测功能**: deep-research Phase-2 三项（均中国可直连）
> 1. 🥇 **multi-query / HyDE 查询扩展** — `research_tools._expand_queries`（纯 LLM 改写 + 假设答案，提召回）
> 2. 🥈 **巨潮资讯直连** — `research_sources.cninfo_search`（上市公司公告 PDF → pypdf 抽正文）
> 3. 🥉 **国家标准全文系统直连** — `research_sources.openstd_search`（国标号 + 名称元数据）
>
> **对应 commit**: `02fd984` feat(deep-research): Phase-2 中文一手源直连 + multi-query/HyDE 查询扩展
> **被测代码**:
> - `backend/deskpet/tools/research_sources.py`（新）— `direct_source_for` / `cninfo_search` / `openstd_search` / `parse_openstd`
> - `backend/deskpet/tools/research_tools.py` — §1.5 query expansion 接入、§4.4 直连源接入、`_query_expansion_enabled` / `_direct_sources_enabled` 开关
> - `backend/deskpet/tools/research_scoring.py` — `cninfo.com.cn` 等加入 TIER_1
> **配置开关**（`config.toml` `[research]`，均默认 **opt-out**，即默认开）:
> - `query_expansion`（默认 true）
> - `direct_sources`（默认 true）
> **最后更新**: 2026-06-15

---

## 0. 测试前置

| 项 | 要求 |
|---|---|
| 桌宠运行 | 已用新代码启动（commit `02fd984` 之后），onboarding 已登录、LLM 链路可用 |
| 网络 | 能直连 `www.cninfo.com.cn` 与 `openstd.samr.gov.cn`（中国大陆环境即可，无需代理） |
| 后端日志 | 能抓到 tauri dev 重定向日志（backend structlog 走 stderr → Stdio::inherit）|
| 报告落盘目录 | `OutPut/Research/`（research_run 自动落盘 .md，含 citations 附录）|
| 触发路由 | 用"**深度调研 / 出一份调研报告**"这类词触发 `research_run`；"查一下"只触发 `web_search`，**不**走本特性 |

> ⚠️ **windows-mcp 真测纪律**（见 `CLAUDE.md`）：每个 UI 用例必须真模拟点击/粘贴 + 截图 + 抓日志，
> **不能**用 pytest / `import research_sources` 查内部状态 / WebSocket 注入当 UI 证据。
> 每个动作前先 declare：`坐标=(x,y) | 动作=click/type | 期望=...`。

### 证据约定

- **报告文件证据**：调研完成后打开 `OutPut/Research/<最新>.md`，看末尾 **引用附录**的 URL 域名。
  - 含 `cninfo.com.cn` → 巨潮直连源进了引用池
  - 含 `openstd.samr.gov.cn` → 国标直连源进了引用池
- **后端日志证据**：grep `errors` 相关行 —— 直连失败会出现 `direct:cninfo:...` / `direct:openstd:...`；
  查询扩展失败会出现 `query_expansion: ...`。**无这些行 = 该步骤未报错**（不代表未触发，触发证据看搜索 query 数 / 报告引用）。
- **截图存档**：`plans/manual-results-2026-06-15-phase2/screenshots/<case-id>.png`

---

## TC-P2-01 — multi-query / HyDE 查询扩展触发（提召回）

**目的**：验证深度调研时，plan 拆出子问题后**额外**跑了 query 扩展产生的查询（改写 + HyDE），
使候选源数量 / 来源多样性高于"仅按子问题原文搜"。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 截图桌宠主界面 | 桌宠在线、聊天输入框可见 |
| 2 | declare 坐标后，点聊天输入框 → 粘贴：`深度调研一下"钠离子电池 2025 年商业化进展"，出一份报告` → Enter | 消息发出；桌宠开始"调研中"状态 |
| 3 | 等调研完成（可能 30s~数分钟） | 桌宠贴出报告，顶部有"调研覆盖 X 个来源 / Y 个独立域名" |
| 4 | 抓后端日志，grep 搜索请求条数 | 搜索请求条数 **> 子问题数**（plan 通常 3~6 子问题，扩展再加 ≤4 条 → 实际搜索串明显更多）|
| 5 | 打开 `OutPut/Research/<最新>.md` 看引用附录 | 来源 ≥ 5 个、独立域名 ≥ 3 个（扩展拉宽了召回面）|

**预期结果**：报告生成成功；搜索查询数 = 子问题数(+site 定向) + 扩展查询数（最多再 +4），
即扩展确实追加了查询。**判定 PASS** 条件：步骤 4 观察到查询数超过子问题数，且报告非空有引用。

**反例（FAIL）**：搜索查询数恰等于子问题数（扩展没触发）；或日志出现 `query_expansion: <err>` 且来源数异常少。

---

## TC-P2-02 — 巨潮资讯直连（上市公司财报，中国可直连）

**目的**：验证子问题命中"上市公司 / 财报 / 年报 / 营收"意图时，绕过搜索引擎**直连巨潮资讯**取公告
（PDF 抽正文），且该一手源因高权威（`cninfo.com.cn` ∈ TIER_1）进入引用。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 截图主界面 | 桌宠在线 |
| 2 | declare 坐标 → 点输入框 → 粘贴：`深度调研"宁德时代 2024 年年度报告的营收和净利润"，要带来源的报告` → Enter | 消息发出 |
| 3 | 等调研完成 | 桌宠贴出报告 |
| 4 | 打开 `OutPut/Research/<最新>.md` 引用附录 | **至少一条引用 URL 含 `cninfo.com.cn`**（巨潮公告直连源）|
| 5 | grep 后端日志 `direct:cninfo` | **无** `direct:cninfo:... : <err>` 行（即直连未报错）；若有则记录错误信息 |

**预期结果**：报告引用里出现巨潮公告链接（`static.cninfo.com.cn/...PDF` 或 `cninfo.com.cn`），
正文引用了来自该公告的财务数据。**判定 PASS** 条件：步骤 4 命中 cninfo 域名引用。

**反例（FAIL）**：引用里**完全没有** cninfo 域名（直连未触发或网络失败）；
或日志有 `direct:cninfo:... : <网络异常>`（此时记录为环境受限 + retry）。

> 📌 真测障碍 workaround：若步骤 4 没看到 cninfo，先 grep 日志确认是 **网络直连失败**（环境）还是
> **意图未命中**（`direct_source_for` 没返回 cninfo）。后者改用更明确的财报措辞重试（≥3 次不同措辞）。

---

## TC-P2-03 — 国家标准全文系统直连（国标，中国可直连）

**目的**：验证子问题命中"国标 / 国家标准 / GB/T / 标准号"意图时，直连 `openstd.samr.gov.cn`
取标准号 + 名称元数据，并因 `.gov.cn` 高权威进入引用。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 截图主界面 | 桌宠在线 |
| 2 | declare 坐标 → 点输入框 → 粘贴：`深度调研"钠离子电池有哪些国家标准 GB/T"，出报告` → Enter | 消息发出 |
| 3 | 等调研完成 | 桌宠贴出报告 |
| 4 | 打开 `OutPut/Research/<最新>.md` 引用附录 | **至少一条引用 URL 含 `openstd.samr.gov.cn`**（国标系统直连源），标题含 `GB/T ...` 标准号 |
| 5 | grep 后端日志 `direct:openstd` | **无** `direct:openstd:... : <err>` 行；有则记录 |

**预期结果**：报告引用里出现国标系统链接（`openstd.samr.gov.cn/bzgk/gb/newGbInfo?hcno=...`），
标准号格式如 `GB/T 32895-2016`。**判定 PASS** 条件：步骤 4 命中 openstd 域名引用。

**反例（FAIL）**：引用无 openstd 域名；或日志有 `direct:openstd:... : <异常>`。

---

## TC-P2-04 — 直连源意图路由不误触（普通主题）

**目的**：验证**非**企业/国标主题时，`direct_source_for` 返回 None，**不**触发任何直连源（避免无谓外连 + 污染引用）。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | declare 坐标 → 点输入框 → 粘贴：`深度调研"宋朝的茶文化"，出一份报告` → Enter | 消息发出 |
| 2 | 等调研完成 | 桌宠贴出报告 |
| 3 | 打开 `OutPut/Research/<最新>.md` 引用附录 | 引用 URL **既无** `cninfo.com.cn` **也无** `openstd.samr.gov.cn` |
| 4 | grep 后端日志 | 无 `direct:cninfo` / `direct:openstd` 任何行 |

**预期结果**：普通文化类主题不触发直连源，报告引用全部来自常规搜索结果。**判定 PASS** 条件：步骤 3 + 4 均无直连域名/日志。

---

## TC-P2-05 — 配置开关（opt-out 关闭直连源后行为）

**目的**：验证 `[research].direct_sources = false` 后，即便子问题命中财报意图也**不**直连巨潮（开关真生效）。
**本例为配置/日志核对**（非纯 UI），用于证明开关可控。

| 步骤 | 动作 | 期望结果 |
|---|---|---|
| 1 | 关桌宠（`taskkill /F /IM deskpet.exe` + Vite）| 进程清干净 |
| 2 | 在 `config.toml` 的 `[research]` 段设 `direct_sources = false`（无则新增）| 配置写入 |
| 3 | 重启桌宠 | 桌宠上线 |
| 4 | declare 坐标 → 粘贴 TC-P2-02 同一财报问题 → Enter | 消息发出，等完成 |
| 5 | 打开最新报告引用附录 | 引用 **无** `cninfo.com.cn` 域名（直连被关，只剩常规搜索源）|
| 6 | 测后把 `direct_sources` 改回 `true`（或删除该行恢复默认开）→ 重启 | 恢复默认行为 |

**预期结果**：关掉开关后财报主题不再出现 cninfo 直连引用 → 证明开关真控制该特性。
**判定 PASS** 条件：步骤 5 无 cninfo 引用，且步骤 6 恢复后（可选复跑 TC-P2-02）cninfo 引用回归。

---

## 结果汇总表

| 用例 | 被测点 | windows-mcp | 判定 |
|---|---|---|---|
| TC-P2-01 | multi-query/HyDE 扩展提召回 | 是（UI + 日志查询数） | ⬜ |
| TC-P2-02 | 巨潮资讯直连（财报） | 是（UI + 报告引用 cninfo） | ⬜ |
| TC-P2-03 | 国标系统直连（GB/T） | 是（UI + 报告引用 openstd） | ⬜ |
| TC-P2-04 | 直连意图路由不误触 | 是（UI + 报告引用无直连域名） | ⬜ |
| TC-P2-05 | direct_sources 开关 opt-out | 部分（配置改 + UI 复跑 + 引用核对） | ⬜ |

> 执行截图/日志证据存 `plans/manual-results-2026-06-15-phase2/`，本文件只放用例定义。
