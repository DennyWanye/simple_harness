# P3-S4 — PyInstaller spec for the frozen backend.
#
# Produces `dist/deskpet-backend/deskpet-backend` (`.exe` on Windows) + an `_internal/`
# sidecar directory with Python modules, native libraries, and the
# data files listed below. The Rust supervisor (post-P3-S3) picks
# this up via the `Bundled` branch of `backend_launch::resolve`.
#
# Usage (from `backend/`):
#   .\.venv\Scripts\python.exe -m PyInstaller deskpet-backend.spec --noconfirm --clean
#
# Or via the wrapper:
#   powershell ..\scripts\build_backend.ps1

# ruff: noqa — PyInstaller injects builtins like `Analysis`, `PYZ`, `EXE`,
# `COLLECT`, `block_cipher` into this file's scope.

import glob
import hashlib
import importlib.util as _ilu
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import PyInstaller.building.build_main as _pyi_build_main
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.building.utils import format_binaries_and_datas

# Playwright driver + the exact product-owned browser are collected through one
# shared helper.  It validates the package/browser pins and fails the build
# before Analysis if an ambient/global browser cache is passed accidentally.
_repo_root = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(_repo_root / "scripts"))
from playwright_bundle_spec_support import collect_playwright_bundle


def _capture_host_build_identity(repository):
    # These are the tracked Host source/config/resource inputs, not build
    # scratch, local SDK checkouts, credentials, or mutable process cwd.
    roots = ["backend", "tauri-app", "scripts", "capability-packs", "resources", "config.toml"]

    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=repository, check=True,
                                  capture_output=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("cannot establish Host build source identity") from exc

    commit = git("rev-parse", "HEAD").decode("ascii").strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise RuntimeError("invalid Host build source commit")
    if git("status", "--porcelain", "--untracked-files=all", "--", *roots).strip():
        raise RuntimeError("dirty Host build inputs: commit backend/tauri-app/scripts/resources changes before building")
    entries = git("ls-files", "--stage", "-z", "--", *roots).split(b"\0")
    digest = hashlib.sha256()
    count = 0
    for entry in sorted(item for item in entries if item):
        metadata, relative = entry.split(b"\t", 1)
        mode, _blob, stage = metadata.split()
        if stage != b"0" or mode not in {b"100644", b"100755", b"120000"}:
            raise RuntimeError("unsupported Host build input mode or conflict")
        path = Path(repository) / os.fsdecode(relative)
        content = hashlib.sha256()
        if mode == b"120000":
            content.update(os.fsencode(os.readlink(path)))
        else:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content.update(chunk)
        digest.update(mode + b"\0" + relative + b"\0" + content.digest())
        count += 1
    if not count:
        raise RuntimeError("Host build inputs are empty")
    if git("rev-parse", "HEAD").decode("ascii").strip() != commit or git(
        "status", "--porcelain", "--untracked-files=all", "--", *roots
    ).strip():
        raise RuntimeError("Host build inputs changed while capturing identity")
    return {"schema": "host-build-identity-v1", "host_commit": commit,
            "host_dirty": False, "tracked_inputs_sha256": digest.hexdigest(),
            "tracked_input_count": count, "input_roots": roots}


_host_build_identity = _capture_host_build_identity(_repo_root)
_host_identity_path = Path(workpath) / "host-build-identity.json"
_host_identity_path.parent.mkdir(parents=True, exist_ok=True)
_host_identity_path.write_text(json.dumps(_host_build_identity, sort_keys=True, indent=2) + "\n", encoding="utf-8")

# Resolve both SDK artifacts through the product SSOT. Verify the immutable
# wheel/manifest bytes and installed wheel origins before collecting anything;
# a source checkout or a stale installed candidate must not become a release.
sys.path.insert(0, str(_repo_root / "backend"))
from deskpet.sdk_adapters.runtime_paths import verify_sdk_candidate
from deskpet.sdk_adapters.sdk_candidate import (
    build_candidate_identity,
    sdk_candidate_manifest_path,
    sdk_service_candidate_manifest_path,
    sdk_service_wheel_path,
    sdk_wheel_path,
    verify_service_candidate,
)

verify_sdk_candidate(build_candidate_identity())
verify_service_candidate()

