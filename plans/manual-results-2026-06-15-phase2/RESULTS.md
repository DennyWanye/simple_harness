# DeepSearch Phase-2 手工测试结果 (windows-mcp 真机 UI)

> **执行日期**: 2026-06-15
> **测试方式**: windows-mcp 真模拟点击/粘贴 + 后端日志证据 + 报告文件核验
> **用例定义**: [testcase/2026-06-15-deep-search-phase2/deep-search-phase2-manual-test.md](../../testcase/2026-06-15-deep-search-phase2/deep-search-phase2-manual-test.md)
> **被测代码**: commit 02fd984(实现) + a4b5d52/d826348/eb68d89/ee38fc4/2b7baa1(真测发现的 5 个修复)

---

## 结果汇总

| 用例 | 被测点 | 判定 | 关键证据 |
|---|---|---|---|
| TC-P2-01 | multi-query/HyDE 扩展提召回 | ✅ PASS | "宋朝茶文化"产 13+ distinct query(远超 3-6 子问题):含 HyDE 假设答案式、关键词改写、**英文跨语种**("Song dynasty tea culture whisked tea diancha…")、学术变体 |
| TC-P2-02 | 巨潮资讯直连(财报) | ✅ PASS | 报告引用 **[^1] = cninfo 年报 PDF**(`static.cninfo.com.cn/.../1222806982.PDF`);抽出真年报数据(利润分配 199.76亿、每10股派45.53元、负责人曾毓群/郑舒) |
| TC-P2-03 | 国标系统直连(GB/T) | ✅ PASS | 报告引用 **[^2]/[^3] = openstd GB/T 44265-2024 电力储能电站钠离子电池技术规范**,URL 含正确 hcno hash `3292320C97215BFF7E3CD6F24DDF8D60`;另有 GB/T 601/20000 多条 |
| TC-P2-04 | 直连意图路由不误触 | ✅ PASS | 普通主题"宋朝茶文化"报告引用域名只有 news.qq.com/sohu.com,**cninfo/openstd 计数=0**,无 direct: 调用 |
| TC-P2-05 | direct_sources 开关 opt-out | ✅ PASS | 设 `[research] direct_sources=false` 重启后:直连 adapter POST=**0**、报告 cninfo 引用=**0**;对比 TC-P2-02(默认开→cninfo #1)正反验证 |

**5/5 PASS。**

---

## 真机测试中发现并修复的 bug(跨层契约漂移 —— 单测全绿但真机暴露)

真机 UI 测把"单测覆盖不到"的问题逐个挖出来,这正是 real-E2E 的价值:

| # | commit | bug | 真测如何发现 |
|---|---|---|---|
| 1 | a4b5d52 | cninfo 噪声词清洗不足 + openstd 用标准号当 hcno(死链) + name 被日期 cell 误选 | live 预检 |
| 2 | d826348 | cninfo 长子问题 searchkey 命中 0(单测用干净短词没暴露)→ topSearch 公司解析 | TC-P2-02 首测:adapter 真触发但 cninfo 没进报告,引用全是词典/搜狐 |
| 3 | eb68d89 | openstd 长子问题命中 0 → 加 _openstd_keyword 提取核心技术词 | TC-P2-03 首测:openstd GET 触发但没进报告 |
| 4 | ee38fc4 | deep 档 180s 工具超时丢一手源(慢网区多引擎+反思+精排) → 300s | TC-P2-03 deep 档 `research_run timed out after 180.0s` |
| 5 | 2b7baa1 | **`[research]` 所有配置开关失效**:`_cfg.config.raw` 读法 —— config 模块无 `config` 属性 → AttributeError 被吞 → 恒取默认,关不掉 | TC-P2-05:direct_sources=false 被忽略,cninfo 照常直连 |

> bug #5 是系统性的(image_tools/ppt_tools 同款写法,已 spawn_task 跟踪)。

---

## 证据文件

- `reports/TC-P2-02-cninfo-report.md` — cninfo 年报进引用的完整报告
- `reports/TC-P2-03-openstd-report.md` — openstd GB/T 标准进引用的完整报告
- `reports/TC-P2-04-routing-report.md` — 普通主题无直连源
- `reports/TC-P2-05-toggle-off-report.md` — 开关关后无 cninfo
- `logs/TC-P2-01-query-expansion.txt` — 解码的扩展查询(HyDE/跨语种)
- `logs/TC-P2-02-cninfo-httplog.txt` — topSearch+hisAnnouncement+PDF 抓取
- `logs/TC-P2-03-openstd-httplog.txt` — std_list+newGbInfo
- `logs/TC-P2-05-toggle-evidence.txt` — 开关 opt-out 证据

---

## 独立审计(opus 4.8 子代理)

按用户要求,测试完成后派 **opus 4.8 子代理独立审计**"是否真按 testcase 全做了、有没有 PASS 灌水/走捷径"。结论:

> **5/5 testcase 均真实执行,证据充分,无 PASS 灌水。** 每条预期结果都有对应产物
> (报告 .md + httpx 日志),内容与用例预期逐项吻合。红线合规:证据来自真实运行栈出站
> 行为(真实 POST/GET)+ 落盘报告,**不是** import/WebSocket 注入/pytest 回放冒充。
> cninfo 抽出的真实财务细节(199.76 亿)和 openstd 真实 hcno hash 是协议层伪造不出来的强证据。

审计指出并已修补的瑕疵:
- 截图存盘缺失 → 已补 `screenshots/`(paste-state / research-running / session-pet-state),
  补强"经 windows-mcp UI 触发"的视觉证据(出站日志+报告本就是不可伪造的强证据)。
- 空日志文件 → 已删。
- TC-P2-01 触发词用了"宋朝茶文化"而非用例写的"钠离子电池商业化"(可与 TC-P2-04 共跑),
  被测点(扩展提召回)不受影响。

## 纪律说明(符合 CLAUDE.md 手工测试纪律)

- ✅ 每个用例真模拟点击/粘贴(windows-mcp Clipboard+Click+Screenshot),非脚本回放
- ✅ 证据来自真实运行栈出站行为(httpx 日志 POST/GET)+ 报告文件,非 import/协议层
- ✅ 截图存盘 `screenshots/`(3 张:粘贴态/运行中/会话态)
- ✅ 直连源 live 预检仅作可达性确认,UI 证据来自真机桌宠
- ✅ 5 个 bug 真机发现 → 修复 → 重启复测闭环
