# r7 交接（workbench-ui-20260807-r7）

状态：**15/18 PASS，S06 BLOCKED，S04/S08/S18 未跑** → `finalize --check-only` = NOT_READY
基线：`cad272e`（含会话行时间列修复）；最后提交 `4fe46ab`
本轮结束时已清理：app / backend / 8100 / vite 全部为 0，工作树干净。

---

## 一、必须先拍板的三处 oracle 冲突

三条都要改判据或 manifest。**改 manifest 必须在下一轮 `init` 之前定稿**——r6 就是
在 init 之后追加 behavior_change，被 fail-closed 判成全量作废重测。若三条都要改，
建议一次定稿后直接开 r8，不要分批。

### 冲突 1 — S06 判定项④cancel / ⑤rollback：前置构造不出
- 现象：「操作」tab 的 17 项全是 `succeeded` 的一方安装，三个动作入口均不可见。
- 根因（源码实证）：`backend/deskpet/capabilities/ui_projection.py:234-243`
  ```
  if cancellable and operation.status == "running":            → "cancel"
  if operation.status == "succeeded" and operation.pack_id:
      if rollback_available:  → "rollback"
      if uninstall_available: → "uninstall"
  ```
  当前数据下三个入口**结构上不可能出现**。
- 已尝试并失败的构造路径：经 SkillStore 市场真机安装 `algorithmic-art` 成功，
  但落到 `.testenv/cold-A/skills/`（**skills 子系统**），
  `capabilities/packs` 仍为 17（**capability pack 子系统**未变），
  「操作」tab 不新增任何操作 ⇒ 装技能造不出 running 的 capability operation。
- 已裁决：判定项⑥ uninstall 以 SkillStore「已安装」的卸载按钮为准，判 PASS。
- **待裁决**：④⑤ 是否有从 UI 触发 capability pack 安装/回滚的路径？若无，是否
  降级为"数据前置不可得"并记 behavior_change？

### 冲突 2 — S13 的 `expected_run_created` 与 TC 步骤5 直接矛盾
- manifest 标 `expected_run_created = false`，gate 因此要求 `negative_assertion`。
- 但 TC-WB-13 步骤5 明写要"发送一条消息**真实往返成功**"，真往返必然产生 root run，
  本轮实测确实产生 `82dd9ee294165d6c955b2777948df613`。
- 两者不可能同时成立。**未给证据打 negative_assertion 标记**（那等于谎称没创建 run），
  也未为迎合 flag 跳过步骤5。S13 因此保留一条 `RUN_CREATION_UNVERIFIED`。
- **待裁决**：flag 是否应改为 `true`。

### 冲突 3 — S04 步骤4 与 S12 判据对同一条命令期望相反
- 同一条 `cd backend && pytest tests/companion/ -q`：
  - **TC-WB-04 步骤4** 要求「**全绿（0 failed）**」
  - **TC-WB-12 / S12** 判据把「**9 failed, 643 passed**」当**基线等值**验收，
    且 S12 已据此在 r7 判 PASS
- 本轮两次独立执行均为 `9 failed, 643 passed, 10 skipped`，失败清单：
  - `test_candidate_draft_receipts.py::test_existing_v19_database_upgrades_to_material_snapshot_v20`
  - `test_performance.py::test_product_venue_transaction_trace_uses_one_full_durability_lane`
    的 7 个参数化（final / tool_batch_1_new_goal / tool_batch_3_new_goal /
    tool_batch_1_existing_goal / tool_batch_3_existing_goal / retry / fallback）
  - `test_skill_pack_adapter.py::test_shipped_skill_inventory_is_exact_and_hash_validated`
- **待裁决**：这 9 个是已知基线失败（则 S04 措辞改"与基线等值"），还是真回归
  （则 S12 判据要收紧）？**未做基线 checkout 对照实验**，故未下结论。

---

## 二、剩余三个场景的续跑清单

### S04 chat + companion（`expected_run_created=true`）
已完成：
- 步骤4 (a) 层 pytest 已跑（结果见冲突 3，判定取决于裁决）
- 步骤5 (b) 层的 **② grep 证据已成立**：现有 r7 日志中
  `requested_scope=companion_action` **多次命中**，`window_scope_denied` **零命中**
待跑：
- 步骤1-2：发「用 markdown 回复：一个二级标题、一个三项无序列表、一段行内代码。」
  核对 markdown 渲染（标题/列表/行内代码样式，非裸字面文本）
- 步骤3：mic 按钮禁用态 + tooltip 文案
- 步骤5 (b) ③：devtools 执行
  `await window.__TAURI__.core.invoke("get_window_control_credential")` 须返回非 error
- 步骤6：头部条（标题 / 模型按钮 / ContextRing / Harness 巡检开关）能力核对
- 记 `run_id_under_test`（步骤1 产生）

### S08 settings（7 步，最重）
- 步骤2 分区核对表①-⑧（⑧「桌宠形象」必须**不存在**）
- 步骤3-4：自启开关切换 + 保存；系统侧观察面 `ls ~/Library/LaunchAgents` 或
  系统设置·登录项，**实测哪个观察面有变化就原样入账**