# Durable core tools must be built from the exact checked manifest.  Release
# wrappers regenerate with --write; PyInstaller itself is deliberately
# read-only and fails before Analysis when sources changed.
_execution_manifest_check = subprocess.run(
    [
        sys.executable,
        str(_repo_root / "scripts" / "generate_execution_build_manifest.py"),
        "--check",
        "--repo-root",
        str(_repo_root),
    ],
    check=False,
)
if _execution_manifest_check.returncode != 0:
    raise RuntimeError("execution build manifest check failed")


_orig_find_binary_dependencies = _pyi_build_main.find_binary_dependencies


def _find_binary_dependencies_skip_flagembedding(binaries, import_packages, symlink_suppression_patterns):
    # FlagEmbedding is pure Python at the package level; importing it in
    # PyInstaller's isolated DLL scan can crash on Windows in this venv.
    import_packages = [
        p for p in import_packages if not (p == "FlagEmbedding" or p.startswith("FlagEmbedding."))
    ]
    return _orig_find_binary_dependencies(
        binaries,
        import_packages,
        symlink_suppression_patterns,
    )


_pyi_build_main.find_binary_dependencies = _find_binary_dependencies_skip_flagembedding

# --- 0. mypyc runtime shims ---------------------------------------------
# `tomli` (and a few other deps) are compiled with mypyc. Mypyc emits a
# companion top-level `<hash>__mypyc.cp311-win_amd64.pyd` module alongside
# the package; both must be importable or `import tomli` raises
# `ModuleNotFoundError: No module named '<hash>__mypyc'` at startup.
# Auto-discover them so the hash is never hardcoded (it changes when the
# upstream wheel is rebuilt).
_site_packages = sysconfig.get_paths()["purelib"]
_mypyc_modules = [
    os.path.basename(p).split(".", 1)[0]
    for pattern in ("*__mypyc.*.pyd", "*__mypyc.*.so")
    for p in glob.glob(os.path.join(_site_packages, pattern))
]

# --- 1. Hidden imports --------------------------------------------------
def _collect_sdk_production_modules():
    # Public SDK __getattr__ exports can traverse another lazy mapping before
    # importing the leaf module. Static imports (or just agent_orchestrator)
    # miss those leaves, e.g. runtime.workspace_binding_protocol at startup.
    # Both distributions have already passed candidate verification above.
    def production_module(name):
        parts = name.split(".")
        return len(parts) == 1 or parts[1] not in {"testing", "cli", "__main__"}

    modules = []
    for package in ("simple_harness", "simple_harness_service"):
        modules.extend(collect_submodules(package, filter=production_module, on_error="raise"))
    return sorted(set(modules))


# Providers that dlopen / importlib their implementations at runtime
# won't be discovered by the default import graph. List every top-level
# package that the frozen exe must be able to `import` lazily.
hiddenimports: list[str] = []
hiddenimports += _collect_sdk_production_modules()
# Domain adapters and runtime providers are selected dynamically by the SDK.
# Collect from the verified installed wheel, never a sibling SDK checkout.
hiddenimports += collect_submodules("agent_orchestrator", on_error="raise")
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("ctranslate2")
hiddenimports += collect_submodules("silero_vad")
# 2026-06-28: keyring backends are loaded through entry-points, so
# PyInstaller's static import graph may miss them in frozen builds. Pin
# keyring + Windows credential backends and their win32ctypes shims when
# available; keep spec loading tolerant on envs without those optional deps.
try:
    hiddenimports += collect_submodules("keyring")
except Exception:
    print("[spec] WARN: keyring not importable, skip")

for _m in [
    "keyring.backends.Windows",
    "keyring.backends.null",
    "keyring.backends.fail",
    "win32ctypes.core",
    "win32ctypes.pywin32",
    "win32ctypes.core.cffi",
    "win32ctypes.core.ctypes",
]:
    try:
        if _ilu.find_spec(_m) is not None:
            hiddenimports.append(_m)
    except (ImportError, ValueError):
        pass
