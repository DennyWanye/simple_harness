#!/usr/bin/env python3
"""Read-only inventory of Grok HTN-acceptance result.json files.

Walks `.local-test-evidence/2026-09-16/htn-acceptance/` and writes:

- episodes.csv
- sha256-manifest.txt
- copies of scanned-clean result.json under episodes/<batch_dir>/<episode>/

Does not read or copy secret files (auth.json, llm_runtime*.json, .env,
*.bak-refresh). Does not copy orchestrator.db, worktree, or delivery.
Does not modify any runs* directory.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

EVIDENCE_ROOT = Path(
    "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness"
    "/.local-test-evidence/2026-09-16/htn-acceptance"
)
OUT_DIR = Path(__file__).resolve().parent

CSV_COLUMNS = [
    "batch_dir",
    "episode",
    "arm",
    "layer",
    "task_id",
    "repetition",
    "sdk_commit",
    "host_commit",
    "started_at",
    "mission_status",
    "stop_reason",
    "hidden_status",
    "public_suite",
    "declared_complete",
    "false_completion",
    "valid_success",
    "budget_conserved",
    "usage_fully_known",
    "unknown_usage_calls",
    "attempts_created",
    "settled_tokens",
    "elapsed_seconds",
    "result_json_sha256",
]

SECRET_PATTERNS = [
    re.compile(r"xai-[A-Za-z0-9]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{10,}"),  # 词边界：避免把 task-<hex> 误判（2026-09-18 指挥者修正）
    re.compile(r"Bearer [A-Za-z0-9._-]{20,}"),
    re.compile(r"refresh_token"),
    re.compile(r'"api_key"\s*:\s*"[^"]{8,}'),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\."),
]

SECRET_NAME_FRAGMENTS = (
    "auth.json",
    "llm_runtime",
    ".env",
    "bak-refresh",
)

SKIP_DIR_NAMES = {
    "receipts",
    "__pycache__",
    "worktree",
    "delivery",
    "reference",
    "hidden",
    "repo",
    "history",
    "worker-logs",
}

# Official batches 2–6 (used when total result.json size exceeds 8 MiB).
BATCH2_TO_6_PREFIXES = (
    "runs-c7cfedd-batch2",
    "runs-2845b7e-batch3-paused",
    "runs-e149524-batch3-paused",
    "runs-d360750-batch4-stopped",
    "runs-f2dfa64-batch5",
    "runs/h-arm",
    "runs/h-arm/",
)

COPY_SIZE_CAP = 8 * 1024 * 1024


def is_secret_path(path: Path) -> bool:
    name = path.name.lower()
    return any(frag in name for frag in SECRET_NAME_FRAGMENTS)


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def scan_secrets(text: str) -> list[str]:
    hits: list[str] = []
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    return hits


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def classify_result_path(path: Path) -> tuple[str, str] | None:
    """Return (batch_dir, episode) for a canonical result.json, else None."""
    rel = path.relative_to(EVIDENCE_ROOT)
    parts = rel.parts
    if "receipts" in parts:
        return None
    if path.name != "result.json":
        return None
    parent = path.parent
    episode = parent.name
    # episodes/<ep>/result.json
    if parent.parent.name == "episodes":
        batch_dir = str(parent.parent.parent.relative_to(EVIDENCE_ROOT))
        return batch_dir, episode
    # superseded-*/<ep>/result.json
    if parent.parent.name.startswith("superseded"):
        batch_dir = str(parent.parent.relative_to(EVIDENCE_ROOT))
        return batch_dir, episode
    # spoiled-*/result.json (episode dir may be the spoiled folder itself)
    if "spoiled" in parent.name or any(p.startswith("spoiled") for p in parts):
        # spoiled-401-0516/<maybe-ep>/result.json or spoiled-401-0516/result.json
        if parent.name.startswith("spoiled"):
            batch_dir = str(parent.parent.relative_to(EVIDENCE_ROOT))
            return batch_dir, parent.name
        batch_dir = str(parent.parent.relative_to(EVIDENCE_ROOT))
        return batch_dir, episode
    # f-arm-invalid-401/<ep>/result.json
    if "invalid" in parent.parent.name or parent.parent.name.endswith("invalid-401"):
        batch_dir = str(parent.parent.relative_to(EVIDENCE_ROOT))
        return batch_dir, episode
    # incomplete dirs already match episodes/<ep-incomplete>/ above
    return None


def json_get(obj, *keys, default=""):
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    if cur is None:
        return ""
    return cur


def csv_bool(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value in ("", None):
        return ""
    return str(value).lower() if isinstance(value, bool) else str(value)


def extract_row(batch_dir: str, episode: str, path: Path, digest: str, data: dict) -> dict:
    grading = data.get("grading") or {}
    hidden = grading.get("hidden") if isinstance(grading, dict) else None
    public = grading.get("public") if isinstance(grading, dict) else None
    mission_account = data.get("mission_account") or {}
    hidden_status = ""
    if isinstance(hidden, dict):
        hidden_status = hidden.get("status") or ""
    public_suite = data.get("public_suite")
    if public_suite in (None, ""):
        if isinstance(public, dict):
            public_suite = public.get("status") or ""
        else:
            public_suite = ""
    elapsed = data.get("elapsed_seconds")
    if isinstance(elapsed, float):
        elapsed_s = f"{elapsed:.2f}"
    elif elapsed is None:
        elapsed_s = ""
    else:
        elapsed_s = str(elapsed)
    sdk = data.get("sdk_commit") or ""
    host = data.get("host_commit") or ""
    return {
        "batch_dir": batch_dir,
        "episode": episode,
        "arm": data.get("arm") or "",
        "layer": data.get("layer") or "",
        "task_id": data.get("task_id") or "",
        "repetition": "" if data.get("repetition") is None else str(data.get("repetition")),
        "sdk_commit": sdk,
        "host_commit": host,
        "started_at": data.get("started_at") or "",
        "mission_status": data.get("mission_status") or "",
        "stop_reason": data.get("stop_reason") if data.get("stop_reason") is not None else "",
        "hidden_status": hidden_status,
        "public_suite": public_suite,
        "declared_complete": csv_bool(data.get("declared_complete")),
        "false_completion": csv_bool(data.get("false_completion")),
        "valid_success": csv_bool(data.get("valid_success")),
        "budget_conserved": csv_bool(data.get("budget_conserved")),
        "usage_fully_known": csv_bool(data.get("usage_fully_known")),
        "unknown_usage_calls": (
            "" if data.get("unknown_usage_calls") is None else str(data.get("unknown_usage_calls"))
        ),
        "attempts_created": (
            ""
            if not isinstance(mission_account, dict) or mission_account.get("attempts_created") is None
            else str(mission_account.get("attempts_created"))
        ),
        "settled_tokens": (
            ""
            if not isinstance(mission_account, dict) or mission_account.get("settled_tokens") is None
            else str(mission_account.get("settled_tokens"))
        ),
        "elapsed_seconds": elapsed_s,
        "result_json_sha256": digest,
    }


def iter_result_json() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(EVIDENCE_ROOT):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        if is_secret_path(Path(dirpath)):
            dirnames[:] = []
            continue
        for name in filenames:
            p = Path(dirpath) / name
            if is_secret_path(p):
                continue
            if name == "result.json":
                found.append(p)
    found.sort()
    return found


def iter_orchestrator_db() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(EVIDENCE_ROOT):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        if is_secret_path(Path(dirpath)):
            dirnames[:] = []
            continue
        for name in filenames:
            if name == "orchestrator.db":
                p = Path(dirpath) / name
                if is_secret_path(p):
                    continue
                found.append(p)
    found.sort()
    return found


def in_batches_2_to_6(batch_dir: str) -> bool:
    if batch_dir == "runs/h-arm" or batch_dir.startswith("runs/h-arm/"):
        return True
    return any(
        batch_dir == p or batch_dir.startswith(p + "/")
        for p in BATCH2_TO_6_PREFIXES
        if p not in ("runs/h-arm", "runs/h-arm/")
    )


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            fp = Path(dirpath) / name
            try:
                total += fp.stat().st_size
            except OSError:
                continue
    return total


def main() -> int:
    if not EVIDENCE_ROOT.is_dir():
        print(f"missing evidence root: {EVIDENCE_ROOT}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    episodes_root = OUT_DIR / "episodes"
    episodes_root.mkdir(exist_ok=True)

    result_paths = iter_result_json()
    records: list[dict] = []
    secret_hits: list[dict] = []
    skipped_unclassified: list[str] = []
    copy_candidates: list[tuple[str, str, Path, int]] = []
    total_result_bytes = 0

    for path in result_paths:
        classified = classify_result_path(path)
        if classified is None:
            rel = str(path.relative_to(EVIDENCE_ROOT))
            if "/receipts/" not in rel.replace("\\", "/"):
                skipped_unclassified.append(rel)
            continue
        batch_dir, episode = classified
        digest, size = sha256_file(path)
        total_result_bytes += size
        raw = path.read_text(encoding="utf-8", errors="replace")
        hits = scan_secrets(raw)
        if hits:
            secret_hits.append(
                {
                    "path": str(path.relative_to(EVIDENCE_ROOT)),
                    "batch_dir": batch_dir,
                    "episode": episode,
                    "patterns": hits,
                    "bytes": size,
                    "sha256": digest,
                }
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        row = extract_row(batch_dir, episode, path, digest, data)
        records.append(row)
        if not hits:
            copy_candidates.append((batch_dir, episode, path, size))

    records.sort(key=lambda r: (r["batch_dir"], r["episode"]))

    csv_path = OUT_DIR / "episodes.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow(row)

    cap_triggered = total_result_bytes > COPY_SIZE_CAP
    copied = 0
    copied_bytes = 0
    skipped_copy_cap = 0
    skipped_secret = len(secret_hits)
    for batch_dir, episode, path, size in copy_candidates:
        if cap_triggered and not in_batches_2_to_6(batch_dir):
            skipped_copy_cap += 1
            continue
        dest_dir = episodes_root / batch_dir / episode
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "result.json"
        shutil.copy2(path, dest)
        copied += 1
        copied_bytes += size

    db_entries: list[tuple[str, str, int]] = []
    for db in iter_orchestrator_db():
        digest, size = sha256_file(db)
        rel = str(db.relative_to(EVIDENCE_ROOT))
        db_entries.append((rel, digest, size))

    manifest_path = OUT_DIR / "sha256-manifest.txt"
    size_by_key = {}
    for batch_dir, episode, path, size in copy_candidates:
        size_by_key[(batch_dir, episode)] = size
    for hit in secret_hits:
        size_by_key[(hit["batch_dir"], hit["episode"])] = hit["bytes"]

    with manifest_path.open("w", encoding="utf-8") as fh:
        fh.write("# sha256 清单（只哈希，不复制 DB / worktree / delivery）\n")
        fh.write(f"# evidence_root={EVIDENCE_ROOT}\n")
        fh.write(f"# result.json_count={len(records)}\n")
        fh.write(f"# orchestrator.db_count={len(db_entries)}\n")
        fh.write("# 列: kind  relative_path  sha256  bytes\n")
        for row in records:
            rel = f"{row['batch_dir']}/{row['episode']}/result.json"
            size = size_by_key.get((row["batch_dir"], row["episode"]), 0)
            fh.write(f"result.json  {rel}  {row['result_json_sha256']}  {size}\n")
        for rel, digest, size in db_entries:
            fh.write(f"orchestrator.db  {rel}  {digest}  {size}\n")

    # Evidence directory sizes for section ⑧
    batch_roots = [
        "runs-a8e902a-invalid",
        "runs-c7cfedd-batch2",
        "runs-e149524-batch3-paused",
        "runs-2845b7e-batch3-paused",
        "runs-d360750-batch3-paused",
        "runs-d360750-batch4-stopped",
        "runs-f2dfa64-batch5",
        "runs/h-arm",
        "runs/report-batch5-gate",
        "runs/report-batch6-gate",
        "runs-p23e-probe",
        "runs-p23f-probe",
        "runs-preflight-discarded",
        "runs-a8e902a-invalid/report-batch-1a",
        "runs-c7cfedd-batch2/report-refreeze-2-c7cfedd",
    ]
    print("=== STATS ===")
    print(f"csv_rows={len(records)}")
    print(f"manifest_result_json={len(records)}")
    print(f"manifest_db={len(db_entries)}")
    print(f"secret_hits={len(secret_hits)}")
    print(f"total_result_bytes={total_result_bytes}")
    print(f"copy_cap_triggered={cap_triggered}")
    print(f"copied={copied}")
    print(f"copied_bytes={copied_bytes}")
    print(f"skipped_copy_cap={skipped_copy_cap}")
    print(f"skipped_secret={skipped_secret}")
    print(f"skipped_unclassified={len(skipped_unclassified)}")
    for rel in skipped_unclassified:
        print(f"UNCLASSIFIED {rel}")
    for hit in secret_hits:
        print(f"SECRET {hit['path']} patterns={hit['patterns']}")

    from collections import Counter

    counts = Counter(r["batch_dir"] for r in records)
    print("=== BATCH COUNTS ===")
    for k in sorted(counts):
        print(f"{k}\t{counts[k]}")

    print("=== DIR SIZES ===")
    for rel in batch_roots:
        p = EVIDENCE_ROOT / rel
        n_ep = sum(1 for r in records if r["batch_dir"] == rel or r["batch_dir"].startswith(rel + "/"))
        print(f"{rel}\tbytes={dir_size_bytes(p)}\tepisodes_in_csv={n_ep}\texists={p.exists()}")

    print("=== ROWS ===")
    for row in records:
        print(
            "\t".join(
                [
                    row["batch_dir"],
                    row["episode"],
                    row["arm"],
                    row["layer"],
                    row["task_id"],
                    row["repetition"],
                    row["sdk_commit"],
                    row["host_commit"],
                    row["started_at"],
                    row["mission_status"],
                    str(row["stop_reason"]),
                    row["hidden_status"],
                    str(row["public_suite"]),
                    row["declared_complete"],
                    row["false_completion"],
                    row["valid_success"],
                    row["budget_conserved"],
                    row["usage_fully_known"],
                    row["unknown_usage_calls"],
                    row["attempts_created"],
                    row["settled_tokens"],
                    row["elapsed_seconds"],
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
