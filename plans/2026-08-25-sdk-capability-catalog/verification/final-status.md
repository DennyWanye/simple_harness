# Verification status — SDK-first capability catalog

> Date: 2026-08-25
> Decision: **PASS_LOCAL_UNPUBLISHED — all functional, exact-wheel and real-UI gates passed; publication was not performed**

## Automated gates

- Harness SDK 0.6.2 candidate: `1464 passed, 2 skipped`; Ruff passed; focused strict mypy passed.
- Memory SDK 0.5.2 candidate: `218 passed, 7 skipped`; Ruff and strict mypy passed.
- Host affected suites: `301 passed`.
- Host baseline runner: `15 passed, 2 known-failure, 0 unexpected`.
  The two known failures are the frozen pre-existing root test fixture/async-plugin issue and frontend global ESLint debt.
- Frontend Vitest, TypeScript, production build, Rust tests/check, exact wheel pin/hash/origin checks and
  execution-build-manifest checks passed.
- Final post-commit-artifact recheck: Host changed-test suite `294 passed`; frontend model/UI `14 passed` plus TypeScript;
  SDK catalog/privacy/artifact `19 passed`; exact wheel SHA/origin, targeted Ruff, JSON parse and both repositories'
  `git diff --check` passed.

Candidate artifacts (not published):

- `simple_harness_sdk-0.6.2-py3-none-any.whl`
  - source commit: `67f5769ca5501f17e37193477d87a149203b6887`
  - wheel SHA-256: `ffb7c0619851f3c936fcc1d0cf527d07f49e87770291b85e57fe87032ac02c2e`
  - sdist SHA-256: `8fa5e2c9f168990a0649ed7e693160f1f6af8257e65053ab2e42eb782809a10b`
- `simple_harness_memory_sdk-0.5.2-py3-none-any.whl`
  - source commit: `46624b...`
  - SHA-256: `deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e`

Baseline runner receipt:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/verification-host/baseline-state.json`.

## Real UI gates

The current-source macOS bundle and the final Host install of the exact 0.6.2 wheel were exercised through the visible
SimpleHarness UI with isolated ports/userdata. They proved the visible sequence:

1. capability search;
2. capability describe;
3. capability activation;
4. the product authorization prompt for the selected filesystem MCP capability.

Those runs exposed and drove fixes for sequential authorization reprojection, immutable TaskGrant identity,
dynamic product-policy admission, dynamic physical-registry admission, normalized-vs-raw MCP schema identity and
MCP incarnation fencing. Focused integration coverage was added for the repaired seams.

Required acceptance is closed locally:

| Scenario | Status | Evidence boundary |
|---|---|---|
| CAP-1 | PASS | Current-source macOS UI, Session `f800f3fd-8a4d-42b5-b7e3-ada0b8fe3d49`, root `230fe1e642ec5cb8b20717741a073c6c`: search/describe/activate projected filesystem search (13→14 tools), real search succeeded, then describe/activate projected read-text (14→15), real README read succeeded, Run completed and the final answer correctly summarized the first paragraph. |
| CAP-2 | PASS | Current-source macOS UI, Session `7b03b54a-17da-4345-b8ef-95ee0200a008`, root `6b0e26708de8535695148e88023397d0`: search/describe/activate projected Playwright run-code, reviewed code navigated the exact port-15193 fixture and returned the real title `Simple Harness Capability Catalog Fixture`; Run completed without external navigation. |
| CAP-3 | PASS | Current-source macOS UI, Session `19d3bc29-78b0-4897-a1df-b7fa28be9476`, root `15d531d2ad9757fb8d26d0ca84905bd3`, SDK Run `product-sdk-d7c2b0888eeccabf3d88a441626461817f953b026446981b771785e1cd03489d`: `tool_search` found `translate-doc`, `skill_invoke` succeeded, the frozen body loaded exactly once, and the final UI preserved H1/H2/H3 plus all three list items and inline `tool_search`. |
| CAP-4 | PASS | Current-source macOS UI, Session `8b062b24-3a63-48ef-aa35-e69babe3821a`, root `39ace93c669e5999bb1700897e50b356`, SDK Run `product-sdk-62e2d665d3f77e6e860e695f87e2e45f8686c3f38e51a8a06a65781585359378`: Playwright navigate was discovered/activated, exact `https://example.com` invocation returned `ERR_BLOCKED_BY_CLIENT`, the page did not open, the Agent reported the safe failure, and no form submit or alternate-Tool bypass occurred. |
| CAP-5 | PASS | Two independent current-source roots straddled a complete app/backend restart. The restarted root began from 13 direct tools, rejected a stale activation nonce, re-described/re-activated, reached 16 tools and completed a real README read. A final packaged-app Run from the installed 0.6.2 wheel independently repeated 13→14 exposure and the real read successfully. |