# 2026-05-30 P0 bug fix #10: deskpet/tools/__init__.py uses pkgutil
# .iter_modules to dynamically discover + import every tool module
# (excel_tools, doc_tools, ppt_tools, image_tools, etc). PyInstaller's
# static analysis can't see these — without explicit hidden_imports the
# office tools (excel_create / doc_create / ppt_create) NEVER reach the
# registry → LLM tool list lacks them → B1/B2/B5 (and many others) fail.
# Discovered via CDP-driven prompt asking LLM to enumerate tools.
hiddenimports += collect_submodules("deskpet.tools")
hiddenimports += collect_submodules("deskpet.skills")
for _pkg in ["scrapling", "curl_cffi", "browserforge", "w3lib", "protego"]:
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        print(f"[spec] WARN: {_pkg} not importable, skip")
hiddenimports += collect_submodules("agent_reach")
# NOTE(2026-06-28, RESOLVED): the frozen embedder subprocess worker used to die
# with "No module named 'datasets'" → silently fall back to MOCK embedder in
# every shipped build. ROOT CAUSE (located on the real exe, see
# plans/2026-06-28-frozen-embedder-datasets-fix/): FlagEmbedding's *inference*
# import chain hard-`import datasets` (a training-only dep) in
# abc/finetune/embedder/AbsDataset.py, plus two more frozen-only transformers
# quirks (inspect.getsource on docstring decorators; dynamic import of
# transformers.models.* during tokenizer autodetection). The fix is NOT a spec
# change — it was the (now removed) frozen embedder worker's compat patch, which
# injected a tiny `datasets` stub + patched the two transformers code paths
# right before `import FlagEmbedding`. Keep datasets EXCLUDED below (bundling it
# drags ~150MB of pyarrow/pandas and historically crashed build-time analysis).
# Do NOT add collect_submodules("datasets") — it does nothing useful here.
hiddenimports += ["sqlite_vec"]                    # P4-S20: L3 vector recall
hiddenimports += [
    "deskpet.playwright_bundle",  # product browser resolver / diagnostics
    "tzdata",                   # zoneinfo needs this on Windows
    # config.py additive feature-flag backfill writes via tomlkit. It's a
    # static import inside _merge_missing_feature_flags (lazy, try-guarded),
    # so static analysis may miss it — pin it explicitly to be safe.
    "tomlkit",
    "prometheus_client",
    "aiosqlite",
    # P3-S6+S7: user data / cache / models dir resolution at startup.
    "platformdirs",
    # uvicorn auto-loaders
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    # edge-tts uses aiohttp via optional import chain
    "edge_tts",
    # deep-research CDP-Edge JS 渲染兜底(research_cdp_edge.py)直接 import websockets
    # 连本地无头 Edge → 显式入包,使冻结正式包也能用 js_render=cdp-edge(Windows)。
    "websockets",
    # Evaluation suites are imported by package name through importlib.resources.
    "deskpet.companion.eval_suites",
]
hiddenimports += _mypyc_modules

# --- 2. Data files ------------------------------------------------------
# (source, dest-inside-bundle) tuples. Use collect_data_files() for
# installed packages; hardcode relative paths for our own repo files.
datas: list[tuple[str, str]] = []
datas.append((str(_host_identity_path), "."))
datas += collect_data_files("agent_orchestrator")
datas += copy_metadata("simple-harness-sdk")
datas += [
    (str(sdk_wheel_path()), "vendor"),
    (str(sdk_candidate_manifest_path()), "vendor"),
]
_playwright_datas, _playwright_hiddenimports = collect_playwright_bundle(_repo_root)
# The mac browser is an upstream signed, hash-pinned standalone tree. Adding
# its Mach-O files before Analysis would reclassify/rewrite/resign them and
# invalidate the runtime pin. Append that tree as DATA only after Analysis.
_immutable_browser_datas = []
for _source, _destination in _playwright_datas:
    if sys.platform == "darwin" and _destination.startswith("playwright-browsers/"):
        _immutable_browser_datas.append((_source, _destination))
    else:
        datas.append((_source, _destination))
hiddenimports += _playwright_hiddenimports
datas += collect_data_files("silero_vad")          # silero_vad/data/*.jit
datas += collect_data_files("faster_whisper")      # tokenizer.json
datas += collect_data_files("tzdata")              # IANA tz db
datas += collect_data_files("ctranslate2")         # any shipped configs
datas += collect_data_files("sqlite_vec", includes=["*.dll", "*.dylib", "*.so"])
for _pkg in ["scrapling", "browserforge", "apify_fingerprint_datapoints"]:
    try:
        datas += collect_data_files(_pkg)
    except Exception:
        print(f"[spec] WARN: {_pkg} data not importable, skip")
