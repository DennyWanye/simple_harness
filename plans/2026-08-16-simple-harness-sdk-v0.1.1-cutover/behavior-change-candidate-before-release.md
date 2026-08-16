# Pending behavior-change approval — candidate before final release

> 状态：APPROVED（2026-08-16）  
> Scope：只修改 `testcase/2026-08-13-simple-harness-sdk/manual-test.md` 的测试前置，不改变产品行为、
> acceptance AC或桌面expected results。  
> Expiry：v0.1.1 program完成或放弃时失效。

## 冲突

- Frozen acceptance要求：SDK测试→Simple Harness真实消费者→最终exact wheel复验并删除旧实现→
  发布exact tag/commit/wheel→Handoff。
- Frozen manual testcase当前要求桌面测试开始前已经从private immutable release取得tag/wheel。

两者形成循环：不先发布不能跑桌面oracle；未跑桌面oracle又不能按acceptance发布。

## Exact old

`从 private immutable release 取得同一份 candidate wheel、wheel SHA-256、tag、commit、release
manifest；不使用 SDK checkout、editable install、PYTHONPATH 或 path dependency。`

## Exact new（推荐）

`从 Slice A clean commit 的只读 candidate 目录取得同一份 wheel、wheel SHA-256、planned tag、commit、
BUILD_INFO 与 candidate manifest；不使用 SDK checkout、editable install、PYTHONPATH 或 path dependency。
桌面验收通过并取得发布批准后，创建 final immutable release，再从远端下载同一 bytes完成SDK-C7。`

批准后动作：记录用户批准原话SHA-256和behavior_change_id，修改exact一行并重新初始化尚未执行的
Slice C gate；不得修改其他oracle。若用户不批准，则备选是先单独批准发布`v0.1.1-rc.1` private
immutable prerelease并用其bytes测试，但final `v0.1.1`仍需产品验收后另行批准。

## Approval artifact

- `behavior_change_id`: `BC-SDK-CANDIDATE-BEFORE-RELEASE-001`
- 批准消息：`s好的， 请你处理`
- 批准消息 SHA-256：`76acb3020f13f19f990818c18c2479976b53916edeac18aa40bef1d434d41803`
- 批准解释：该消息紧接在“同时批准总计划与 candidate-before-release behavior change”的明确说明之后，
  因此仅批准本文件的 exact old/new 与开始执行；不授权数据库 reset、远端发布或 push。
- 执行纪律：总计划账本已冻结旧 oracle，故不在该账本存续期间原地改写。进入 Slice C 前先关闭/归档
  总计划阶段账，再按本批准 artifact 修改 exact 一行，并用含本 behavior change 的 Slice C manifest
  初始化全新 gate；其余 oracle 字节保持不变。
