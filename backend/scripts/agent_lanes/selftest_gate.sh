#!/bin/bash
# selftest_gate.sh - offline self-test for sdk_gate.sh. No model is called.
# Builds throwaway git repos (under a private mktemp dir) that mimic the SDK,
# forces GATE_SKIP_PYTHON=1 so the python-dependent items are skipped, and checks
# the deterministic gate items. Prints "SELFTEST OK n/n" on success.
# Every git call uses `git -C <repo>` (never a bare `cd`), and `guard_repo`
# hard-aborts if a fake repo is not really inside the temp dir, so a bug here can
# never touch the host repository.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE="$SCRIPT_DIR/sdk_gate.sh"
[ -f "$GATE" ] || { echo "selftest_gate.sh: sdk_gate.sh not found next to this script" >&2; exit 1; }

# pull in parse_pytest_tail (sourcing must NOT run the gate body)
# shellcheck disable=SC1090
source "$GATE"

# slice_pipeline.sh must be source-safe too: with SLICE_PIPELINE_SOURCE_ONLY=1 it only defines functions.
PIPE="$SCRIPT_DIR/slice_pipeline.sh"
[ -f "$PIPE" ] || { echo "selftest_gate.sh: slice_pipeline.sh not found next to this script" >&2; exit 1; }
# probe in a subshell first, so an unsafe (non-source-safe) script cannot kill the self-test run
PIPE_PROBE=$(SLICE_PIPELINE_SOURCE_ONLY=1 bash -c 'source "$1" >/dev/null 2>&1 && declare -f grep_verdict >/dev/null && declare -f verdict_kind >/dev/null && declare -f round_action >/dev/null && echo SOURCE_OK' selftest "$PIPE" 2>&1 || true)
PIPE_SOURCED=0
case "$PIPE_PROBE" in
  *SOURCE_OK*) PIPE_SOURCED=1 ;;
esac
if [ "$PIPE_SOURCED" -eq 1 ]; then
  # shellcheck disable=SC1090
  SLICE_PIPELINE_SOURCE_ONLY=1 source "$PIPE"
else
  # stubs so the verdict/round tests below fail loudly instead of aborting the whole self-test
  grep_verdict() { return 1; }
  verdict_kind() { echo ""; }
  round_action() { echo "not-source-safe"; }
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/selftest_gate.XXXXXX")"
if [ -z "$TMP" ] || [ ! -d "$TMP" ]; then
  echo "selftest_gate.sh: could not create a temp dir; aborting" >&2
  exit 1
fi
TMP_REAL="$(cd "$TMP" && pwd -P)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

PASS=0
TOTAL=0
ok()  { TOTAL=$((TOTAL+1)); PASS=$((PASS+1)); printf 'ok   - %s\n' "$1"; }
bad() { TOTAL=$((TOTAL+1)); printf 'FAIL - %s\n' "$1"; }