datas += collect_data_files("agent_reach")
datas += collect_data_files("simple_harness_service")
datas += copy_metadata("simple-harness-service-sdk")
datas += [
    (str(sdk_service_wheel_path()), "vendor"),
    (str(sdk_service_candidate_manifest_path()), "vendor"),
    # P4-S22 fix: ship the canonical migrations directory under
    # ``deskpet/memory/migrations`` (where the actual v9/v10/v11 SQL
    # files live). The legacy ``memory/migrations`` only contains
    # ``001_initial.sql`` (long stale) — keep both for back-compat
    # with any tests that still touch the legacy path.
    ("memory/migrations", "memory/migrations"),    # legacy path
    ("deskpet/memory/migrations", "deskpet/memory/migrations"),  # canonical
    ("deskpet/companion/migrations", "deskpet/companion/migrations"),
    ("deskpet/companion/eval_suites", "deskpet/companion/eval_suites"),
    # 2026-05-30 P0 bug fix #7: ship builtin skills directory.
    # Production install had `skill.reload_ok count=0` because PyInstaller
    # never bundled `deskpet/skills/builtin/` (it's a data tree, not a
    # Python module). Result: B1-B10 skill tests all failed — LLM真选
    # skill_invoke 但 SkillLoader 找不到 excel-generate / doc-edit / ppt-
    # generate 等 → 没文件生成 → fake completion 漏网。
    # Discovered via CDP-driven R3-3 test + backend log skill.reload_ok=0.
    ("../capability-packs", "capability-packs"),
    ("deskpet/capabilities/schemas", "deskpet/capabilities/schemas"),
    # P4-S21 #12: ship the unified-schema config.toml so seed_user_config_if_missing
    # has a source to seed from / migrate legacy installs against. Without this,
    # frozen builds with no <exe_dir>/config.toml returned None and the migration
    # path was a no-op.
    ("../config.toml", "."),
    # compile_workflow() fingerprints the exact dependency lock as part of
    # every durable manifest. In frozen mode definition.py resolves this to
    # _MEIPASS/uv.lock, so omitting it disables workflow startup.
    ("uv.lock", "."),
    ("../resources/diagnostic-redaction.json", "resources"),
    (
        "deskpet/tools/execution_build_sources.json",
        "deskpet/tools",
    ),
    (
        "deskpet/tools/execution_build_manifest.json",
        "deskpet/tools",
    ),
    (
        "deskpet/tools/tool_effect_policy_manifest.json",
        "deskpet/tools",
    ),
]

# --- 2a-bis. busybox-w32 (P5-S2 — 2026-05-10) --------------------------
# Ship the ~700KB busybox.exe so end users without Git for Windows still
# get a competent unix-like shell for run_shell. run_shell.py picks
# this up via _bundled_busybox_path() — checks _MEIPASS first, so the
# datas entry below is what makes the frozen build self-sufficient.
#
# The file is fetched by `scripts/download_busybox.ps1` and committed
# to the repo at `resources/busybox-w32/busybox.exe`. If that file
# isn't present at build time, the spec gracefully omits it (the
# runtime falls back to PowerShell or cmd, which the tier system
# handles).
import os as _os  # noqa: PLC0415
_busybox_src = _os.path.join("..", "resources", "busybox-w32", "busybox.exe")
if sys.platform == "win32" and _os.path.isfile(_busybox_src):
    datas += [(_busybox_src, ".")]
    print(f"[spec] bundling busybox: {_busybox_src}")
elif sys.platform == "win32":
    print(
        f"[spec] WARNING: busybox not found at {_busybox_src} — "
        "frozen build will rely on Git Bash / PowerShell / cmd at runtime. "
        "Run scripts/download_busybox.ps1 to enable bundled fallback."
    )

