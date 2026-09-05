# WeMM delayed model loading — bounded Host leaf

2026-09-06. Base134bc4b8, isolated feat/wemm-lazy-loading at
/Users/denny/projects/simple_harness-wemm-lazy. SDK artifacts/pins, main.py startup,
Host main/runtime, model selection and warmup hooks unchanged. No native/build/model.
Authority input: simple_harness-audit-access/plans/2026-09-05-agent-operation-audit/
human-access-leaf/NEXT-MEMORY.md and coordinator clarification.

## Production contract

Construction saves local model path/device and model identity, creates locks only.
SentenceTransformer import/construction occurs in the first actual embed load worker.
kind=wemm, dim=2048, provider=local, caller revision (production product-bundled),
normalization=l2 and wemm-embedding-2b:<revision>:2048 fingerprint remain unchanged.
Loading requires the model's reported dimension exactly2048 before publishing it;
encoded output length is also checked. Original normalize_embeddings=True and
encode_document fallback to encode retained. Original local_files_only=True and
trust_remote_code=True refer to the existing local resource authority, not downloads.

One shared load task is shielded from waiter cancellation. Loading state remains
loading while the actual thread is running, including after the initiating request
was cancelled. A later request shares that task and does not construct a second model.
Cancelled loading waiters never enqueue their encode. Failed loading records failed
with stable wemm_load_failed; no exception text, automatic retries, timers or warmup.
Only another explicit embed request can retry after the failed worker completes.

Owned encode tasks hold an asyncio queue lock until actual to_thread completion.
Caller shield plus a cancellation event lets cancelled queued requests leave without
submitting any thread. Physical threading.Lock additionally prevents overlapping model
load/encode work when an asyncio waiter departs. Cancellation of running computation
DOES NOT stop its thread or release model weights; late results are discarded for the
cancelled caller. Other live requests wait. No close/aclose/unload API is introduced.
This is a per-instance async-runtime wrapper, not cross-process or multi-loop sharing.

Cold/loading/ready/failed and model name/path are atomically read without loading.
p4 IPC uses that snapshot for WeMM, retaining legacy SDK/Mock fallback behavior.
EmbedderStatusCard distinguishes configured cold from loading and failed, shows actual
model name/path, and refresh sends only embedder_status. No separate loading command,
subscription framework, model chooser rewrite or default-disabled feature introduced.
Ready means the model is loaded, not proof every future encode will succeed.

## Explicit limits

Installed Memory0612 build_production still calls ensure_embeddings. The real public
empty-SQLite builder with a temporary local resource directory and fake model constructor
completed with0 model constructions/encodes and cold status. This fixture satisfies the
actual public resource existence contract; it is not a real provisioned model snapshot.
An old database with missing vectors or changed lineage can still invoke embedding at
startup; no reliable old-missing-vector fixture or SDK catchup change is claimed here.
No claim of all old-database startup zero weights, post-use unload, actual6GB saving,
real model dimensional quality, graph/native/Provider E2E or physical thread termination.
SDK deadlines are unchanged; first load can still exceed an existing caller deadline.

## Actual verification

Initial unmodified constructor was decisively red: guarded model import raised before
metadata read (1FAIL, exit1). New wrapper/installed-empty/IPC group12PASS0.29s.
Coordinator then identified executor thread starvation in an intermediate WIP: it
submitted to_thread before waiting on the physical mutex. Replaced with async owned
queue. Narrow affected group3PASS0.17s (queue counterexample, cancelled encode, output
length); no whole-group rerun. Final unique backend scope13 cases (8 new +5 legacy IPC),
not15: two affected cases overlap. Queue oracle wraps real asyncio.to_thread to count
submissions, still executes actual threads with fake SentenceTransformer; twelve queued
requests submit no extra thread while first is blocked, eleven cancelled requests never
encode, one live request follows. Physical peak encode concurrency1, model loads1.
Test fixture finally releases both actual thread gates; tests await surviving work.

