# Pending behavior-change approval — split local candidate from remote platform release gate

> 状态：APPROVED（2026-08-16）  
> `behavior_change_id`: `BC-SDK-A7-REMOTE-GATE-002`  
> Scope：只修正 Slice A 派生 oracle/manifest 的内部冲突；不改变 program acceptance、产品行为、
> 三平台最终必须通过的要求或远端发布需要单独批准的边界。  
> Expiry：v0.1.1 program 完成或放弃时失效。

## 冲突

Slice A acceptance 明确允许未获授权的远端三平台保持 PENDING，但冻结的 `SDK-A7` 同时把三平台
设为 Slice A required PASS，机器 gate 因此永远无法 finalize。当前 `SDK-C7` 只验证release identity，
尚未包含三平台 conformance；推荐把 Slice A 收口为本地 exact candidate，并同时把三平台 required
conformance 明确加入获发布/dispatch授权后的 `SDK-C7`。

## Exact old

`| SDK-A7 | immutable artifact/platform | 1 | 两次clean build canonical一致；同SHA在macOS ARM64/Windows x64/Linux ARM64 Python3.11 import+schema reopen+四suite通过 |`

## Exact new（推荐）

`| SDK-A7 | immutable local candidate | 1 | 两次clean build canonical一致；同SHA在clean macOS ARM64 Python3.11环境完成import、schema reopen与四suite；Windows x64/Linux ARM64同bytes复验移至获授权后的SDK-C7 required release gate |`

并把 Slice C exact old：

`| SDK-C7 | release identity | script | candidate/vendor/tag commit/BUILD_INFO/SHA/SBOM一致；远端下载bytes与vendor相同 |`

改为 exact new：

`| SDK-C7 | release identity + three-platform conformance | script + remote CI | candidate/vendor/tag commit/BUILD_INFO/SHA/SBOM一致；远端下载bytes与vendor相同；同一wheel SHA在macOS ARM64、Windows x64、Linux ARM64 Python3.11完成import、schema reopen与provider/tool/runtime/workflow四suite |`

批准后动作：关闭并保留当前 FAIL gate，修改 exact row、Slice A plan 与 manifest，初始化 successor
Slice A run；当前 run 只有在 successor 真正 SHIPPABLE 后才按 gate protocol retire。不得借此执行
push、workflow dispatch、tag、Release、数据库 reset 或产品 cutover。

## 已确认但不等同于本变更批准的决定

- 用户消息：`好的，用这个Simple Harness app做桌面端测试，不用另一个`
- 消息 SHA-256：`990513205ed6ac96a8a7ba6cec331d258a8bdfea5bc05612d66abfef014027bd`
- 仅确认：后续桌面端唯一验收对象为当前 `/Users/denny/projects/simple_harness` 对应的
  Simple Harness App，不使用其他桌面应用。
- 该消息没有明确批准把三平台 gate 从 Slice A 移至 SDK-C7，因此当时本文件仍为 PENDING。

## Explicit approval artifact

- 批准消息：`同意，继续`
- 批准消息 SHA-256：`2f5f51773fc29defabe6e3eef5938f1e07c4f2045b76dc42063de7d3e56eacf6`
- 上下文：紧接在“先本地 SDK → 当前 Simple Harness App 真实桌面测试 → 另行发布许可 →
  同一安装包 macOS/Windows/Linux 三平台验证”的完整顺序之后。
- 批准范围：采用本文件两处 exact new，把三平台 required conformance 从 Slice A 移至 SDK-C7。
- 未批准：push、远端 workflow dispatch、tag/Release、数据库 reset。