# --- 2b. Bundled model weights (P4-S20+ install bundle) -----------------
# Ship BGE-M3 (vector embedder, ~2.2 GB) and faster-whisper-large-v3-turbo
# INT8 (ASR, ~1.5 GB) inside the frozen bundle. Lands at
# `_internal/models/<subdir>/...` and is picked up by paths.resolve_model_dir
# when the user has not provisioned `%LocalAppData%/deskpet/models/<subdir>/`.
#
# Together these add ~3.7 GB to the bundle. NSIS LZMA compression usually
# halves it, so the final installer is ~2 GB. Acceptable for "download &
# run, no further setup" UX.
#
# Sources are populated by the dev workflow:
#   - assets/bge-m3-int8/                        (robocopy from %LocalAppData%/deskpet/models/)
#   - assets/faster-whisper-large-v3-turbo/      (copied from HF cache,
#                                                 mobiuslabsgmbh INT8 ct2 build)
# Both directories are gitignored — see backend/.gitignore.
# P4-S20 install bundle Plan A (post-NSIS-mmap-failure):
# We previously bundled BGE-M3 (1.1 GB fp16) + faster-whisper INT8 (1.5 GB)
# directly into the PyInstaller dist. Tauri NSIS makensis is 32-bit and
# its cumulative mmap address space caps out around ~3.5 GB; bundling
# 7.7 GB of resources blows that limit with `Internal compiler error
# #12345: error mmapping file (...) is out of range`. NSIS amd64-Unicode
# builds exist as community forks (e.g. negrutiu/nsis) but introduce a
# trust dependency we'd rather avoid.
#
# Plan A: ship a thin (~1.5 GB) bundle, rely on first-run download.
# `paths.resolve_model_dir` already does multi-source fallback
# (user_models_dir → _MEIPASS/models → backend/assets), so:
#  - Frozen bundle: models NOT bundled → first-run code (or user-run
#    download script) populates `%LocalAppData%/deskpet/models/`.
#  - Dev mode: backend/assets/ still has the weights → resolves there.
# Either way the runtime path is identical.
#
# P4-S20 MSI fat bundle: ship models inside the installer for true
# zero-config "download → install → launch" UX. Set
# DESKPET_BUNDLE_MODELS=0 to opt out (yields a thin bundle that relies
# on first-run download via scripts/setup_models.py).
if os.environ.get("DESKPET_BUNDLE_MODELS", "1") == "1":
    _BUNDLED_MODELS = [
        ("assets/bge-m3-int8", "models/bge-m3-int8"),
        ("assets/faster-whisper-large-v3-turbo", "models/faster-whisper-large-v3-turbo"),
    ]
    for _src, _dest in _BUNDLED_MODELS:
        if os.path.isdir(_src):
            datas.append((_src, _dest))
            print(f"[spec] bundling model: {_src} -> {_dest}")
        else:
            print(f"[spec] WARN: bundled model source missing, skipping: {_src}")
else:
    print("[spec] thin bundle: models NOT bundled (DESKPET_BUNDLE_MODELS=0)")

# --- 3. Analysis --------------------------------------------------------
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Things torch/transformers sometimes drag in that we never use.
        # Keep this conservative — overzealous excludes cause runtime
        # ImportError deep in a stack trace.
        "tkinter",
        "matplotlib",
        "IPython",
        "notebook",
        "jupyter",
        "pytest",
        "_pytest",
        # FlagEmbedding 推理经 transformers.trainer 拉入 datasets(训练用)，
        # 但推理 lazy `is_datasets_available()` 可选；其 PyInstaller 隔离
        # 子进程 bindepend 导入会崩(SubprocessDiedError)→ 排除即修复 +
        # 减体积，运行时 is_datasets_available()→False 安全降级。
        "datasets",
    ],
    noarchive=False,
    # WorkflowDefinition hashes callable source text. PyInstaller normally
    # stores modules only in PYZ bytecode, so inspect.getsource() fails in the
    # installed backend and disables the entire durable workflow service.
    # Keep definition modules as external source alongside the frozen app;
    # source-mode builds and frozen builds then compute identical manifests.
    module_collection_mode={"deskpet.workflows.definitions": "py"},
)

a.datas += [
    (destination, source, "DATA")
    for destination, source in format_binaries_and_datas(_immutable_browser_datas)
]

# --- 3b. Defensive torch CUDA DLL strip ---------------------------------
# REQUIRED SETUP: the backend venv MUST install torch's CPU-only wheel
#   pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
# torch is used ONLY for VRAM detection (observability/vram.py), which
# wraps every call in try/except and falls back to vram_gb = 0.0 (→ CPU
# tier) when CUDA is unavailable. faster-whisper's GPU path uses
# ctranslate2's independent CUDA DLLs under _internal/ctranslate2/.
# If someone accidentally installs torch+cu124 (3.5 GB of CUDA DLLs under
# torch/lib/), the filter below strips the biggest offenders at build
# time so the bundle stays under P3-G2's 3.5 GB budget. On a CPU-only
# install this filter is a no-op.
_CUDA_DLL_PREFIXES = (
    # P4-S20 fat MSI bundle: empty strip list — keep the full torch
    # CUDA stack. shm.dll directly imports torch_cuda.dll which in
    # turn lazily binds cudnn / cublas / cufft / etc., so cherry-
    # picking what to strip is fragile (broke on first try).
    # MSI (vs NSIS) handles 7+ GB dist sizes natively, so we don't
    # need the strip dance.
    # Original aggressive list (kept for reference, was active in
    # P3-S5's NSIS-only era):
    #   "torch_cuda", "cudnn", "cublas", "cufft", "cusparse",
    #   "cusolver", "curand", "nvrtc", "nvjitlink", "cupti",
    #   "nvtoolsext", "c10_cuda", "caffe2_nvrtc", "cudart",
)


