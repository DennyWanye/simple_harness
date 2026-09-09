# journal · 删 workflow 线 Slice 1（2026-09-09 → 2026-09-10）

> 完成判定依据本 journal 与 phase-final 的 DoD 清单，**无机器 receipt**（`MACHINE_GATE` 未启用）。
> 被测 HEAD：Host 工作树相对 `03de5052` 的本次 diff（提交见文末终态行）。SDK 仓 `fd12e7dd` 未动。

## 1. 核心价值 smoke（最小验证动作，Task 5a，2026-09-10 00:11）

| 步骤 | 命令 / 动作 | 结果 |
|---|---|---|
| 守卫自检 | `backend/.venv/bin/python -c "…load_tool_manifest(); migrate_tool_schemas(m)…"` | 76 条工具、70 条迁移、`MANIFEST_SHA256 = df979c0e…`，无异常 |
| 冷启动 | `launch_native_candidate.py --launch --source $PWD --bundle …ec7b28c7p18120.app --installed-target …installed-h0710-m0638-s0313 --evidence-root .local-test-evidence/2026-09-09/remove-workflow-slice1 --port 18120 --model deepseek-v4-flash --env-file …deepseek.env` | `startup complete`；native.log 无 `RuntimeError` / `Application startup failed` / `Traceback`；证据 `primary-ui-0zda7ryu` |
| T1 | `TF_TURNS="1" twoflow_driver.sh … flow1` | `COMPLETED` 11.0 s，一次发送 |
| AC-2③ | `assert_provider_requests.py <E1>/userdata/data` | 2 次调用：`workflow_spawn` 0、catalog 提示词 0、tools 12 个 ⊆ 交付版 14 个 |

## 2. 兑现表

| AC | 矛盾地位 | 是否含 UI | 测试方式 | 驾驶者 | 真机证据 | 状态 |
|----|----------|----------|----------|--------|----------|------|
| AC-1 主流程原样跑通 | 决定性 | 是（原生 App，脚本驱动 AX） | 交付版同款 10 轮冒烟 + 同 userdata 重启 + 旧 userdata 段 | AI（`twoflow_driver.sh`） | flow1 T1/T2/T3/T5/T9/T11 = 11/17/68/16/22/26 s 全 COMPLETED、`clips.md` 落盘（`~/SimpleHarnessWorkSpace/task-abd9430e…/clips.md` = A-01/A-02/A-03）；重启 `primary-ui-6kafvuif` 就绪（AX「空闲 · 排队 0」）；flow2 T12/T14/T16/T22 = 17/31/127/41 s 全 COMPLETED；T12 答「成品一律放到「霜降素材 / 成片」」；T14 `continue_active` 绑定任务并复述决定（`A-序号` 在 provider 响应中出现 4 次，与交付版 4 次相同）；已知偏差：T16 未落 `inventory.md`（与交付版一致）、T22 因 T7 未跑而无字幕校对任务——本轮模型如实答「不存在该任务」（交付版为「已完成」，同一根因的另一形态，非本次删除引入，见 §5）；旧 userdata 段 `primary-ui-hcgwytes`：交付版数据用最终代码重启就绪，T12 20 s COMPLETED；进度文件 `primary-ui-0zda7ryu/twoflow-progress.jsonl`、`primary-ui-hcgwytes/twoflow-driver-old-userdata.log` | ✅ |
| AC-2 模型视野与代码无 workflow_spawn 定义 | 决定性 | 否 | 静态断言 + 运行时 provider 请求断言 | AI | ① 清单 76 / `workflows == {}` / 哈希同为 `df979c0e…`；② `len(SDK_DIRECT_TOOL_KERNEL)==19`、`len(PRODUCT_TOOL_NAMES)==83` 且等于清单 ∪ host-composed、`_CONTROL_TOOLS` / `core_names` 去名、`orchestration_controls` 无 `WORKFLOW_SPAWN`（Task 2/3 门命令输出「task2/3/4 gates ok」）；③ 全新 userdata 跑完 10 轮后终态 62 次、旧 userdata 45 次 provider 请求均 0 次（里程碑时刻 T1 后为 2 次）；④ `_inject_profile_catalog` 不存在（`tests/test_turn_preparer_static_helpers.py::test_profile_catalog_injection_is_gone`）；⑤ `route_prompt = ""`（源码） | ✅ |
| AC-3 冻结清单一致性 | 次要 | 否 | 脚本 | AI | §1 守卫自检；两个 JSON 往返字节相同（写回前断言）；冷启动到 startup complete | ✅ |
| AC-4 死壳与残留措辞 | 次要 | 否 | 脚本（两条 grep 等式） | AI | `-w`：非 vendor 11 个白名单 + 21 wheel；`-i`：非 vendor 12 个；`execution_profiles.py` 无两个死类；`companion/workflows.py` 已 `git rm`；三处注释已改 | ✅ |
| AC-5 边界之外零改动 | 次要 | 否 | `git status` | AI | 改动 18 个 backend 文件 + 新增 1 个测试，不含 `main.py` / `deskpet/workflows/` / `tauri-app/`；SDK 仓仍只有 ` M .gitignore`；旧 userdata 重启并继续一轮（见 AC-1） | ✅ |
| AC-6 非 ticket 委派与 ReAct 机制不回归 | 次要 | 否 | 脚本 + 单测 | AI | 五个委派工具仍在 kernel 与 PRODUCT_TOOL_NAMES；`test_deskpet_agent_loop.py` 排他拆批参数化 `capability_build`（accepted_async）/ `tool_activate`（sync）双分支通过；`workflow_progress` 相关文件不在 diff 内 | ✅ |
| AC-7 测试基线 | 次要 | 否 | pytest 单进程 | AI | 八文件门 **2 failed / 91 passed**（七文件 89 + 新增回归 2），失败集合 = baseline 两条既有红 | ✅ |
| AC-8 文档回写 | 次要 | 否 | 文件 | AI | `CHANGELOG.md` Unreleased 首条；`ARCHITECTURE/AGENT_HARNESS.md` 置顶条目；`ARCHITECTURE/AgentLoop.md` 边界句 + §7；`ARCHITECTURE/PROJECT_STATUS.md` 总表一行 | ✅ |

