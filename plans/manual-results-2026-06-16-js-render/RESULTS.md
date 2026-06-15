# JS 渲染兜底 (cdp-edge) — windows-mcp 真机手测结果

> **执行**: 2026-06-16，windows-mcp 真模拟人工点击/粘贴。
> **被测**: deep-research JS 渲染兜底 (Option C / cdp-edge)，对应 commit `a520ef7`+`cd15844`。
> **跑的是 worktree 源码**: 启动日志确认 `[backend_launch] Dev python=...\backend`（非冻结 exe）。
> **结论**: **11/11 TC PASS，0 bug**。日志证据见同目录 `logs/`，报告见 `reports/`。

## 结果汇总（全部真机点击 + 后端日志锚点 + 报告核对）

| 用例 | 验证点 | 真机证据 | 判定 |
|---|---|---|---|
| TC-01 | happy path：JS 空壳被渲染救回 | `cdp_edge_render ok=True` **4 次**（cctv 217K/254K、news.cn 117K、douban 90K 字）；报告引用含全部 4 个被渲染 JS 站 | ✅ PASS |
| TC-02 | flag-off 不触发 | js_render=false → `cdp_edge_render` **0 行**，报告正常生成 | ✅ PASS |
| TC-03 | engine=webview 优雅降级 | webview(本期未实现) → `cdp_edge_render` **0 行** + 报告仍生成 + 无 300s 超时 | ✅ PASS |
| TC-04 | 双闸不误触发静态文章 | 静态茶文化主题：只渲染了真 JS 空壳站（byd.com「宋」车型 SPA、music.163），**未渲染静态长文站** → 源类型驱动正确 | ✅ PASS |
| TC-05 | 触发计数 ≤4 | TC-01 一轮**正好 4 次**渲染（命中上限不越界） | ✅ PASS |
| TC-06 | 单页超时降级 | timeout=2 → `cdp_edge_render ok=False error=TimeoutError`，报告仍生成、无挂死 | ✅ PASS |
| TC-07 | 渲染命中后去重不走 jina | jina 开 + 3 次渲染命中，`r.jina.ai` 调用 **0**（渲染 URL 没再走 jina） | ✅ PASS |
| TC-08 | 开关切换 | 开=4 次渲染 / 关=0 次（TC-01 vs TC-02），重启各自生效 | ✅ PASS |
| TC-09 | deep 档(300s)叠加不超时 | TC-01/03 deep 档完整出报告，`research_run' timed out` **0** | ✅ PASS |
| TC-10 | 中文 JS 站渲染 | TC-01 douban.com `ok=True chars=90232`，报告中文正文可读无乱码 | ✅ PASS |
| TC-11 | 渲染失败保活常驻浏览器（gap① 招牌修复） | timeout=2 渲染 `ok=False` 后，msedge 进程**全程稳定 10**（单浏览器存活未被杀，旧 bug 会归 0 重起） | ✅ PASS |
| TC-04.1 | 渲染正文更短不替换（观察性） | 逻辑由单测 `len(r_text)>len(text)` 覆盖；真机难定向制造，文档标观察项 | ✅（单测覆盖） |

## 测试中的发现
- **未发现功能 bug** —— 实现端到端按设计工作（双闸判别、触发上限、去重、降级、保活全部真机验证一致）。
- TC-04 揭示「双闸是源类型驱动而非主题驱动」：静态茶文化主题里的 BYD「宋」车型 SPA / 网易云音乐被正确识别为空壳并渲染，静态长文站未被误渲染——符合设计。

## 环境/前置
- dev 真测三件套(坑#8): `DESKPET_BACKEND_DIR`+`DESKPET_PYTHON`+`DESKPET_USER_DATA_DIR`，启动日志确认 Dev python。
- 配置在 `backend/userdata/config.toml [research]`，各 TC 改 `js_render`/`js_render_engine`/`js_render_timeout`/`jina_reader` + 重启。