def _is_torch_cuda_bloat(entry):
    dest = entry[0].replace("\\", "/").lower()
    if not dest.startswith("torch/lib/"):
        return False
    name = dest.rsplit("/", 1)[-1]
    return any(name.startswith(p) for p in _CUDA_DLL_PREFIXES)


# P4-S20 thin bundle: re-enabled strip. We keep c10_cuda + cudart in
# torch/lib/ (removed from `_CUDA_DLL_PREFIXES`) so shm.dll's deps
# resolve, while everything else (cudnn family, cufft, curand,
# cusolver, cusparse — collectively ~3 GB) is dropped. main.py
# registers `_MEIPASS/ctranslate2/` in the DLL search path BEFORE any
# `import torch`, so torch's `_load_dll_libraries` finds cudart/cublas
# there. Without the strip, dist is ~5 GB (vs ~1.5 GB with strip),
# which blows NSIS 32-bit makensis's cumulative mmap budget.
a.binaries = [b for b in a.binaries if not _is_torch_cuda_bloat(b)]

# --- 3c. Re-bundle the minimal CUDA DLLs ctranslate2 actually needs -----
# After the torch CUDA strip above (saves ~2.9 GB), ctranslate2's GPU
# path dlopen's a small set of NVIDIA DLLs that torch's filter removed:
#   cublas64_12.dll, cublasLt64_12.dll  — matrix kernels (~370 MB together)
#   cudart64_12.dll                     — CUDA runtime shim (~600 KB)
#   nvrtc64_120_0.dll, nvrtc-builtins64_129.dll — runtime kernel compile
# ctranslate2 already ships cudnn64_9.dll in its own wheel.
#
# These DLLs come from the standalone pip packages:
#   pip install nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12
# Dropped into `_internal/ctranslate2/` alongside cudnn64_9.dll so they
# resolve via ctranslate2's own AddDllDirectory registration.
_NVIDIA_DLL_DIRS = [
    os.path.join(_site_packages, "nvidia", "cublas", "bin"),
    os.path.join(_site_packages, "nvidia", "cuda_runtime", "bin"),
    os.path.join(_site_packages, "nvidia", "cuda_nvrtc", "bin"),
]
for _dir in _NVIDIA_DLL_DIRS:
    if not os.path.isdir(_dir):
        continue
    for _dll in glob.glob(os.path.join(_dir, "*.dll")):
        # Dest "ctranslate2/<name>.dll" → ends up next to cudnn64_9.dll
        # inside the ctranslate2 search dir registered by the wheel.
        a.binaries.append(
            (f"ctranslate2/{os.path.basename(_dll)}", _dll, "BINARY")
        )

# Recursive data trees and third-party hooks must not carry private workspace
# files into the onedir artifact. Check the expanded destinations before COLLECT;
# required Python modules (including secrets.py) and pinned wheels remain valid.
for _dest, _src, _type in [*a.datas, *a.binaries]:
    _parts = Path(_dest.replace("\\", "/")).parts
    if any(
        part.lower() in {".git", ".venv", "secrets", ".local-test-evidence"}
        or part.lower() == ".env"
        or part.lower().startswith(".env.")
        or part.lower().startswith("local-dev-credentials.")
        for part in _parts
    ):
        raise RuntimeError(f"private workspace data must not be bundled: {_dest}")

pyz = PYZ(a.pure, a.zipped_data)

# --- 4. EXE + COLLECT ---------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="deskpet-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                                     # UPX breaks CUDA DLLs
    console=True,                                  # SHARED_SECRET on stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="deskpet-backend",                        # dist/<this>/
)

# A long build must not silently mix inputs from a newer checkout. A mismatch
# invalidates this build; the captured resource must never be rewritten to HEAD.
if _capture_host_build_identity(_repo_root) != _host_build_identity:
    raise RuntimeError("Host build inputs changed during PyInstaller; rebuild from a frozen checkout")