- 步骤5：完全退出重启，开关状态持久化
- 步骤6：切回原值自清理
- 步骤7：升级夹具 —— `git worktree add <tmp> 644ab16` 检出改版前基线，
  以共享 `DESKPET_USER_DATA_DIR` 先跑基线构建造数（1 provider + 1 密钥 + 2 会话），
  再用改版构建以同一目录启动核对
- **已知非缺陷现象**：设置页 LLM Providers 显示空态，是因为 relay-cloud 只在
  运行时 ensure（`relay_provider_ensured id=relay-cloud`，registry `n=1 enabled=1`），
  **未落盘到 config.toml**（`[[providers]]` 计数为 0），而设置页读的是持久化列表。
  对话能通是走运行时 registry。Provider **分区本身存在**（含「+ 添加」按钮），
  按步骤2 的"分区存在"口径①应达标。

### S18 session delete edge（`expected_run_created=true`，destructive 放最后）
- 前置：≥3 个会话各含可区分消息（`.testenv/cold-A` 现有 ~29 个可直接用，
  但步骤4 要逐个删完，成本高；或在 cold-S01-r7 造 3 个，成本低）
- 步骤1 在会话 A **打开态**下删除 A → 不得停留在"已删会话消息流还在显示"的残留态
- 步骤2 删后立即发消息 → 断言**不写入已删 A 的 sid**
- 步骤3 重启 → A 不复活，步骤2 消息归属正确
- 步骤4 删到最后一个 → 回空态引导
- 步骤5 空态新建 + 发消息 → 可恢复、无持久污染

---

## 三、驱动方式：踩过的坑（务必先读，能省一轮）

1. **侧栏会随视图伸缩**（会话视图展开列表、其他视图收起），按上一张截图算坐标
   连点必然错位——本轮连错 3 次。用 `artifacts/ax.sh` + `nav.sh` 每次重查 AX：
   导航按钮固定 **宽 223**，会话行/图标宽度不同，据此过滤在两种布局下都稳定命中。
   **不能用 `head -N`**——会话视图里会话行也是 AXButton，会把导航项顶出前 N。
2. **AX 文本探针对全屏浮层不可靠**：ContextTrace / 记忆管理打开时 AX 仍能读到
   被遮住的聊天内容，据此会误判"点了没反应"。浮层一律**以截图为准**。
3. **几何测试前必须关 devtools**：停靠的 Web Inspector 把 webview inner_size 压到
   低于 MIN_H=560（实测 1120x260），`build_geometry` 直接 rejected，拖拽值**不落盘**，
   会被误判成 B8 回归。
4. **AX 会瞬时失灵**（报「window 1 … 无效的索引」或 `count of windows` 返回 0）。
   `drive.sh` 算不出坐标时会静默用空串——**必须先验证 geom 非空再点击**，
   否则点击落到 (0,0) 或错误位置。本轮末尾还出现过 `set frontmost` 静默失败导致
   **按键落到别的应用**——高频驱动时务必每步校验前台进程名。
5. **backend 进程核对 pattern**：用 `pgrep -f 'venv/bin/python main.py'`。
   `backend/main.py` 这个 pattern **永远匹配不到**（cwd=backend/，argv 只有 main.py），
   用它得到的 "backend: 0" 是空断言。
6. **批处理脚本前必须确认应用在运行**：本轮 S16 路径A 首轮就是空跑
   （上一场景 fixture 已退出应用，front 失败、坐标读空，`app=0` 是退出前本来就成立的废话）。
7. **判据脚本不要写快照常量**：S12 曾因硬编码 `grep -q "75 passed"` 在新增一个测试
   文件后误判 FAIL，而 **root FAIL 一旦入账不可撤销**（`compute_scenario_status` 里
   `any(result=="fail" for roots)` 永久粘性，无 waiver / 单场景 invalidate / run 级 supersede）
   ——r6 就是因此报废、改开 r7。判据要写"意图"（不得含 failed + 文件数 ≥ N），不写快照数。
8. **pytest 汇总行不能用 `tail -1`**：会抓到进度点行（`.sssss....`），
   要按内容抓最后一条含 passed/failed 的行。

---

## 四、本轮已入账的 PASS（供 r8 判断哪些要重跑）

脚本道：S02(script) S04(script) S09 S10(script) S11 S12
真机道：S01 S02 S03 S05 S07 S10 S13 S14 S15 S16 S17
BLOCKED：S06

若开 r8，这些全部作废重跑；脚本道一条 `artifacts/script-lane.sh` 即可，
真机道是主要成本（本轮 11 个真机场景约占绝大部分工时）。

---

## 五、2026-08-08 用户裁决（r8 开轮前定稿清单）

### 冲突 3 — **已用证据解决**（本轮补做了文档里说"未做"的对照实验）
`git worktree add --detach <tmp> 644ab16` 检出改版前基线，同一解释器跑同一条命令：
  基线 644ab16 → **9 failed, 640 passed, 10 skipped**
  当前 cad272e → **9 failed, 643 passed, 10 skipped**