Automated evidence did not replace the UI/real-provider gates; all required UI gates were executed. The candidate SDKs
and Host changes are not an SDK tag/release because no tag, release upload or download-back promotion was requested.
The exact candidate bytes are suitable for continued testing, but `publication_allowed` remains false until that separate
release workflow is explicitly authorized and completed.

## CAP-5 and installed-wheel closure evidence

The first qualifying source Run was CAP-1: Session `f800f3fd-8a4d-42b5-b7e3-ada0b8fe3d49`, root
`230fe1e642ec5cb8b20717741a073c6c`. After a complete app/backend restart, Session
`a40d6303-131e-4fb2-8ce3-4bbac041d119`, root `8a3a0f1ac9e65175bf4cddeb6d76a632`, SDK Run
`product-sdk-3cdfeab730f946a741978a5e6cffcc7e31352ca77a164af061c4231b3d7c0079` started again with 13
direct tools. A stale activation nonce was rejected rather than reused; the model re-searched, re-described and
re-activated capabilities, reached 16 tools and completed a real README read. Screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/cap5-pass-restart-readme.jpeg`, SHA-256
`859ddc7e3f32cf5fce241598afc9d71dbfed4b3bbdc1b86ae29a9209fec49a8b`.

The Host was then synchronized from its vendored pre-commit 0.6.2 wheel and the packaged macOS app was started without
`PYTHONPATH`. Startup logged `sdk_version=0.6.2`. Session `b3479aa9-201b-4fcf-8068-a5246b7fe20b`, root
`18efe97f5dee509db92b4c113688a346`, SDK Run
`product-sdk-cb792c9a03ea2e6d034f8e6ca814ebce140951bfc5b1f4dbac648565b6190fee` and request
`request-0d12017d-d1a0-4803-814e-be53adafc758` repeated search/describe/activate, proved Provider tool count
13→14, executed the exact `head=20` README read and rendered the correct first-paragraph summary. Screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/packaged-0.6.2-pass-readme.jpeg`, SHA-256
`9e632d5c2eca0e199f55ed799e8feff0ccd8c84fdc0544e38b21708aa7eee908`. The privacy-redacted runtime log SHA-256 is
`91d580d733a2602a4b9c43eeefabb40dc878fefc6536f931bc4d3951662b26b3`.

After source commit `67f5769ca5501f17e37193477d87a149203b6887`, the reproducibility builder produced the final
`ffb7c061…` wheel. A recursive wheel comparison confirmed the entire `simple_harness/` runtime package is byte-identical
to the fully exercised pre-commit wheel; only artifact metadata/documentation changed. Host lock/sync, installed-origin
and SHA verification passed for `ffb7c061…`, followed by another no-`PYTHONPATH` cold start. Startup again reported
`sdk_version=0.6.2`, MCP filesystem/Playwright ready and a connected, interactive UI. Cold-start screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/packaged-0.6.2-commit67f5769-cold-start.jpeg`, SHA-256
`551d468bc7cea657bcef10ea9b32a7647092dbd69fdea7878bb31bb93d8c9a4c`.

## CAP-3 closure evidence

The Host now routes `skill_invoke` through the SDK Run authority's complete `ToolExecutionContext`, including the exact
`capability_snapshot_ref`; it no longer reconstructs a partial context at the SDK-to-Host bridge. The SDK-first resolver
uses the frozen Skill resource locator and metadata, with legacy fallback only when no SDK Run authority exists. The
candidate SDK catalog also performs conservative ASCII morphology-prefix matching so `translation` can discover
`translate-doc` without broad fuzzy matching. Focused Host regression after the closure: `70 passed`; focused SDK
diagnostic/catalog regression: `11 passed`; privacy canary did not enter logs.

Final UI screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/cap3-pass-skill-translation.jpeg`, SHA-256
`1542b14956f1090ec096b4d753b6b42388b23df5e36ee01dcac39adb5ebb66ae`.
Successful request: `request-b59ef0a6-3919-41e3-9a70-a8e9da33e983`; frozen instruction hash:
`823d744cb2f48bc74342b1e2d477f65f93b2ee76917576f4c39bdbd8bf67453d`.

