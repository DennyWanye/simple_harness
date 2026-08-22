# FULL_SURFACE_SMOKE 与核心价值输入清单

## 价值优先 smoke（昂贵回归前）

以下 4 条先走真实 UI + Provider；1～4 均须得到非空、可人工判定的有效业务结果，否则提前 BLOCKED。

1. 直接事实：`记住，我家的狗叫 Max。`；重启后：`我家的狗叫什么？`
2. 口语事实：`欸，我家狗子叫 Max，之后提到它时别弄错哈。`；新 Turn：`狗子叫啥来着？`
3. 中英混合偏好：`FYI，我偏好 minimal style，之后做演示文稿按这个来。`；新 Turn：
   `给我一句话概括做 PPT 时该用的风格。`
4. 无“记住”关键词：`我通常把每天散步安排在晚饭后。`；重启后：`我一般什么时候去散步？`

这些是同一“个人事实/偏好”意图的自然表达变体，只验证路由鲁棒性，不冒充四个 distinct input class。
排名、排行榜等表达与本次 Memory 生命周期无直接关系，不列为 required。

## Distinct 输入类别

required UI matrix 共 5 个语义不等价类别：个人事实/偏好召回、工具型 committed turn、长上下文偏好
综合、恶意 Memory、故障降级恢复。SH-M5 的冷启动仍使用个人事实，不另计类别；SH-SURFACE 是入口回归，
不是语义类别。重启、重试、同义改写、continuation 只证明可靠性，不增加类别计数。

## Critical surface

- 应用冷/暖启动、登录/Provider 设置、主页。
- Session 创建、切换、重开、删除边界。
- 普通 chat send、stop、history reopen、Context 入口。
- 每个入口预期：非空、非 404/500、非“未接通”，且不串 Session。

## Affected surface

- 自动 recall；现有显式 remember/write、forget；Memory 查看入口若公开存在。SDK share API 走联合 conformance，
  本轮 simple_harness 不新增 share Tool/UI。
- ordinary/tool/attachment chat；PPT Tool + ArtifactCard 打开。
- permission、waiting、cancel、failed、recovery。
- Context empty/measured/failure/restart/multi-Session。
- Provider CRUD/reorder、Session model params、本地身份初始化与稳定binding。
- 自动化产品身份边界：同user-data重启稳定、Provider/API key变化稳定、跨user-data隔离、损坏identity在LLM前
  fail closed、payload/model spoof不能覆盖actor、`legacy_local_profile`不成为Agent Memory actor。

## Full surface extra

- 文件成功/失败、shell 成功/失败、web 成功/失败。
- Skills、Artifacts、Settings、现有关键导航与空态。
- installed-origin/exact-wheel 身份可见检查。

每项动作格式固定：Snapshot/Screenshot → 声明坐标/动作/期望 → 真点击/输入 → Screenshot →
关联脱敏日志与 receipt。无法执行的 required 项保持 NOT_RUN 并升级，不能用协议/脚本结果替代。
