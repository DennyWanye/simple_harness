# Prompt v5.1 — two actual classification calls

2026-09-07. Fixed source dc3bed40 (product342e2722), H079/M619/S0313 installed.

**Positive and negative both met this bounded classification expectation.** Exactly2 physical requests,0 automatic retries. gpt-5.5; actual wire max_tokens6144, no temperature; model_params={}, no reasoning override. Production ProductProviderAdapter and analysis_executor-equivalent messages/tool schema/request construction; production worker budget defaults (maxoutput6144/deadline180000), outer420s. Credentials from original simple_harness ignored .env only in process, no Keychain. No main/native/PID56392 changes.

Positive input was copied exactly from r24 USER AX: undecided workflow with record.txt/backup.txt and “松柏记录”, explicitly do not execute. Model returned uncertain Procedure plus Episode; exact full USER source/step compilation produced DRAFT, no rejected operations. Public Memory strict mutation committed that plan, public receipt readback bound its plan hash and actual revision1 operation IDs. No procedure execution occurred.

Negative input used the same two files/content as an explicit ordinary one-off task. Model returned only Episode, no Procedure; public strict mutation committed the single episode. This negative is not an independent assistant-source test. Original r24 response/FAIL unchanged; no retroactive reclassification.

## Evidence scope

This carrier constructs real Host S1 records in two isolated local databases, invokes the actual production provider adapter with exact analysis request shape, then uses the real compiler and public Memory mutation/readback. It does not drive foreground or the durable Host analysis job/attempt runner. Source Run identifiers are from the Host evidence builder, not proof of an actual foreground SDK execution. The public receipt does not expose a standalone lifecycle field: DRAFT is the compiled operation committed under the exact receipt plan hash. No native reminder/discovery/use or240 quality claim.

Both requests include only protocol instructions, current time/subject, the respective evidence item and empty semantic_candidates. The setup manifest/classification labels/expectations were not included. The two inputs and maximumcall count were written to ignored manifest before execution.

## Process and failure history

- model-r1: configuration path mistakenly pointed at candidate .env without APIKEY; KeyError before any HTTP request. PG69108exit1,0.687s,remaining[],cleanupnull. No credentials printed. This failure is retained.
- model-r2: corrected original main .env path; exactly2 physical requests; PG69158exit0,32.26s,peak151936KiB,remaining[],cleanupnull,minimumdisk5462MiB. Slot released immediately to main/Singer. No extra calls.
- Positive token usage:2759input/795output/3554total; negative2756/622/3378, actual reported usage, not inferred billing.

## Public references

- positive: source `d44c8604-d593-5fba-9bff-4426ae2a10d9` / `0537c7194a8f90d7fe8ec45d467b08ebf87d0d1d8ff4b44d57cfb7a95770602d`; analysis request `cf2c1f5ca2831dbe066d701d5e3e9bc2b314256cdadc93432d3825a3bf788c3b`; receipt `memory-mutation-receipt-6089d042f32dfb689560408e940466de92a6d80463ecab136cfaa6550a010dfc` / `11437b25b760f95b0acca6ab6fa667251424fb25e78291bece61a2910424783d`.
- negative: source `d3eef5e3-a058-5b3b-94db-99a8443b67db` / `7f5b498161291a2371adab98b3bf48f6b545d5e6204695cebae674738ee6735f`; analysis request `52d1b95b894c4927432932c86f95cb30bd8e40a0fd9b475b00bb1ebe82cf2822`; receipt `memory-mutation-receipt-e9a4f8b97dffc91b709381caa4ecb6d97285723f6cb1351dfd6e4c2ca2376b2a` / `212e70bf520033758f7a6a09091c2686df2cad7e48d6e0dc6f274ffb5283cef4`.

## Ignored local evidence

- `.local-test-evidence/2026-09-07/procedure-draft/model-plan/manifest.json` SHA256 `d48f43995dc7b54b6a9aaa719908cb4f85e0aa49f549a24617fd970b6c9fa57b`
- `.local-test-evidence/2026-09-07/procedure-draft/model-plan/run.py` SHA256 `85c5584b5020138b42d6ced9aebd2aa3aae2de1769db8fedfe1853dd69ddb987`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r1/command.log` SHA256 `07902c2780d5410135f83aa2e11bba1df7ab7a9e563cb0af6e75b59cbb68b6cd`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r1/resource.json` SHA256 `3d5df96fba295573bf2e54ed1936fb8c4226f017eb81aedef7964f3d9685f619`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/manifest.json` SHA256 `cc12ddd4731275215f260611e54d0f4f8fcdbdacbddb0640cb122b0b2b778e20`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/binding.json` SHA256 `5722614ac341c9e5290d06ef12ee125dfe8485b8cf2763cd7524e0766cebb69a`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/wire-requests.json` SHA256 `c4616856c863584d0042cc39e86eb1315ca565f6191d30ab0b09a59ec4b42a1c`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/positive/response.json` SHA256 `6b4bd7cdf6420c21543d1d1ebcccda1cc65aa191000c8089005d6e81747eae74`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/positive/proposal.json` SHA256 `aac58cf6c63474b81e1d2bb0ce4ed271421056744ab7406afe596c5a6879c168`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/positive/compiled.json` SHA256 `fd363c14dde8ce30eb5c1b99730bd0cb8029e378964adb562e3c25ab9ed02ecb`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/positive/public-receipt.json` SHA256 `69e6c192dd42e32250992e37a981a31b77dadcbb7ef4853999957869997cc712`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/negative/response.json` SHA256 `c939243054a2a5525274289bbafb772395c5e57de5e4fa69101b614265342213`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/negative/proposal.json` SHA256 `a24d74a62f652f69a8d777a8bf14b59a8079461a3014e4bd01bfda9bfbf5a158`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/negative/compiled.json` SHA256 `089ab26ea0f66c5307185ca82d464294180b989475996c9c0090c8a50a61be2f`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/negative/public-receipt.json` SHA256 `67b810aad3ee30219833d50238fa83322e8e2e894ad7bc5847cda9b26ad2d38e`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/results.json` SHA256 `4e17a44329bcc12bbccb9c8925e61b3a302b913e1c321a81646f3f56154352e5`
- `.local-test-evidence/2026-09-07/procedure-draft/model-r2/resource.json` SHA256 `fa150c96e0ceb79f5862d41ded6e823d51f2ea65d318aef8252456c835d86520`

Dirac read-only scoped ACCEPT for20f58862: original positive/negative responses and compiled/public strict-atomic receipt bindings verified. Limited to ProviderAdapter+compiler+public mutation, not durable analysis job/native/240. No rerun or further model call.