## 3. 分级冒烟

- 范围：LEAN/FULL 默认 critical + affected。affected = 工具清单加载路径 → 启动装配 → 主对话入口；critical = 主对话发送/召回/任务/重启。全部由 §1 与 AC-1 的 10 轮 + 重启覆盖；启动装配（`main.py`）与路由层本轮未改，不触发 full-surface。
- 脚本：`scripts/native/twoflow_driver.sh`（`TF_TURNS` 子集）、`scratchpad/assert_provider_requests.py`（已复制到 `plans/2026-09-09-remove-workflow-line/assert_provider_requests.py`）。

## 4. code review（phase-3 A4）

- 引擎：Opus 5 独立子代理（执行者未自审）。findings：**P0 ×1、P2 ×3**。
- P0 `F1`：删 `_inject_profile_catalog` 时误删紧随其后的 `@staticmethod`，`_has_active_skill_scope` 调用会 TypeError 并被 `except Exception` 吞掉。已修（补回装饰器）；决定性回归测试 `backend/tests/test_turn_preparer_static_helpers.py`（2 例）入回归套件。两份冒烟 native.log 均无 `pipeline_pre_loop_failed`；根因是当前 `main.py` 没有注册 `problem_pipeline` 服务（`config.toml` 的 `enabled = true` 空转，`context.py` 默认 None），缺陷调用点在该门之后，冒烟路径不可达。复验：八文件门全量重跑 2 failed / 91 passed；修复只改一行装饰器且该路径冒烟不触达，按 `REVALIDATION_SCOPE` 未重跑 10 轮；旧 userdata 段（启动 + 一轮 + provider 断言）在修复后的最终代码上执行，作为价值 smoke 一枪。
- P2 `F2` docstring 77→76 已改（`providers.py:670`）；`tools.py:1154` 历史注释保留。P2 `F3` `ProfileRegistry.model_spawnable` 与 `_profile_registry` 成死代码：记入遗留，Slice 2b 清理。P2 `F4` 投影不可变性断言降级为顶层：接受（投影已空）。

## 5. 遗留清单（不悬空）

- F-WF-1：三个拒绝集与 `deny_selectors` 仍含旧名字，是否放开由用户在编排大改时决定。
- Slice 2a/2b（`main.py` 旧启动器、`deskpet/workflows/` 图引擎、前端面板、`sdk_adapters/workflows.py`、`router.py`、DelegateRun ticket 字段、`model_spawnable` / `_profile_registry` 死代码、五条 orchestration 工具 `execution_build_identity` 漂移、文档整篇回写）与 Slice 3（SDK spawn 协议）：验收条款已登记在 acceptance 末节。
- T22 形态差异：本轮模型如实报告「无字幕校对任务」，交付版为「已完成」。两者同源于 `TF_TURNS` 子集未跑 T7；本次改动对模型可见工具集零 delta（AC-2③），判定为 provider 随机性，不作回归。
- 既有环境红两条（`test_execution_build_manifest`、`test_provider_runtime_refresh`）与本次无关，未动。
- 审计顺带发现（范围外）：`config.toml` 的 `[features.problem_pipeline] enabled = true` 在当前 `main.py` 下空转（服务从未注册），七步问题处理流水线的 PRE-LOOP 在生产里是死代码；建议单独立项。
- 幂等性审查：本次只有删除与常量改写，无「遍历 + 写副作用」代码；清单重签脚本为一次性且未入库。

## 6. 挑战与审计记录

- plan 挑战：primary 1 轮（12 findings，2 P0）→ synthesis → closure 2 轮 → specialist 1 个（residual-surface）→ CONVERGED；记账文件 `challenge-round-{1-primary,2-closure,3-closure}.json`、`challenge-round-1-synthesis.md`、`specialist-residual-surface.json`。required specialist 中三个以 closure 的真实命令输出替代，未取得用户 waiver 批准，如实记录。
- 完成度审计：`audit-full.json`（Opus 5，MODE full-audit）**PASS，8/8**；P2 findings：文档笔误三处（已改）、restart note 为空（记入 Slice 2 驱动改进）、`problem_pipeline` 配置空转（范围外，见遗留）。

VERDICT: PENDING（待完成度审计与提交后填写）
