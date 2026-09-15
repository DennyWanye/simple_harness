# A96 freeze (A-round)

Last update: 2026-09-15 11:15 CST.

## Decision

12 public AppWorld **dev** families were already predeclared. Instruction SHA-256 all match. Code/budget frozen to SDK `69d679c` / Qwen 256K / 4M tokens / 40 calls / 900s.

**96 episodes were not started.** Smoke on the named small control (`37a8675_1`, arm D) hit the 900s cap: TimeoutError, official_utility false, mission still ACTIVE, **1 unknown usage**, 26 calls / 621764 tokens, 0 knowledge rows. Failure retained. No rerun, no budget bump, no Flash.

Raw evidence: ignored `.local-test-evidence/2026-09-15/a96/` (`freeze.json`, `smoke-d-37a8675/result.json`).

## Why this is the A-round handling

A-round is not “96 green scores”. It is freeze → prove path → only then run the matrix. Smoke failed with unknown usage, which is a stop condition. Starting 96 now would mix an unproven live path into the formal identity.

N5 Qwen official pair remains the successful A-round model slice. N2 v4 1.2M failure remains. This smoke is a **new** failure on a different task/budget, not a replacement.
