# r8 交接（workbench-ui-20260808-r8）

状态：**9/18 PASS**，S07 步骤5 卡在一个待裁决缺陷 → `finalize --check-only` = NOT_READY
基线：`b68e958`（含品牌修复 44 处）；manifest 已按 BC-04/05/06 定稿后才 init
环境：`cold-A` 实例可能仍在跑；工作树干净

## 一、已通过（9）
脚本道 6：S02(script) S04(script) S09 S10(script) S11 S12
真机道 3：**S01 主窗形态** · **S15 空态** · **S02 单窗形态**

## 二、唯一待裁决：S07 步骤5 的产物卡片缺陷
S07 步骤 1-4 **已验证通过**（38 项与磁盘一致 / 排序与预置 mtime 逐条对照 /
「打开」→TextEdit 弹出预置文本 /「在文件夹中显示」→Finder 精确选中）。
步骤5 卡住，详见 `artifacts/r8-S07-partial.txt`：

- 按 TC 前置2 真跑产物任务：`excel_create` 成功，xlsx 真实落盘
  （`cold-A/OutPut/Excel/…xlsx`，5024 B）
- 但**消息流中无任何 ArtifactCard**
- 三个 last-mile 开关实读**全为 true**（artifact_envelope / frontend_artifact_card /
  tauri_artifact_ops）⇒ **排除"功能未开"**
- 形状矛盾：后端 `registry.py:2641` 信封是 `{ok, result, error}` + **顶层** artifacts
  （正好命中前端 `ArtifactCard.tsx:510` 第①分支）；但前端真机收到的是
  `{error, state, value:{artifacts}}` —— 是更内层的工具原始返回，信封那层没到前端
- 归类：**跨层契约漂移**（CLAUDE.md feedback_cross_layer_contract），非本改版回归

**三个选项（未自行选）**：
 (A) 本轮追到底并修 → 作废 tauri-app/src/** 或 backend/** 覆盖的场景
 (B) 记独立缺陷，S07 判 FAIL，r8 带 FAIL 收尾
 (C) 记独立缺陷，S07 暂不判定，先跑完其余场景再统一决定 ← **本 AI 倾向**
未追完的候选层：`harness/drivers/react.py` 结果投影 / `ui_projection` / 消息落库序列化

## 三、剩余 9 个真机场景
S03 S05 S06 S08 S10(真机半边) S13 S14 S16 S17 S18
- **S06**：cancel/rollback 前置**可按设计路径构造**——UI 无安装按钮是刻意设计
  （`main.py:11477` 抛 `model_driven_action_required`），在对话里让 agent 调
  `capability_install`（source_type=local，源用 `capabilities/packs/<id>/<ver>/<hash>/`）
- **S08**：LLM Providers 空态**已有解释**——relay-cloud 只在运行时 ensure
  （日志 `relay_provider_ensured`），未落盘到 config.toml `[[providers]]`，
  而设置页读持久化列表。按 TC 步骤2「分区存在」口径应达标
- **S18** 破坏性（删空会话），放最后
- **S13/S14/S16** 重启密集，建议连跑

## 四、驱动纪律（本轮新增/验证有效）
1. **每次跑前先 `artifacts/killall.sh`** —— `pkill "tauri dev"` **不杀 Python backend**，
   孤儿会占着 8100 且下一轮看起来"正常"，等前端真 spawn 才炸。S01 首轮因此作废重跑。
   backend pattern 必须是 `venv/bin/python main.py`。
2. **锁屏硬闸**（drive.sh/launch-*.sh 已内置，exit 3）——锁屏下 AX/截图/窗口计数
   全是无效观测，失败形态酷似"应用起不来"，本轮据此误判过一次。
   实现踩了两个 pipefail 坑（见脚本注释），最终用纯 bash `case` 匹配。
3. **几何硬闸**：`drive.sh geom` 返回 GEOM_FAIL 时拒绝点击（AX 会瞬时失灵）。
4. **坐标换算**：截图 displayed 宽 → 逻辑宽要乘 `窗口逻辑宽/displayed宽`；
   全屏截图则乘 `1470/displayed宽`。多次因直接用 displayed 值点空。
5. **浮层以截图为准**：AX 能读到被遮住的底层内容，据此会误判"点了没反应"。
6. **几何测试前必须关 devtools**：停靠的 Inspector 把 inner_size 压到低于 MIN。
7. **backend 归属核对**：`ps eww -o command= -p <pid> | grep DESKPET_USER_DATA_DIR`。

## 五、本轮其它产出（均已提交）
- `cad272e` 会话列表时间列缺陷修复（+ src/relativeTime.ts 共用 + 9 条单测）
- `b68e958` 品牌残留 44 处（A 前端 26 / B Rust 6 / C 后端 22），已真机验收
- `57d79f1` 三处 oracle 裁定（BC-04/05/06）+ impact_paths 覆盖漏洞修补
- 锁屏误判已更正落盘：`b68e958` **确认无回归**，解锁后一次通过
