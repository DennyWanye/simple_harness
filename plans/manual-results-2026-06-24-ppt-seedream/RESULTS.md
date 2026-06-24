# 真机 E2E 验收 — ppt_pro 惊艳生图路径 × doubao-seedream-4.0

> **日期**: 2026-06-24
> **目的**: 验证 relay 下线 gpt-image-2 后切换 doubao-seedream-4.0,`ppt_pro` 完整「惊艳 AI 整页生图」路径能否端到端真机跑通(这是 2026-06-22 那次被 gpt-image-2 403 卡住、**一直没验证过**的 happy path)。
> **结论**: ✅ **PASS** —— 主题 → 深度调研 → 大纲卡确认 → 8 张 seedream 图 → fromscratch 惊艳渲染 → pptx 落盘 → WPS 自动打开,**全程未降级到模板兜底**。

---

## 测试方式(符合项目真测纪律)

- 真机 windows-mcp:真截图 + 真坐标点击 + 中文剪贴板粘贴输入 + 后端日志判定。
- 跑的是 **master 源码**(`DESKPET_BACKEND_DIR=backend` + `DESKPET_PYTHON=venv`,日志确认 `[backend_launch] Dev python=… backend_dir=…backend`,非旧 frozen)。
- 不手动起 backend/vite(只给 Tauri 注 env,它自 spawn);backend=8100 / vite=5173。
- 主题:`帮我用AI配图生成一份惊艳的PPT，主题：在AI时代，程序员的核心竞争力是什么？`

## 证据链(后端日志 + UI 截图,均真机)

| 阶段 | 证据 | 结果 |
|---|---|---|
| 路由 | 日志 `p5s2_tool_call_args_dump name='ppt_pro' args='{...image_mode:true,theme:dark,pages:8...}' parse_ok=True` | ✅ LLM 正确路由到 ppt_pro + 惊艳模式 |
| F1 调研 | 日志真抓搜狗百科(CSDN/计算机词条 200)、arxiv 200;桌宠气泡「📚 调研完成(3 个来源),正在拟大纲」 | ✅ 真 deepresearch,非凭空编 |
| F2 拟纲 | LLM 返回 5504 字符大纲 JSON;大纲卡正文带引用 `[^1][^2][^3]` + 诚实声明「无直接证明→待核验假设」 | ✅ 调研支撑 + 诚实边界 |
| F3 确认卡 | 大纲卡渲染(📜历史/✅确认生成/✏️修改/✖取消);真 SendInput 点「确认生成」→ 日志 `ppt_outline_decision_resolved outline_id=d1266e40…` | ✅ FP-5 风格卡 + 确认环闭合 |
| F4 出图 | 日志 `_render_pro start image_mode=True pages=8` → `GET /v1/models 200`(probe) → **8× `POST /v1/images/generations 200 OK`** → `gate reachable=True n_ok=8` | ✅ seedream-4.0 8 张全成功 |
| 渲染 | 日志 `render path=fromscratch(惊艳)` → `render done(fromscratch) ok=True path=…deskpet-ppt-1782287938.pptx` | ✅ **惊艳路径,非模板兜底** |
| 产物 | 文件 2.88MB / **8 张 slide**;WPS Office **自动打开**(标题栏 `deskpet-ppt-1782287938.pptx`),封面=电影感电路板大图+标题压字,8 页全 AI 整页图 | ✅ |
| 回报 | 桌宠「✅ PPT 做好啦,已自动打开:C:\…\OutPut\PPT\deskpet-ppt-1782287938.pptx」 | ✅ WI-10 通知通道 |

**证据文件**:`deskpet-ppt-1782287938.pptx`(产物副本) · `screenshots/wps-deck-8slides-seedream.png` · `tauri-dev.log`(完整后端日志)。

## 时延(当前 relay 负载下)

- 确认(07:47:48)→ 渲染完成(08:01:11)≈ **13min**(含 8 张 seedream 出图 + WPS 渲染)。落在 8 页渲染超时 `max(600, 8×120)=960s` 内,但**余量不大**(若每张图都撞 ~190s 高峰,会逼近超时)。

## 过程中暴露的环境/产品问题

1. 🔴 **C: 盘曾 100% 满(0 字节)** —— 测试中途发现。导致 `state.db backup`/`billing`/`ppt_outline_store 建表`/memory `disk I/O error` 全报错。
   - **产品韧性正面发现**:ppt_pro orchestration **没崩** —— 这些持久化都是 best-effort,磁盘满时降级报警而非 fail,编排照常推进到出图。清盘(释放 ~23GB:Temp 21GB + pip/npm/.cache)后持久化自动恢复(`SessionDB ready vec=on`)。
   - **但仍是真实风险**:磁盘满会让最终 pptx/图片写盘失败(本次靠中途清盘救回)。
2. 🟡 **relay 不稳**:首次推理曾返回一次 `upstream_error: Upstream service temporarily unavailable`(空响应),重试后成功调起 ppt_pro。出图/渲染期间稳定。

## 生产就绪判断(更新)

之前的两大未验证项**现已闭合**:
- ✅ 惊艳路径端到端(seedream)——本次真机证实可用、产物质量在线(8 页全 AI 图、封面专业)。
- ✅ 多图在当前负载下的真实耗时——13min/8 图,在超时预算内(但建议给 `pro_render_timeout_s` 留更大余量,或对超大页数提示耗时)。

**结论**:核心「主题→惊艳 PPT」用户旅程现在**可以给用户用**了。建议补两条护栏后更稳:(a) 磁盘空间预检(出图前查 workspace 盘剩余,不足时提前提示而非写盘失败);(b) 大页数(>10)时把渲染超时调大或提示「图多耗时长」。
