# 阶段 11 SDK 发版独立核验（opt.192）

- 日期：2026-10-18
- 核验员：独立子代理 Opus 5.5
- 范围：`git diff 48d15b3a9 HEAD -- sdk/simple-harness-sdk`（HEAD = db80fc7d8）。共 131 个文件：124 个 .py，7 个非 .py。
- 方式：全程只读。自写对比脚本逐文件比对。跑部署清单新鲜度检查。跑禁区比对（全仓与只限 SDK 两种）。没有跑全量测试。
- 工作区：除未跟踪的 `.archify/` 外与 HEAD 一致。

## 核对结果（逐条）

1. .py 改动只改注释与模块文档字符串：通过。
   - 方法：两端源码用 tokenize 取词法序列，去掉注释、空行、换行与缩进记号。字符串记号保留，所以函数与类的文档字符串也在比对内。
   - 123 个文件词法序列完全一致（只改了注释）。
   - 1 个文件只改模块文档字符串：`src/agent_orchestrator/runtime/legacy_provider_slots.py`。删去一句指向旧计划文件的话。去掉模块文档字符串后词法序列与语法树都一致。该模块的 `__doc__` 没有被任何代码读取。
   - 不通过的文件：0。
   - 补查：SDK 里按源码文本或文件字节算身份的两处（`workflow/definition.py` 的 getsource、`operation_profiles.py` 的连接器文件哈希）。对应源码文件都不在改动范围里。

2. 非 .py 改动（7 个）：
   - `src/agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json`：随源码重生成。`source_files` 键集合不变；哈希变化的 124 项与改动的 124 个源码文件一一对应，多一个少一个都没有。`deployment_id` 随之改变。`taskgraph_acceptance` 由 VALIDATED 回到 NOT_RUN，`acceptance_evidence` 变空。这是 `generate` 的固定输出；`scripts/release_sdk_opt.sh` 发版时会跑执行图验收门并 `validate`，重新写回 VALIDATED。对运行时：发版脚本跑完前 Host 不认这份清单，跑完后正常。不阻断。
   - `pyproject.toml`：只改 `[tool.mypy]`（加 `platform = "darwin"`，可选依赖清单加 `tiktoken`）。不进轮子元数据，对运行时无影响。
   - `README.md`、`ARCHITECTURE/index.md`、`ARCHITECTURE/PROJECT_STATUS.md`、`ARCHITECTURE/ORCHESTRATOR.md`：只改交接文件链接路径。新目标 `docs/archive/reports-2026-08/HANDOFF-2026-09-23.md` 存在，旧路径已不存在。对运行时无影响。
   - `tests/orchestrator/acceptance_assets/mutations.json`：SM-11、SM-21 两条的 `original` 跟着源码注释改了文字。逐条核对：195 条改坏用例的 `original` 在当前源码里全部找得到。对运行时无影响。

3. 部署清单是否最新：通过。
   - `uv run --frozen python scripts/build/taskgraph_manifest.py verify` 必须带 `--wheel`，本次没有轮子，没跑成。
   - 改跑 `backend/.venv/bin/python scripts/code_quality/manifest_fresh.py`：输出"部署清单：最新"，退出码 0。

4. 禁区比对：
   - 全仓跑 `guard.py --ref 48d15b3a9`：不通过。原因全部在 SDK 范围外：前端 `tauri-app/src` 的样式拆分和引用路径字面量变化（阶段 11 前端片），以及后端改动。和这次 SDK 发版无关。
   - 只限 SDK 跑同一套逻辑（导入 guard，文件清单只留 `sdk/simple-harness-sdk/`，不取前端字面量）：通过。禁区文件 114 个字节不变；严格字面量多重集不变；自由文本字面量增减均为 0。

5. 工具说明、已发布模板文字、并发默认值、编解码钉死文件：都没改。
   - 依据第 4 条 SDK 范围比对：工具说明与提示词属于自由文本字面量，增减 0；并发默认值属于严格字面量，不变；编解码钉死文件与包内资源文件都在禁区文件里，字节不变。
   - 依据第 1 条：函数与类的文档字符串词法不变。

## 必须处理

无

## 结论

PASS

备注（不阻断）：
- 全仓禁区比对不通过来自 SDK 范围外的前端与后端改动。Host 侧发版若用全仓比对作门，要另看那一份裁决与放行清单。
- 部署清单当前是 NOT_RUN。必须走 `scripts/release_sdk_opt.sh` 发版，让验收门把它写回 VALIDATED；不要手改。
