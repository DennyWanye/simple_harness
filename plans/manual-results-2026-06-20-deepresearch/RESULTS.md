# DeepResearch 升级 — 真机 windows-mcp E2E 结果（2026-06-20）

> 真实运行的 DeskPet App（master backend，HARD GATE 通过：`[backend_launch] Dev python=...\.venv\Scripts\python.exe backend_dir=...\backend`）。
> 真模拟人：windows-mcp Click(SetCursorPos+SendInput) 聚焦输入框 → Clipboard 设中文 → Ctrl+V → Enter。
> 证据：`tauri-dev.log.err`(backend structlog 走 stderr) + `OutPut/Research/*.md` 落盘 + 截图。

## 环境门 PASS
- `[backend_launch] Dev python=G:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=G:\projects\deskpet\backend` → 跑的是本仓库改动代码（非 frozen exe）。
- App 主界面、已登录（"已连接"）、keychain 凭据可用、真 LLM 链路（relay chinzy.com）通。

## 用例结果

| 用例 | 判定 | 硬证据 |
|---|---|---|
| **TC-R1-1 更名生效（核心）** | ✅ **PASS** | log `event='p5s2_tool_call_args_dump' name='deepresearch' parse_ok=True`；全程 `deepresearch` 调用 **1 次**、`research_run` **0 次**。LLM 在真机按新名调用工具。 |
| **TC-R2-1 研究 E2E happy** | ✅ **PASS** | 落盘 `OutPut/Research/宁德时代-2024-年年报-...-1781896370.md`(8247B)；头部「调研覆盖 **5 个来源** 来自 **3 个独立域名**（1 轮检索，velocity=medium，引用自检 通过）」；正文 5 个内联引用 `[^1][^3][^8][^11][^12]` + 引用列表；桌宠回「已完成…并生成了 Markdown 报告文件」。 |
| **TC-R7-1 中文一手源直连回归** | ✅ **PASS** | log `GET http://static.cninfo.com.cn/finalpage/2026-03-10/1225002213.PDF 200`（巨潮年报 PDF 真抓）；`POST cninfo.com.cn/.../topSearch/query 200`；报告 `[^1]: 宁德时代 2025年年度报告摘要 (cninfo PDF)`。Phase 2 改动**未破坏** direct_sources。 |
| **TC-E2-1 路由：快查不误触发** | ✅ **PASS** | 发"MCP 是什么"后 `deepresearch` 累计仍 **=1**（未触发第二次）；近 3 轮 `tool_calls=0 stop_reason='end_turn'`（直接答，不走重型工具）。 |

## 顺带 live 确认（非独立用例）
- **多 query 扩展 + site 定向**：log 见多条中英文 Bing 查询变体 + `site:cninfo.com.cn` 定向搜。
- **可观测 coverage 组装无误**：报告头部 sources/domains/rounds/velocity/cite_check 均正常 → coverage dict（含 Phase 2 新字段 `**_observability_coverage()` 合入）live 组装成功无异常。`n_dropped_by_reason`/`elapsed_ms_per_stage` 是工具 JSON 返回值（不打 stderr），其键存在性由 Phase 2 单测 `test_research_run_coverage_observability_counts_drops` 确定性覆盖。
- **recency 修复**：财报主题走 direct-source（recency 硬编码 8.0），date 回填代码 live 无报错；新鲜度随真实日期变化由单测 `test_research_run_recency_scores_payload_dates` 覆盖。

## 观察（非本次 bug）
- 桌宠把本轮 Rust Tokio 请求**漂移**到了上下文里的旧 CATL/宁德任务（topic=宁德）。这是已知的「任务作用域漂移」问题（见 git `fix(context): 任务作用域隔离`），**与 deepresearch 更名/Phase2 无关**；且漂移到的财报主题反而顺带实测了 direct_sources 回归。

## 结论
本次升级的**实际代码改动**（Phase 1 更名 = THE 交付；Phase 2 可观测 coverage 组装 + direct-source 回归 + 路由）已在真实运行栈用模拟人点击/输入验证 PASS。其余用例（deep 档第二轮、强制 drop 计数、PPT 下游、空主题/网络降级）测的是既有特性或已被单测确定性覆盖，作为后续完整 live 跑量的补充。