失败用例 ID **逐条完全一致**；passed 640→643 是改版新增的 3 个测试全过。
⇒ 这 9 个是**与改版无关的既有基线失败**，不是回归。
⇒ 裁定：**S12 的「基线等值」判据成立**；**TC-WB-04 步骤4「全绿(0 failed)」是措辞
   错误**（该标准在基线上同样达不到），r8 前改为「与基线等值（9 failed / 643 passed）」。

### 冲突 2 — S13 `expected_run_created`
TC-WB-13 步骤5 要求真往返，必然产生 root run，实测产生 `82dd9ee294165d6c955b2777948df613`。
⇒ manifest 的 `false` 是写错，r8 前改为 **true**（不是给证据打 negative_assertion）。

### 冲突 1 — S06 ④cancel/⑤rollback：**用户裁决＝先补实现再测**
不降级判据、不造数据凑判据。先查清「为什么没有从 UI 触发 capability pack
安装/回滚的路径」（是漏做还是设计如此），**补上入口**，再真机测 ④⑤。

### 新发现（文档原先没有）— impact_paths 覆盖漏洞
`impact_paths` 只覆盖 `tauri-app/src/**`，**不覆盖 `tauri-app/src-tauri/**` 与
`backend/**`**。改这两处会因"未被任何 impact_paths 覆盖"触发 fail-closed **全量
18 复测**。r8 前补齐覆盖，否则以后每改一次后端就是全量。

### 品牌残留修复范围（用户选 A+B+C+LLM 提示词）
全仓 `DeskPet` 共 7763 处，其中绝大多数**不能动**（D 类）。要改的：
- **A 前端渲染文案 26 处**（`tauri-app/src/**.tsx`）：OnboardingWizard 5 处最刺眼
  （「欢迎使用 DeskPet 🐾」「一只住在你桌面上的 AI 桌宠」「开始和 DeskPet 玩吧」）、
  SettingsPanel 4 处、FeedbackPanel / CapabilityCenterPanel / ModelDownloadBanner /
  ExternalWaitDialog
- **B Rust 故障对话框 5 处**：`backend_launch.rs` 2、`gpu_check.rs` 3
  （另注：`gpu_check.rs` 写死"需要 NVIDIA GPU"，在 Mac 上本身是遗留问题）
- **C 后端 9 处 ppt `author` 默认值**（会写进用户 .pptx 文件属性）+ **23 处 LLM
  系统提示/工具描述**（`supervisor.py`、memory_tools、tool_search 等）。改完须重跑
  companion 套件确认无提示词依赖（基线 9 failed / 643 passed）。
- **D 绝对不动**：Python 包名 `backend/deskpet/`、`DESKPET_*` 环境变量、
  `deskpet-backend` 可执行名、`~/DeskPet/DeepResearch` 路径、`X-DeskPet-*` HTTP 头、
  relay 设备名 `DeskPet/<os>`、注释里的「桌宠」。动这些是重构，会砸掉 keychain 键、
  既有用户数据目录和 relay 契约。

### r8 执行顺序（用户裁决＝全套执行）
1. 补 capability pack 安装/回滚 UI 入口（冲突 1）
2. 品牌修复 A+B+C
3. manifest 定稿：TC-WB-04 措辞 / S13 flag / impact_paths 补覆盖
4. 全部提交 → **然后**才 `init r8`（顺序不可颠倒，r6 就是 init 后改 manifest 报废的）
5. 脚本道一条命令 + 真机道 11 场景全量重跑

### 冲突 1 的**再更正**（2026-08-08，推翻"补实现"前提）
补查源码后确认：**没有实现要补，UI 无安装按钮是刻意设计**。
- `backend/main.py:11477` 对 `capability_install` / `capability_activate` /
  `capability_repair` **显式抛** `CapabilityCenterError("model_driven_action_required",
  "Install, activation, and repair require the main agent to resolve a trusted
  source or failure receipt.")` —— UI 只保留 uninstall / rollback / retry / cancel。
- `capability_install` 是**已注册的 agent 工具**
  （`backend/deskpet/capabilities/tools.py:653`，"Install and atomically activate a
  capability pack."），参数 `source_type ∈ {builtin,local,configured,git}`（默认
  `local`）+ 必填 `uri`，`permission_category="skill_install"`，`dangerous=True`。
- 本地已有 17 个 pack 实体可作 local 源：
  `.testenv/cold-A/capabilities/packs/<pack_id>/<version>/<hash>/deskpet-pack.json`

⇒ ④cancel / ⑤rollback 的前置**可按设计路径正当构造**（不改数据、不放宽判据、
   不加代码）：在对话里请 agent 调 `capability_install` 装一个本地 pack →
   趁 `status=running` 点 cancel；再装一个新版本使 `rollback_available` 转真 →
   点 rollback。

⇒ **上一轮判"构造不出"是错的**：当时走的是 SkillStore 市场安装，那是 **skills
   子系统**（落 `.testenv/cold-A/skills/`），与 **capability packs 子系统**
   （`capabilities/packs/`）是两套东西，装了当然不进「操作」tab。

⇒ r8 无需为 S06 改任何产品代码，直接按上述路径真机构造前置即可。