## CAP-4 closure evidence

The real-model Run searched, described, and activated only the Playwright navigate capability. The reviewed target call
was exactly `https://example.com`; the MCP call returned a browser-level `ERR_BLOCKED_BY_CLIENT` page, so no target page
or form became available and no submit action was attempted. The Agent stopped with an explicit safe-failure response
and did not use another Tool to bypass origin policy.

Final UI screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/cap4-pass-external-origin-blocked.jpeg`, SHA-256
`bf4d80087fbd62000c7d71b392dc539e37e2a771340f00d2280c0a05ef863ae1`.
Successful safe-failure request: `request-8b51287b-e3af-4ea6-a35d-5037e5b8594d`.

## CAP-2 closure evidence

The Host now freezes an optional `DESKPET_LOCAL_PAGE_URL` into the trusted project/task snapshot only after validating
HTTP(S), loopback host, explicit port, no credentials, and no query/fragment. The same exact origin is merged into the
Playwright MCP stdio config before both process launch and execution-build identity resolution. Shipped defaults use
the Playwright-supported loopback wildcard-port syntax and do not permit public origins. An invalid or external value
is rejected without logging the raw value.

Focused context/MCP/catalog regression after the closure: `131 passed`; `py_compile` and `git diff --check` passed.
The historical whole-file Ruff baseline in `backend/main.py` still reports existing undefined-`Any` debt and is not
reported as clean.

Final UI screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/cap2-pass-browser.jpeg`, SHA-256
`a17ed10443a80fbeabe329d898bebc61592a4d33da7d0b7907521081770f4375`.
Provider request refs: `e4743e31d45d32b1`, `6be252e51e5316c9`, `d39d777ddea81c91`,
`92e1b18a515102e3`, `14a1cd86b0de3d3c`.

## CAP-1 closure evidence

The Host composition now supplies the startup-global physical `ToolCapabilityScopeStore` to SDK Run authority and
freezes the physical registry policy fingerprint into RunStart. This keeps the strict stale check intact while making
the SDK and physical dispatcher evaluate the same frozen policy. Focused policy/scope regression: `72 passed`; test-file
Ruff and `git diff --check` passed. The production files still have pre-existing repository-wide Ruff debt and are not
reported as globally clean.

Final UI screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/cap1-pass-readme.jpeg`, SHA-256
`a63aa058cc689979fd1f3828ae10bd160dffa123aa197fe08f18ed8c0ff1b4c4`.
Provider request refs for the successful Run: `f29079990aa0e348`, `9cafb8549d1a2ea5`, `e5ee5fc02e8fd15d`,
`c0e609f4b24f6d44`, `597b4bff4b91015e`, `91a1ab0754b05bba`, `c6feaacf53102aba`, `9fd6f0509a67f93d`.

## Follow-up diagnostics added

The Host Provider seam now emits privacy-safe correlated records for attempt start, HTTP response shape, response parse
and terminal outcome. A single opaque `request_ref` joins these events. Stable stage codes distinguish transport timeout,
HTTP timeout/status, protocol shape, Host contract and adapter failures. Logs contain only bounded counts, digests,
status/elapsed metadata and model identity; tests assert that secrets, prompts, response text, Tool arguments and raw
upstream request IDs do not appear. Focused Provider/projection/SDK execute regression: `79 passed`; Ruff and diff-check
passed. These records were used during the real-UI closure to distinguish Provider success from later authorization and
physical-policy failures instead of treating degraded attempts as an undifferentiated relay failure.

The first instrumented CAP-1 rerun used Session `5e8e3739-3491-4197-953c-567bce87b03e`, canonical root
`ef9a6957240456c3bbaf95310037dba3` and Provider request refs `63c28b9dc53f9bf5`, `ae2e544eb598e1b0`,
`f2289bbeba4afa86`, `e1106edb987811e3`, `68f6202074c7b76e`. All five were HTTP 200/JSON/succeeded. The
fourth request proved same-Run exposure (`tool_count=14`, previously 13) and selected the real filesystem MCP Tool.
After user authorization the Tool failed with `capability_denied`; the fifth Provider request correctly consumed that
failure and completed. UI screenshot:
`.local-test-evidence/2026-08-25/sdk-capability-catalog/real-ui/cap1-workspace-capability-denied.jpeg`, SHA-256
`2f5de70ab06e79f14529c6bec756b96d9e587badb2937e6018ff54341858670b`.
