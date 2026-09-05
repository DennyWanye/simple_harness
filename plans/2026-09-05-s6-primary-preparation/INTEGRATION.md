# Primary integration — 2026-09-05

Isolated branch `feat/human-memory-primary-integration`, main base `c183fe70`.
Combined runtime `74425388`, API `fbc026a0` + `dc83c306`, frontend `829e21d7`.
No main checkout cutover, release, or program completion claim.

## Runtime / public API verification

Real installed Harness 0.7.2 + actual Host/SDK SQLite + deterministic Provider: new unscoped and genuine legacy scoped terminal receipts feed the same public page/detail API; close/reopen preserves message references and performs no additional Provider call. Changed raw SDK event ID/hash is rejected. No historical evidence is deleted to create legacy fixtures. Suppression policy is explicitly injected in this cross-component test; public Memory-backend behavior is covered separately by the API suite, not inferred from this fixture.

Command from this checkout:

```sh
PYTHONPATH="$PWD/backend" /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/memory/test_primary_runtime_api_integration.py backend/tests/memory/test_primary_read_api.py backend/tests/memory/test_primary_control_binding.py backend/tests/execution/test_primary_foreground_runtime.py -q -p no:cacheprovider
```

Result: **51 passed in 27.63s**. Raw log remains ignored at `.local-test-evidence/2026-09-05/primary-integration/runtime-api-first.log`. SHA-256: `bc763642d0bc3b03a0b0b977a0550ba81c69820095018c692aec9caa4a7ef378`.

## Native ordinary chat and restart observation

Actual native candidate: Host `87c42b43ab14363f6e5dc90df041327c930e583f`, including frontend retry correction `e84cade2`; `SimpleHarness Primary P18120.app`, binary SHA-256 `b7c556a527a40629ba721d38399533e6ff61beb285238c825f1e6c6cf658d307`. Frontend backend port was compiled as 18120; Tauri itself launches this checkout's Python backend. The dedicated runtime environment installs the unchanged exact integration-vendor SDK wheels (Harness 0.7.2, Memory 0.6.3, Service 0.3.12), borrowing other dependencies from the main environment. It is not an independently resolved dependency lock.

Through native coordinates/input, sent “请只回复：主对话链路正常。” and observed the real gpt-5.5 response “主对话链路正常。”, cleared draft, and idle queue. Host Run `72ae10bb-2921-5b1d-bb7d-0e5bf2e5fcce` completed; TaskScope count remained zero. Foreground Provider call took 2993 ms; the separate analysis call took 10333 ms and its batch/job reached applied before quitting. These are single-run timings, not a performance distribution or memory quality acceptance.

After normal native quit, relaunched the identical binary/source with the same user data. Native AX showed both previous messages. A subsequent read-only ledger comparison confirmed unchanged identities and counts: one foreground invocation, one analysis invocation, one applied job. No completed invocation was resent. After unlocking, the native follow-up “我上一条要求你回复的那句话是什么？只回复那句话。” returned “主对话链路正常。”. The actual second start Context contains system + prior USER + prior assistant + current USER; the physical Provider diagnostic agrees (4 messages). This one foreground call took 4868 ms; its analysis took 3244 ms and reached applied. Both foreground Runs remain unscoped. Ordinary reply, native reopen/no replay, and this history-use case passed for the original runtime candidate; full memory forgetting is still separate.

Two preceding carrier failures are retained: `primary-ui-j475lsei` failed SDK installed-origin validation; `primary-ui-0p2sx1j1` had an incorrect frontend compiled port and was closed without a test message. Correct SDK installation origins and a rebuild with port 18120 preceded the successful run. No unrelated port-8100 process was stopped.

Raw evidence stays under the main checkout's ignored `.local-test-evidence/2026-09-05/human-memory-resume/`:

| Relative evidence | SHA-256 |
| --- | --- |
| `primary-ui-u3ajfchy/04-first-response.png` | `8a07824af5bc94bc7d61f09ad598bd3727ecdeb3c203840f2113beb9a3b9f07a` |
| `primary-ui-u3ajfchy/04-first-response.ax.txt` | `44ae72e630217c82ced0b84620a929cf7dfb2c22416a148a1a413f70257fbe24` |
| `primary-ui-u3ajfchy/first-turn-ledger.json` | `da0cfd5a1668e33f0e036bc0addfa5fac69eb87dcfa1e1c8ace4436d63cf0bdf` |
| `primary-ui-u3ajfchy/before-restart-jobs.json` | `6de18bd77c96c4658ff98b30e43cc37fe8678d1bb9dd5a6afaceb1ca2a01132b` |
| `primary-ui-h_39qzgm/restart-no-replay.json` | `bbae8336d128b385a1d5b136dd509b364b03d5a7228cde1f92aa91e70b1e49a1` |

## Remaining boundaries

- Full history suppression still lacks Memory-owned reverse lineage and a batch visibility snapshot; a separate SDK candidate is being implemented. Existing source-level filtering is not a complete memory-forget proof.
- CREATE_NEW child-root and active-run binding now pass deterministic production-path tests, but the first native run exposed missing control tools; catalog and guidance now reach the real SDK authorization decision, whose primary UI presentation is still missing.
- Manual binding UI, attachments/slash/realtime, task/artifact/context inspection and original program quality gates remain incomplete.
- The old provider-unknown root and gate failures remain historical evidence; this run does not replace them.

## CREATE_NEW production catalog correction

Candidate `607acc7d` combines CREATE_NEW binding, original invocation generation fence, first-tool startup ordering, and tool-call UI identity. Combined runtime/API/control/create-new/delivery tests: **75 passed in 37.22s**, raw `.local-test-evidence/2026-09-05/primary-integration/create-new-combined.log`, SHA-256 `d762b77505d7e1032af4a477b023a9cc1ece62a07ade1a81eb2b14fef6980a4e`.

Actual native CREATE_NEW at this candidate **failed**: four foreground calls searched for capabilities, then the assistant asked for a project location. No TaskScope/file was created. Run `dbe46b69-9c7f-5b86-88c8-390a1652c95f`; SDK `product-sdk-39173e5e53ed189ff3fa023dc33b0a7e1759abae694b82f4474bae0497a64d85`. The actual frozen tool inventory lacked context_route, task_scope_search, and task_scope_update. Registration was present, but those three Host-added registrations inherited requires_project and were filtered from an unscoped primary Run. The fixture's explicitly safe registrations had concealed the production difference.

Correction explicitly marks only these three existing Context controls safe to expose before a workspace is bound. Their actual mutation/binding authorization stays in their handlers; file tools retain requires_project. The existing real production composition test now passes its actual published catalog/inventory through the primary filter and asserts all three direct controls, with write_file retaining its project requirement. No default permission classification was broadened.

Decisive red: all three controls missing (1 failed). After correction, production composition + context-route + actual CREATE_NEW tests: **34 passed in 12.31s** using this checkout's exact-wheel runtime environment. Raw `.local-test-evidence/2026-09-05/primary-catalog/red.log` SHA-256 `e0c4d0d473e22a2047a9635019a9f606eac0911d1ef8da0d801ee48421a6f723`; green SHA-256 `003efbf97ff9cd71f4e5e170a49c511af9c2e2d8e7a25f056a6f9ff351c57eef`. Earlier attempts using the main venv failed SDK installed-origin preflight and remain in origin-preflight-*.log; they are not the product red/green proof. Native corrected-candidate retest is pending.

Additional native evidence, relative to main checkout's ignored human-memory-resume root:

| Evidence | SHA-256 |
| --- | --- |
| primary-ui-h_39qzgm/05-restored.png | 7625f139fd5a6cbc7ebf6f170ba99fef16f78c59f51f29c415a010496de6c18a |
| primary-ui-h_39qzgm/07-history-response.png | c7c892927c63c92115034219a9aef2f57f090240af62e52fc9e91b09e6421756 |
| primary-ui-h_39qzgm/second-turn-start-context.json | fb31c013ae01bf96fece5939ef77d5c90e623159e3b5179b35463165ecb0ec7b |
| primary-ui-yteyuzxm/catalog-failure.json | 014d086aed1c24e40f1a2c4d7f001121a770bab215d355045f8960152775abff |
| primary-ui-yteyuzxm/03-project-result.png | 1555245a5fcc5b5ecc9965bfcf35c299378148762ecd6138f6c64f63ad319ff4 |

## Current-route guidance after catalog repair

Native c283e51c did expose all nine direct tools, including the three Context controls, but the model made no tool call and repeated the previous missing-location reply. New request Run `defcef60-9dc1-5ea5-aad0-0fd70b8fe2b3` completed without creating a Scope; this CREATE_NEW attempt remains **FAIL**, independently of the repaired catalog. Native `primary-ui-ccwhd61e/project-no-route.json` SHA-256 `f44c9fbdd11529b54d12ba4ed1c27f43454c8018f920e3c21bcb73fb3005d36b` retains the exact nine direct names and invocation result. An initial native paste/Return did not enqueue; the visible draft was then verified and sent by its actual button, resulting in this single new invocation.

The primary system instruction now explains the existing route: for a requested new project, call create_new with its title; the Host selects the workspace and the current tool result determines whether a user location/approval is required. Historical capability failures are historical observations, not current authorization. Binding/effect checks are unchanged. Real SDK/SQLite primary runtime regression: **16 passed in 11.37s**; actual model behavior must still be retested.


## Native authorization wait and stop

At exact Host `5da24d6f67331aeb190f5cb23defd2863674d670`, unchanged binary/wheels/userdata, the same natural project request did call `context_route(create_new)`. One real foreground Provider invocation succeeded in 16423 ms. SDK Run `product-sdk-812aa644b05ec98c7ca6fd52584895102c851f9ad8ec55dfab664cfa7f49d48a` entered `waiting` with a real `tool_authorization` decision. Host Run `6db4545d-f074-5797-bbad-09dd3c468430` remained RUNNING and Primary showed executing with no authorization card. No route-handler invocation, TaskScope or file effect was created. CREATE_NEW remains **FAIL: authorization presentation gap**; this is before the separate Manual binding challenge.

The native Stop button was clicked once. Host reached STOPPED, SDK reached cancelled, and the pending decision was cancelled; the interface returned to idle. No additional foreground invocation/effect occurred. Runtime closure reported clean with zero closure Provider calls; subsequent analysis made its separate 8390 ms call. This proves stop during an authorization wait only, not pause/resume, full decision interaction, or project completion. The interface still lacks an explicit stopped outcome in the historical group.

A stale computer-use app handle initially returned noWindowsAvailable and did not enqueue; reselecting the same running bundle by identifier restored input, without restarting the process. The visible draft was verified before the single Send. Raw evidence remains in the main checkout under `human-memory-resume/primary-ui-84iu5du1/`:

| Evidence | SHA-256 |
| --- | --- |
| 01-project-request.png | 7ff7262fd6eb8f1a00607ee0e30e780e9bd598b6dda39e33b74a5e41cf562c62 |
| 02-project-progress.png | 9b4b8c65e2ae05193d2d978c680dcb01602d894e9544851110b32567c12a0ea5 |
| 03-stopped.png | c109e39b12edf9074f15d7056e712c76d5943e4c5e4a398b8193f3e764a2451d |
| authorization-stop-ledger.json | 9470fb9f5373011fbba9cb68ca52b38dd76762705284208c06d7070247ca79c5 |

The 16-test context-instruction log above has SHA-256 `a28de7cdeeb4f88e9180ac63421e2af6a8d702e9c27d63cb94da572def80bb1c`. The actual stop evidence does not substitute for the remaining authorization UI fix or memory-history candidate integration.