React2PASS (28ms testcase/460ms Vitest duration), cold/loading/ready/failed, real name,
path, refresh-only request, absent/mock compatibility. tsc app project --noEmit with
incremental false exited0; no Vite/Tauri build and no shared tsbuildinfo modification.
node_modules is an ignored symlink to existing dependency installation, no install run.
Non-failing Vitest localstorage-file warning retained, not hidden.

All model classes in load/encode tests are in-memory fakes; constructor import guard
forbids sentence_transformers/torch/transformers. Actual public installed Memory version
asserted0.6.12; no SDK SQL, private ensure override or hash substitute. Native/live model
not exercised. All operations serial, process-tree limit1GiB/deadline120s; max observed
RSS608496KiB in tsc, below cap. PIDs16597/16667/16696/16720/16728/16739 absent after
completion; slot released. No tests restarted during docs/source freeze.

## Exact commands and local raw evidence

Backend env PYTHONPATH=backend, PYTHONDONTWRITEBYTECODE=1,
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1; only pytest_asyncio plugin enabled. Each monitor JSON
below records actual argv, exit, elapsed wall and sampled process-tree peak; no raw
log/DB/receipt is tracked in Git. Wrapper exit is distinct from recorded pytest exit.

### red

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_wemm_lazy.py::test_constructor_metadata_do_not_import_model -q -p no:cacheprovider -p pytest_asyncio.plugin
```

Exit1; monitor 0.843s; peak 116032KiB; stop=None.

`.local-test-evidence/2026-09-06/wemm-lazy/red.json` SHA256 `2577af2094653c7eff3939d2357957c8335938b89d398de18a3a32c39435d692`.

`.local-test-evidence/2026-09-06/wemm-lazy/red.log` SHA256 `709b31f2bb5128b28bfdc1e2c6a5d6cb1019aa36de8c1713a51fe9ce83e60d86`.

### green

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_wemm_lazy.py backend/tests/test_deskpet_p4_ipc.py::TestEmbedderStatus -q -p no:cacheprovider -p pytest_asyncio.plugin
```

Exit0; monitor 0.959s; peak 120912KiB; stop=None.

`.local-test-evidence/2026-09-06/wemm-lazy/green.json` SHA256 `b556732460642d2cca94884c9c90c822447795d8fdcdd7a8bc0cfd3c86725087`.

`.local-test-evidence/2026-09-06/wemm-lazy/green.log` SHA256 `47b7315acda5ca0143a1c738211adfb382a8ea5e9474bfc9e8365f44b3ffd58e`.

