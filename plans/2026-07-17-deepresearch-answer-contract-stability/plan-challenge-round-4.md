# Plan Challenge Round 4

> 结果：`VERDICT: FAIL`  
> 状态：2026-07-18 已逐项闭环，供下一轮只挑战新增/未闭环问题。

| Round 4 blocker | 闭环位置 |
|---|---|
| negative page outcome 无 canonical replay | `v6-contracts.md` §6、T4/T5：所有已派发 terminal outcome 都 commit exact result；outer journal 只分 committed/not_started |
| official search 无 durable result，URL hash 无法渲染引用 | §3/§6、T3～T5：`OfficialSearchResultV1/SearchCandidateV1/SourceLocatorV1`，完整 URL 仅在 owner-protected evidence blob |
| continuation 没有 exact snapshot/closure | §6.1/§7、T9/T12：`ResearchContinuationSnapshotV1`，manifest 单向引用 snapshot，closure 从 snapshot 验证 |
| artifact intent 无法接现有 ProductDelivery | §8、T9/T10：取消提前写文件；delivery-time deterministic `ArtifactProjectionV1` + existing ProductDelivery |
| uncertain effect 的 provisional result 无 durable locator | §4.1/T5：删除猜测复用；未 canonical commit 一律保守消费 reservation、清 staging、attempt+1 |
| wire `sha256:` 与 DB/native bare digest 冲突、manifest self-hash | §1/§7/T1/T10/T12：strict parse/format boundary；semantic hash 与 blob ref 分离；manifest identity seed 后 full-byte hash |
| terminal failed 会被 launcher 无限 reclaim | §8/T10：exact due/begin/claim-expiry CAS，attempt 5 terminal row 永不 due |
| v19 projection kind 不是可执行 enum | §9/T10：exact enum、legacy default、event/role/visibility/skip-embed mapping，unknown workflow event fail closed |
| terminal public/final-status payload 未冻结 | §7/T10：bounded exact `V6TerminalPublicV1` 与 final-status payload/size limits |
| effect head reconcile 会指向不存在的 next attempt | §4.1/T5：reconcile 保持 latest 指向真实 failed row，successor begin 后才原子推进 head |
| aggregate pending vs UI queued 漂移 | §8/T10：DB pending 唯一映射 public queued；其余 public status 冻结 |

额外修正：continuation head 同时保存 semantic `spec_hash` 与 `spec_blob_digest`，只有后者 FK 到 `workflow_blobs`，避免把业务 hash 错当物理 blob digest。
