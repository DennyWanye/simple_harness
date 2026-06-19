# 手工 E2E 报告 — 预览图视觉选模板(高级色)

> 2026-06-20 · 真机 windows-mcp · DeskPet dev(`DESKPET_BACKEND_DIR` 指向本 worktree backend,venv python)

## 目标
验证迁库后「LLM 选大类 → 引擎看预览图视觉选具体模板 → design-pages 渲染」这条**真实运行栈**链路(裸进程冒烟因无 keychain key 无法触发 vision,故必须真机)。

## 环境就绪证据
- `[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=G:\projects\deskpet\backend` —— 跑的是本 worktree 改动(坑#8 满足),非 frozen exe。
- cloud LLM 连通:`GET https://chinzy.com/v1/models 200`(keychain key 生效,免重新登录)。

## case: E2E-PPT-高级色
- **动作**:桌宠输入框(屏幕坐标 (2817,1351))Clipboard 粘贴中文 → 点「发送」(3012,1351)
- **消息**:`用「高级色」模板风格，帮我做一份关于"新能源电池技术发布会"的PPT，4到5页就行`
- **期望**:LLM 调 `ppt_create(template="高级色")` → picker 真 vision 选一套 → 渲染落盘 → 桌宠回复成功

### backend log 证据(见 `backend-log-evidence.txt`)
```
tool_call ppt_create args: {"title":"新能源电池技术发布会",...,"template":"高级色",...}   ← LLM 选对大类
pick_template: 220 candidates > capacity 90, sampled 90 (dropped 130)                 ← 采样有日志(非静默)
HTTP POST https://chinzy.com/v1/chat/completions 200                                  ← 真 vision 调用
pick_template: vision chose id=77 → (177).pptx                                        ← 模型看预览图真选中
visual_review_loop(template) done rounds=2 final_issues=2                             ← 模板视觉闭环真回改 2 轮
```

### 产物 + 视觉证据
- 桌宠回复:**"做好啦，喵~ 已用「高级色」模板生成 5 页 PPT:"**(`screenshots/pet_reply_desktop.png`)
- 产物:`backend/userdata/OutPut/PPT/deskpet-ppt-1781887340.pptx`(5 页,8.6MB)
- 渲染对比:`screenshots/render_generated/slide{1..5}.png`(产物)vs `screenshots/render_template177/`(模板 177 原始 23 页)
  - 产物首页 = (177) 蓝灰线描设计 + 我的标题「新能源电池技术发布会」+ 生成副标题
  - 产物第3页「核心技术突破」= (177) 内容页设计 + 生成的电池技术要点

## 判定:**PASS** ✅
真实运行栈完整走通:LLM 选大类「高级色」→ picker 拼预览图 contact sheet → 真 vision 调用按主题选中 (177).pptx → design-pages 填充我的内容 → 模板视觉闭环 2 轮修版 → 桌宠确认 5 页产物。这是裸进程冒烟无法覆盖的 vision 选图真链路。

## 备注
- 视觉闭环报告 `final_issues=2`(竖排文字密集/右侧留白)——是所选模板个别设计页与内容的适配遗留,属已知「6 短板」范畴,非本次改动引入;模板本身可编辑可二次调整。
- dev 实例(新代码)仍在运行;如需停止:`taskkill /F /IM deskpet.exe` + 杀 5173 vite。