# guard_repo <repo> - hard-abort unless <repo> is a git repo rooted inside $TMP.
guard_repo() {
  local repo="$1" top
  if [ -z "$repo" ] || [ ! -d "$repo" ]; then
    echo "selftest_gate.sh: FATAL: fake repo path missing: '$repo'" >&2
    exit 1
  fi
  top="$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -z "$top" ]; then
    echo "selftest_gate.sh: FATAL: '$repo' is not a git repo" >&2
    exit 1
  fi
  case "$top" in
    "$TMP_REAL"/*) : ;;
    *) echo "selftest_gate.sh: FATAL: '$repo' toplevel '$top' is outside $TMP_REAL; refusing to touch it" >&2
       exit 1 ;;
  esac
}

# make_repo <name>: create a fresh fake SDK inside $TMP, echo its path.
make_repo() {
  local name="$1"
  local repo="$TMP/$name"
  mkdir -p "$repo/src/agent_orchestrator/contracts" "$repo/plans/llm-native-htn/H0"
  git -C "$repo" init -q
  git -C "$repo" config user.email "selftest@example.com"
  git -C "$repo" config user.name "selftest"
  printf '# contract a\n' > "$repo/src/agent_orchestrator/contracts/a.py"
  printf '# pkg\n' > "$repo/src/agent_orchestrator/__init__.py"
  printf '{"full_target":{"passed":10},"legacy":{"passed":5}}\n' > "$repo/plans/llm-native-htn/H0/test-results.json"
  git -C "$repo" add -A
  git -C "$repo" commit -q -m "base"
  guard_repo "$repo"
  printf '%s\n' "$repo"
}

# report_item_ok <report.json> <item-name> -> echoes true/false/missing
report_item_ok() {
  python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(next((str(i['ok']).lower() for i in d['items'] if i['name']==sys.argv[2]),'missing'))" "$1" "$2"
}

# report_top_ok <report.json> -> true/false
report_top_ok() {
  python3 -c "import json,sys;print('true' if json.load(open(sys.argv[1]))['ok'] else 'false')" "$1"
}

# ---- Case 1: clean + in-allowlist change -> green --------------------------
R1=$(make_repo case1)
B1=$(git -C "$R1" rev-parse HEAD)
AL1="$TMP/allow1.txt"; printf 'src/agent_orchestrator/*.py\n' > "$AL1"
printf '# new\n' > "$R1/src/agent_orchestrator/newmod.py"
git -C "$R1" add -A && git -C "$R1" commit -q -m "add newmod"
GATE_SKIP_PYTHON=1 "$GATE" "$R1" "$B1" --allow "$AL1" --out "$TMP/c1.json" > "$TMP/c1.out" 2>&1
rc=$?
[ "$rc" -eq 0 ] && ok "case1 clean+allowed: exit=0" || bad "case1 clean+allowed: exit=$rc; $(cat "$TMP/c1.out")"
[ "$(report_top_ok "$TMP/c1.json")" = "true" ] && ok "case1 top-level ok=true" || bad "case1 top-level not ok"
[ "$(report_item_ok "$TMP/c1.json" clean)" = "true" ] && ok "case1 clean ok" || bad "case1 clean not ok"
[ "$(report_item_ok "$TMP/c1.json" allowlist)" = "true" ] && ok "case1 allowlist ok" || bad "case1 allowlist not ok"

# ---- Case 2: modify existing contracts file -> contracts_frozen red ---------
R2=$(make_repo case2)
B2=$(git -C "$R2" rev-parse HEAD)
printf '# contract a MODIFIED\n' > "$R2/src/agent_orchestrator/contracts/a.py"
git -C "$R2" add -A && git -C "$R2" commit -q -m "modify contract"
GATE_SKIP_PYTHON=1 "$GATE" "$R2" "$B2" --out "$TMP/c2.json" > "$TMP/c2.out" 2>&1
rc=$?
[ "$rc" -eq 1 ] && ok "case2 modified contract: exit=1" || bad "case2 modified contract: exit=$rc"
[ "$(report_item_ok "$TMP/c2.json" contracts_frozen)" = "false" ] && ok "case2 contracts_frozen red" || bad "case2 contracts_frozen not red"

# ---- Case 3: file outside allowlist -> allowlist red ------------------------
R3=$(make_repo case3)
B3=$(git -C "$R3" rev-parse HEAD)
AL3="$TMP/allow3.txt"; printf 'src/agent_orchestrator/*.py\n' > "$AL3"
mkdir -p "$R3/docs"; printf 'note\n' > "$R3/docs/notes.md"
git -C "$R3" add -A && git -C "$R3" commit -q -m "add docs"
GATE_SKIP_PYTHON=1 "$GATE" "$R3" "$B3" --allow "$AL3" --out "$TMP/c3.json" > "$TMP/c3.out" 2>&1
rc=$?
[ "$rc" -eq 1 ] && ok "case3 outside allowlist: exit=1" || bad "case3 outside allowlist: exit=$rc"
[ "$(report_item_ok "$TMP/c3.json" allowlist)" = "false" ] && ok "case3 allowlist red" || bad "case3 allowlist not red"

# ---- Case 4a: added line with sk- -> no_secrets red -------------------------
R4=$(make_repo case4)
B4=$(git -C "$R4" rev-parse HEAD)
printf 'token = "sk-abcdefghij1234"\n' > "$R4/leak.py"
git -C "$R4" add -A && git -C "$R4" commit -q -m "leak"
GATE_SKIP_PYTHON=1 "$GATE" "$R4" "$B4" --out "$TMP/c4.json" > "$TMP/c4.out" 2>&1
rc=$?
[ "$rc" -eq 1 ] && ok "case4 sk-: exit=1" || bad "case4 sk-: exit=$rc"
[ "$(report_item_ok "$TMP/c4.json" no_secrets)" = "false" ] && ok "case4 no_secrets red (sk-)" || bad "case4 no_secrets not red (sk-)"

# ---- Case 4b: task-abcdefghij1234 must NOT trigger no_secrets ---------------
R5=$(make_repo case5)
B5=$(git -C "$R5" rev-parse HEAD)
printf 'task = "task-abcdefghij1234"\n' > "$R5/taskid.py"
git -C "$R5" add -A && git -C "$R5" commit -q -m "taskid"
GATE_SKIP_PYTHON=1 "$GATE" "$R5" "$B5" --out "$TMP/c5.json" > "$TMP/c5.out" 2>&1
rc=$?
[ "$rc" -eq 0 ] && ok "case4b task-: exit=0" || bad "case4b task-: exit=$rc; $(cat "$TMP/c5.out")"
[ "$(report_item_ok "$TMP/c5.json" no_secrets)" = "true" ] && ok "case4b no_secrets green (task-)" || bad "case4b no_secrets not green (task-)"

# ---- Case 5: dirty working tree -> clean red -------------------------------
R6=$(make_repo case6)
B6=$(git -C "$R6" rev-parse HEAD)
printf 'untracked\n' > "$R6/dirty.txt"
GATE_SKIP_PYTHON=1 "$GATE" "$R6" "$B6" --out "$TMP/c6.json" > "$TMP/c6.out" 2>&1
rc=$?
[ "$rc" -eq 1 ] && ok "case5 dirty: exit=1" || bad "case5 dirty: exit=$rc"
[ "$(report_item_ok "$TMP/c6.json" clean)" = "false" ] && ok "case5 clean red" || bad "case5 clean not red"

# ---- Case 6: GATE_SKIP_PYTHON=1 records items 5-8 as skipped ----------------
R7=$(make_repo case7)
B7=$(git -C "$R7" rev-parse HEAD)
printf '# new\n' > "$R7/src/agent_orchestrator/newmod.py"
git -C "$R7" add -A && git -C "$R7" commit -q -m "newmod"
GATE_SKIP_PYTHON=1 "$GATE" "$R7" "$B7" --full --out "$TMP/c7.json" > "$TMP/c7.out" 2>&1
rc=$?
[ "$rc" -eq 0 ] && ok "case6 skip-python: exit=0" || bad "case6 skip-python: exit=$rc; $(cat "$TMP/c7.out")"
for it in ruff import_origin targeted full_target legacy; do
  det=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(next((i['detail'] for i in d['items'] if i['name']==sys.argv[2]),''))" "$TMP/c7.json" "$it")
  case "$det" in
    *skipped*) ok "case6 $it skipped ($det)" ;;
    *) bad "case6 $it not marked skipped: $det" ;;
  esac
done

# ---- parser unit tests (three tails) ---------------------------------------
p=$(parse_pytest_tail "12 passed, 2 skipped in 7.9s")
[ "$p" = "passed=12 failed=0 errors=0" ] && ok "parse '[12 passed, 2 skipped]' -> $p" || bad "parse all-passed: $p"

p=$(parse_pytest_tail "1 failed, 11 passed")
[ "$p" = "passed=11 failed=1 errors=0" ] && ok "parse '[1 failed, 11 passed]' -> $p" || bad "parse with-failed: $p"

p=$(parse_pytest_tail "no tests ran in 0.01s")
[ "$p" = "passed=0 failed=0 errors=0" ] && ok "parse '[no tests ran]' -> $p" || bad "parse no-tests: $p"

# ---- Case 7: sentinel counts only .py source files -------------------------
R8=$(make_repo case8)
B8=$(git -C "$R8" rev-parse HEAD)
# a real source file with the marker, and a binary-looking .pyc that must be ignored
printf 'x = "_new_mode"\n' > "$R8/src/agent_orchestrator/sentinel_source.py"
printf 'binary:_new_mode:payload\n' > "$R8/src/agent_orchestrator/sentinel_fake.pyc"
git -C "$R8" add -A && git -C "$R8" commit -q -m "sentinel files"
GATE_SKIP_PYTHON=1 "$GATE" "$R8" "$B8" --out "$TMP/c8.json" > "$TMP/c8.out" 2>&1
rc=$?
sent=`python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(next((i['detail'] for i in d['items'] if i['name']=='sentinel'),''))" "$TMP/c8.json"`
case "$sent" in
  *count=1*) ok "case7 sentinel counts .py only ($sent)" ;;
  *) bad "case7 sentinel counted non-source files: $sent; rc=$rc" ;;
esac

# ---- verdict extraction unit tests (source-only helpers from slice_pipeline.sh) ----
if [ "$PIPE_SOURCED" -eq 1 ]; then
  ok "slice_pipeline.sh is source-safe (SLICE_PIPELINE_SOURCE_ONLY=1)"
else
  bad "slice_pipeline.sh is not source-safe: $PIPE_PROBE"
fi
VTD="$TMP/verdict-out"
mkdir -p "$VTD"
# three verdict kinds; last.txt is the primary source, the summary is a decoy with a different verdict
vk_case() { # vk_case <name> <last.txt verdict> <summary verdict> <expected>
  local name="$1" last="$2" summary="$3" want="$4" got line
  printf '%s\n' "VERDICT: $last" > "$VTD/last.txt"
  printf 'some text\nVERDICT: %s\n' "$summary" > "$VTD/summary.txt"
  line=$(grep_verdict "$VTD/summary.txt" "$VTD" || true)
  got=$(verdict_kind "$line")
  if [ "$got" = "$want" ]; then ok "verdict $name -> $got (line: ${line:-<none>})"
  else bad "verdict $name -> '$got' (line: '${line:-<none>}'; want: $want)"; fi
}
vk_case "可合"      "可合"      "不可合"    "可合"
vk_case "修后可合"  "修后可合"  "可合"      "修后可合"
vk_case "不可合"    "不可合"    "修后可合"  "不可合"
# summary is the fallback when last.txt is missing
printf 'text\nVERDICT: 修后可合\n' > "$VTD/summary.txt"
line=$(grep_verdict "$VTD/summary.txt" "$TMP/verdict-no-last" || true)
[ "$(verdict_kind "$line")" = "修后可合" ] && ok "verdict falls back to the summary" || bad "verdict fallback broken: '$line'"
# the last VERDICT line in the file wins (guards the tail -1 lookup)
printf 'VERDICT: 可合\ntext\nVERDICT: 修后可合\n' > "$VTD/summary.txt"
line=$(grep_verdict "$VTD/summary.txt" "$TMP/verdict-none" || true)
[ "$(verdict_kind "$line")" = "修后可合" ] && ok "verdict takes the last line" || bad "verdict last-line broken: '$line'"
# no verdict anywhere -> no line
printf 'no verdict here\n' > "$VTD/summary.txt"
if line=$(grep_verdict "$VTD/summary.txt" "$TMP/verdict-none"); then bad "verdict found when none exists: $line"; else ok "no verdict -> red (empty)"; fi

# ---- round decision unit tests --------------------------------------------
rcase() { # rcase <kind> <round> <max> <want>
  local got
  got=$(round_action "$1" "$2" "$3")
  if [ "$got" = "$4" ]; then ok "round $1 round=$2/$3 -> $got"
  else bad "round $1 round=$2/$3 -> '$got' (want $4)"; fi
}
rcase "可合"     1 2 "green"
rcase "修后可合" 1 2 "disposition"
rcase "修后可合" 2 2 "green-with-gaps"
rcase "不可合"   1 2 "red-unmergeable"
rcase ""         1 2 "red-no-verdict"

echo
if [ "$PASS" -eq "$TOTAL" ]; then
  echo "SELFTEST OK $PASS/$TOTAL"
  exit 0
else
  echo "SELFTEST FAILED $PASS/$TOTAL"
  exit 1
fi