### queue

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_wemm_lazy.py::test_async_queue_does_not_occupy_executor_threads backend/tests/memory/test_wemm_lazy.py::test_cancel_encode_keeps_physical_mutex_and_skips_cancelled_queue backend/tests/memory/test_wemm_lazy.py::test_output_dimension_is_checked -q -p no:cacheprovider -p pytest_asyncio.plugin
```

Exit0; monitor 0.686s; peak 115600KiB; stop=None.

`.local-test-evidence/2026-09-06/wemm-lazy/queue.json` SHA256 `3f71217e41cea2fdc2c608544cb90c0d3b0b54cd28716647dc59d9bbf1825ff5`.

`.local-test-evidence/2026-09-06/wemm-lazy/queue.log` SHA256 `61ca266d920fafef052343603b50fecd4732b9b475dffd38709cbf8c33c80949`.

### react

```sh
/opt/homebrew/bin/node --max-old-space-size=768 tauri-app/node_modules/vitest/vitest.mjs run --root tauri-app src/components/EmbedderStatusCard.test.tsx --maxWorkers=1 --minWorkers=1 --no-file-parallelism
```

Exit0; monitor 0.921s; peak 270160KiB; stop=None.

`.local-test-evidence/2026-09-06/wemm-lazy/react.json` SHA256 `92ae3b14bf5f7346cfbaf599d3c467309d2d3d4c150c5e781da071ad3e3875cb`.

`.local-test-evidence/2026-09-06/wemm-lazy/react.log` SHA256 `7e753f03975845ac4863c085cce711050a330a5aad1009d52464f20bd40cd929`.

### types

```sh
/opt/homebrew/bin/node --max-old-space-size=768 tauri-app/node_modules/typescript/bin/tsc -p tauri-app/tsconfig.app.json --noEmit --incremental false
```

Exit0; monitor 2.308s; peak 608496KiB; stop=None.

`.local-test-evidence/2026-09-06/wemm-lazy/types.json` SHA256 `36602fa9fe0d4c29085881f7a179ddbad9d6befcd64e862a6cf4c8bd03276a4c`.

`.local-test-evidence/2026-09-06/wemm-lazy/types.log` SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

## Failed-load task retention follow-up

2026-09-06, after19b851f2. Main identified self._load_task retaining a completed
failure traceback and therefore its rejected local model. New load-specific callback
observes a completed task and drops self._load_task only if still that exact task.
Pending/newer tasks are untouched. It never clears exception tracebacks, changes
waiter errors, changes failed status, triggers retries or stops a physical thread.
Encode completion remains unchanged (no permanent self task field).

Decisive fake-model weakref oracle initially failed: after two actual concurrent
waiters got the dimension error, dropped their exception references and gc ran, the
rejected instance remained alive. After correction the same oracle passes: one
construction, both original errors, failed state and collected instance. A callback
identity guard oracle checks pending/current/stale tasks, with existing explicit
retry2 and cancelled-loader1 as necessary neighbors:5PASS0.20s, exit0. These add only
2 unique tests beyond earlier13 backend tests (15 unique total), not5 new cases.
No real model/weights loaded. No claim of allocator/GPU memory reclamation.

Dirac19b851f2 static scoped ACCEPT remains historical; this follow-up is sent for
independent read-only review. His nonblocking UI wording comment is also addressed:
ready now says '模型已加载，可处理语义嵌入请求。' rather than complete semantic-search
activation. Literal copy-only change; React/backend unrelated suites not rerun.

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_wemm_lazy.py::test_failed_dimension_load_releases_model_after_waiters_release_errors -q -p no:cacheprovider -p pytest_asyncio.plugin
```

Exit1; monitor0.722s; peak115856KiB.

`.local-test-evidence/2026-09-06/wemm-lazy/retention-red.log` SHA256 `6e7948ae8a82828b6f436694ffd53c06140a780f951922817715b7e515d9c9db`.

`.local-test-evidence/2026-09-06/wemm-lazy/retention-red.json` SHA256 `0f4e429872730bb5fbe52f8d6f36be269fa4cb52d6141da2e6ceab3393d06757`.

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_wemm_lazy.py::test_failed_dimension_load_releases_model_after_waiters_release_errors backend/tests/memory/test_wemm_lazy.py::test_load_completion_never_clears_pending_or_newer_task backend/tests/memory/test_wemm_lazy.py::test_failed_load_is_honest_and_retries_only_on_explicit_use backend/tests/memory/test_wemm_lazy.py::test_cancel_loading_does_not_duplicate_or_encode_cancelled_request -q -p no:cacheprovider -p pytest_asyncio.plugin
```

Exit0; monitor0.713s; peak115600KiB.

`.local-test-evidence/2026-09-06/wemm-lazy/retention-green.log` SHA256 `f40dc4889a5251e74fd5c12a6f6381b5832ccec301af19e7358daf1a4ed3a52b`.

`.local-test-evidence/2026-09-06/wemm-lazy/retention-green.json` SHA256 `b3cd55bb9e3f173e0646ca1e75422be46cafbdc87ccde298e597fc2e98f9fab0`.

Owned pytest16951/16966 exited; no background test/build/model/native. Slot released.
