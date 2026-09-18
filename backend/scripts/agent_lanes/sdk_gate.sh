#!/bin/bash
# sdk_gate.sh - deterministic gate for one SDK slice.
# Usage: sdk_gate.sh <sdk-worktree> <base-commit> [--tests "<pytest paths ...>"]
#                   [--allow <allowlist-file>] [--full] [--max-sentinel N] [--out <report.json>]
# Every check is written into a JSON report as {name, ok, detail}; top-level ok is the AND.
# Any failed check => exit code 1.
# Env: GATE_SKIP_PYTHON=1 skips the python-dependent items (ruff/import_origin/targeted/full_target/legacy).
set -u

# ---------------------------------------------------------------------------
# parse_pytest_tail: robustly parse a pytest summary tail.
# Usage: parse_pytest_tail "<tail line>"
# Echoes: "passed=N failed=N errors=N" (unknown / "no tests ran" => all zeros).
# Defined at top level so the self-test can `source` this file and unit-test it.
# ---------------------------------------------------------------------------
parse_pytest_tail() {
  local line="${1:-}"
  local passed failed errors
  passed=$(printf '%s\n' "$line" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | tail -1)
  failed=$(printf '%s\n' "$line" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' | tail -1)
  errors=$(printf '%s\n' "$line" | grep -oE '[0-9]+ errors?' | grep -oE '[0-9]+' | tail -1)
  [ -n "$passed" ] || passed=0
  [ -n "$failed" ] || failed=0
  [ -n "$errors" ] || errors=0
  printf 'passed=%s failed=%s errors=%s\n' "$passed" "$failed" "$errors"
}

main() {
  local SDK="" BASE=""
  local TESTS="" ALLOW="" OUT="" MAX_SENTINEL=""
  local FULL=0

  if [ "$#" -lt 2 ]; then
    echo "usage: sdk_gate.sh <sdk-worktree> <base-commit> [--tests \"...\"] [--allow <file>] [--full] [--max-sentinel N] [--out <report.json>]" >&2
    return 1
  fi

  SDK="$1"; BASE="$2"; shift 2
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --tests) TESTS="${2:-}"; shift 2 ;;
      --allow) ALLOW="${2:-}"; shift 2 ;;
      --full) FULL=1; shift ;;
      --max-sentinel) MAX_SENTINEL="${2:-}"; shift 2 ;;
      --out) OUT="${2:-}"; shift 2 ;;
      *) echo "sdk_gate.sh: unknown argument: $1" >&2; return 1 ;;
    esac
  done

  if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
    echo "sdk_gate.sh: sdk worktree not found: $SDK" >&2
    return 1
  fi
  if [ -z "$OUT" ]; then
    OUT="$SDK/.sdk_gate_report.json"
  fi
  mkdir -p "$(dirname "$OUT")"
  local OUTDIR; OUTDIR="$(cd "$(dirname "$OUT")" && pwd)"
  local REPORT="$OUTDIR/$(basename "$OUT")"

  # ---- accumulate items ---------------------------------------------------
  local RESULTS_DIR; RESULTS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sdk_gate_items.XXXXXX")"
  local ITEM_N=0
  # record <name> <ok:true|false> <detail>
  record() {
    ITEM_N=$((ITEM_N + 1))
    printf '%s' "$1" > "$RESULTS_DIR/$ITEM_N.name"
    printf '%s' "$2" > "$RESULTS_DIR/$ITEM_N.ok"
    printf '%s' "$3" > "$RESULTS_DIR/$ITEM_N.detail"
  }

  local SKIP_PY=0
  [ "${GATE_SKIP_PYTHON:-0}" = "1" ] && SKIP_PY=1

  # ---- 1. clean -----------------------------------------------------------
  local status_out
  status_out=$(git -C "$SDK" status --short 2>&1)
  if [ -z "$status_out" ]; then
    record "clean" "true" "working tree clean"
  else
    record "clean" "false" "working tree dirty:
$status_out"
  fi

  # ---- changed files (vs base) -------------------------------------------
  local changed_files
  changed_files=$(git -C "$SDK" diff --name-only "$BASE"..HEAD 2>/dev/null)

  # ---- 2. allowlist -------------------------------------------------------
  if [ -z "$ALLOW" ]; then
    record "allowlist" "true" "skipped: no --allow given"
  elif [ ! -f "$ALLOW" ]; then
    record "allowlist" "false" "allowlist file not found: $ALLOW"
  else
    local offenders="" f glob matched
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      matched=0
      while IFS= read -r glob; do
        glob="${glob#"${glob%%[![:space:]]*}"}"
        glob="${glob%"${glob##*[![:space:]]}"}"
        [ -n "$glob" ] || continue
        case "$glob" in \#*) continue ;; esac
        case "$f" in
          $glob) matched=1; break ;;
        esac
      done < "$ALLOW"
      if [ "$matched" -eq 0 ]; then
        offenders="$offenders$f
"
      fi
    done <<EOF
$changed_files
EOF
    if [ -z "$offenders" ]; then
      record "allowlist" "true" "all changed files match allowlist"
    else
      record "allowlist" "false" "files outside allowlist:
$offenders"
    fi
  fi

  # ---- 3. contracts_frozen -----------------------------------------------
  local ct_out ct_bad
  ct_out=$(git -C "$SDK" diff --name-status "$BASE"..HEAD -- src/agent_orchestrator/contracts 2>/dev/null)
  ct_bad=""
  if [ -n "$ct_out" ]; then
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      local st="${line%%$'\t'*}"
      st="${line%%[[:space:]]*}"
      case "$st" in
        A*) : ;;
        *) ct_bad="$ct_bad$line
" ;;
      esac
    done <<EOF
$ct_out
EOF
  fi
  if [ -z "$ct_bad" ]; then
    record "contracts_frozen" "true" "only additions (or no change) under src/agent_orchestrator/contracts"
  else
    record "contracts_frozen" "false" "non-additive changes under contracts:
$ct_bad"
  fi

  # ---- 4. no_secrets ------------------------------------------------------
  local added_tmp secret_hits
  added_tmp="$RESULTS_DIR/added_lines.txt"
  git -C "$SDK" diff "$BASE"..HEAD 2>/dev/null | grep -aE '^\+' | grep -avE '^\+\+\+' > "$added_tmp" || true
  secret_hits=$(grep -aE '(^|[^A-Za-z0-9])sk-[A-Za-z0-9]{10,}|xai-[A-Za-z0-9]{10,}|Bearer [A-Za-z0-9._-]{20,}|eyJ[A-Za-z0-9_-]{20,}\.' "$added_tmp" 2>/dev/null || true)
  if [ -z "$secret_hits" ]; then
    record "no_secrets" "true" "no secret-like added lines"
  else
    record "no_secrets" "false" "secret-like added lines:
$secret_hits"
  fi

  # ---- changed .py files --------------------------------------------------
  local py_files
  py_files=$(printf '%s\n' "$changed_files" | grep -E '\.py$' || true)

  # ---- 5. ruff ------------------------------------------------------------
  if [ "$SKIP_PY" -eq 1 ]; then
    record "ruff" "true" "skipped: GATE_SKIP_PYTHON=1"
  elif [ -z "$py_files" ]; then
    record "ruff" "true" "skipped: no changed .py files"
  else
    local ruff_log ruff_rc
    ruff_log="$OUTDIR/ruff.log"
    ( cd "$SDK" && uv run ruff check $py_files ) > "$ruff_log" 2>&1
    ruff_rc=$?
    if [ "$ruff_rc" -eq 0 ]; then
      record "ruff" "true" "ruff check passed on changed .py files"
    else
      record "ruff" "false" "ruff check failed (rc=$ruff_rc); see $ruff_log"
    fi
  fi

  # ---- 6. import_origin ---------------------------------------------------
  if [ "$SKIP_PY" -eq 1 ]; then
    record "import_origin" "true" "skipped: GATE_SKIP_PYTHON=1"
  else
    local origin_out origin_rc sdk_real
    origin_out=$( cd "$SDK" && PYTHONPATH=src uv run python -c "import agent_orchestrator,os;print(os.path.realpath(agent_orchestrator.__file__))" 2>/dev/null )
    origin_rc=$?
    sdk_real=$( cd "$SDK" && pwd -P )
    case "$origin_out" in
      "$sdk_real"/*)
        if [ "$origin_rc" -eq 0 ]; then
          record "import_origin" "true" "imports from $origin_out"
        else
          record "import_origin" "false" "import failed (rc=$origin_rc): $origin_out"
        fi
        ;;
      *)
        record "import_origin" "false" "agent_orchestrator imported from outside the worktree: '$origin_out' (expected under $sdk_real)"
        ;;
    esac
  fi

  # ---- 7. targeted --------------------------------------------------------
  if [ "$SKIP_PY" -eq 1 ]; then
    record "targeted" "true" "skipped: GATE_SKIP_PYTHON=1"
  elif [ -z "$TESTS" ]; then
    record "targeted" "true" "skipped: no --tests given"
  else
    local t_log t_rc t_tail t_par t_passed t_failed t_errors
    t_log="$OUTDIR/targeted.log"
    ( cd "$SDK" && PYTHONPATH=src uv run pytest $TESTS -q -p no:cacheprovider ) > "$t_log" 2>&1
    t_rc=$?
    t_tail=$(grep -avE '^[[:space:]]*$' "$t_log" | tail -1)
    t_par=$(parse_pytest_tail "$t_tail")
    t_passed=$(printf '%s' "$t_par" | sed -n 's/.*passed=\([0-9]*\).*/\1/p')
    t_failed=$(printf '%s' "$t_par" | sed -n 's/.*failed=\([0-9]*\).*/\1/p')
    t_errors=$(printf '%s' "$t_par" | sed -n 's/.*errors=\([0-9]*\).*/\1/p')
    if [ "$t_rc" -eq 0 ] && [ "$t_failed" -eq 0 ] && [ "$t_errors" -eq 0 ]; then
      record "targeted" "true" "passed=$t_passed failed=$t_failed errors=$t_errors; tail: $t_tail"
    else
      record "targeted" "false" "rc=$t_rc passed=$t_passed failed=$t_failed errors=$t_errors; log: $t_log; tail: $t_tail"
    fi
  fi

  # ---- 8. full_target + legacy (only with --full) -------------------------
  local baseline_json full_base legacy_base
  baseline_json="$SDK/plans/llm-native-htn/H0/test-results.json"
  full_base=""; legacy_base=""
  if [ -f "$baseline_json" ]; then
    full_base=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('full_target',{}).get('passed',''))" "$baseline_json" 2>/dev/null || true)
    legacy_base=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('legacy',{}).get('passed',''))" "$baseline_json" 2>/dev/null || true)
  fi

  if [ "$FULL" -eq 1 ]; then
    if [ "$SKIP_PY" -eq 1 ]; then
      record "full_target" "true" "skipped: GATE_SKIP_PYTHON=1"
      record "legacy" "true" "skipped: GATE_SKIP_PYTHON=1"
    else
      # full_target
      local ft_log ft_rc ft_tail ft_par ft_passed ft_failed ft_errors
      ft_log="$OUTDIR/full_target.log"
      ( cd "$SDK" && PYTHONPATH=src uv run pytest tests/orchestrator/full_target -q -p no:cacheprovider ) > "$ft_log" 2>&1
      ft_rc=$?
      ft_tail=$(grep -avE '^[[:space:]]*$' "$ft_log" | tail -1)
      ft_par=$(parse_pytest_tail "$ft_tail")
      ft_passed=$(printf '%s' "$ft_par" | sed -n 's/.*passed=\([0-9]*\).*/\1/p')
      ft_failed=$(printf '%s' "$ft_par" | sed -n 's/.*failed=\([0-9]*\).*/\1/p')
      ft_errors=$(printf '%s' "$ft_par" | sed -n 's/.*errors=\([0-9]*\).*/\1/p')
      if [ "$ft_failed" -eq 0 ] && [ "$ft_errors" -eq 0 ] && [ -n "$full_base" ] && [ "$ft_passed" -ge "$full_base" ]; then
        record "full_target" "true" "passed=$ft_passed (baseline=$full_base) failed=$ft_failed errors=$ft_errors; log: $ft_log"
      else
        record "full_target" "false" "rc=$ft_rc passed=$ft_passed (baseline=${full_base:-?}) failed=$ft_failed errors=$ft_errors; log: $ft_log; tail: $ft_tail"
      fi

      # legacy
      local lg_log lg_rc lg_tail lg_par lg_passed lg_failed lg_errors
      lg_log="$OUTDIR/legacy.log"
      ( cd "$SDK" && PYTHONPATH=src uv run pytest tests/orchestrator/step02 tests/orchestrator/step05 tests/orchestrator/step06 tests/orchestrator/step07 tests/orchestrator/p34 tests/orchestrator/p35 -q -p no:cacheprovider ) > "$lg_log" 2>&1
      lg_rc=$?
      lg_tail=$(grep -avE '^[[:space:]]*$' "$lg_log" | tail -1)
      lg_par=$(parse_pytest_tail "$lg_tail")
      lg_passed=$(printf '%s' "$lg_par" | sed -n 's/.*passed=\([0-9]*\).*/\1/p')
      lg_failed=$(printf '%s' "$lg_par" | sed -n 's/.*failed=\([0-9]*\).*/\1/p')
      lg_errors=$(printf '%s' "$lg_par" | sed -n 's/.*errors=\([0-9]*\).*/\1/p')
      if [ "$lg_failed" -eq 0 ] && [ "$lg_errors" -eq 0 ] && [ -n "$legacy_base" ] && [ "$lg_passed" -ge "$legacy_base" ]; then
        record "legacy" "true" "passed=$lg_passed (baseline=$legacy_base) failed=$lg_failed errors=$lg_errors; log: $lg_log"
      else
        record "legacy" "false" "rc=$lg_rc passed=$lg_passed (baseline=${legacy_base:-?}) failed=$lg_failed errors=$lg_errors; log: $lg_log; tail: $lg_tail"
      fi
    fi
  fi

  # ---- 9. sentinel --------------------------------------------------------
  local sentinel_n
  sentinel_n=$( grep -rn "_new_mode" "$SDK"/src/agent_orchestrator 2>/dev/null | wc -l | tr -d ' ' )
  if [ -n "$MAX_SENTINEL" ] && [ "$sentinel_n" -gt "$MAX_SENTINEL" ]; then
    record "sentinel" "false" "sentinel count $sentinel_n exceeds --max-sentinel $MAX_SENTINEL"
  else
    record "sentinel" "true" "sentinel count=$sentinel_n${MAX_SENTINEL:+ (max=$MAX_SENTINEL)}"
  fi

  # ---- assemble JSON ------------------------------------------------------
  local HEAD_SHA
  HEAD_SHA=$(git -C "$SDK" rev-parse HEAD 2>/dev/null || echo "?")
  local ok_flag
  ok_flag=$(python3 - "$RESULTS_DIR" "$REPORT" "$SDK" "$BASE" "$HEAD_SHA" <<'PY'
import json, os, sys
results_dir, report_path, sdk, base, head = sys.argv[1:6]
items = []
for fn in sorted(os.listdir(results_dir), key=lambda x: int(x.split('.')[0]) if x.split('.')[0].isdigit() else 0):
    if not fn.endswith('.name'):
        continue
    n = fn[:-5]
    name = open(os.path.join(results_dir, n + '.name')).read()
    ok = open(os.path.join(results_dir, n + '.ok')).read().strip() == 'true'
    detail = open(os.path.join(results_dir, n + '.detail')).read()
    items.append({"name": name, "ok": ok, "detail": detail})
top_ok = all(i["ok"] for i in items)
report = {"sdk": sdk, "base": base, "head": head, "ok": top_ok, "items": items}
with open(report_path, "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("true" if top_ok else "false")
PY
)
  rm -rf "$RESULTS_DIR"

  echo "sdk_gate report: $REPORT"
  echo "sdk_gate: ok=$ok_flag (failed items: $(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(','.join(i['name'] for i in d['items'] if not i['ok']) or '-')" "$REPORT" 2>/dev/null))"

  if [ "$ok_flag" = "true" ]; then
    return 0
  else
    return 1
  fi
}

# Only run the gate body when executed directly (not when sourced by selftest_gate.sh).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
