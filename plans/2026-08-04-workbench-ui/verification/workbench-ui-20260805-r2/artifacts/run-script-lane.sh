#!/usr/bin/env bash
# script lane（r2）：S09/S11/S12 全场景 + S02/S04/S10 script 半边，oracle 原文判定
set -uo pipefail
cd /Users/denny/projects/simple_harness
RD=plans/2026-08-04-workbench-ui/verification/workbench-ui-20260805-r2
GATE=/Users/denny/.claude/skills/plan-test/scripts/plan_test_gate.py
A=$RD/artifacts
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

record() { # $1 scenario $2 outfile $3 verdict $4 desc $5 t0 $6 t1
  python3 $GATE record-timing --run-dir $RD --phase script-lane --task "$1" \
    --activity-class automated_test --command "$4" --declared-start "$5" --declared-end "$6" >/dev/null
  python3 $GATE attach-evidence --run-dir $RD --path "artifacts/$2" --kind primary --scenario "$1" >/dev/null
  python3 $GATE record-run --run-dir $RD --scenario "$1" --kind root --result "$3" \
    --lane script --driver ai --command "$4" --engine-terminal script --business-terminal "$3" >/dev/null
  echo "== $1 -> $3"
}

# ── S09（TC-WB-09 步骤 1-7 原文）──
t0=$(now)
python3 - > $A/S09-scan.txt 2>&1 << 'PY'
import subprocess, re
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True)
ok = True
def is_comment(line):
    m = re.match(r"^[^:]+:\d*:?\s*(.*)$", line)
    body = (m.group(1) if m else line).strip()
    return body.startswith(("//","/*","*","#","{/*")) or "SPDX" in body
for i, cmd in enumerate([
  'grep -riE "petcanvas|pet-anim|pet-engine" tauri-app/src',
  'grep -riE "petcharacter|pettransform|petmodels|petstatemachine" tauri-app/src']):
    hits = [l for l in sh(cmd).stdout.splitlines() if l.strip()]
    bad = [l for l in hits if not is_comment(l)]
    print(f"step{i+1}: hits={len(hits)} non-comment={len(bad)}")
    for l in hits: print(("  BAD: " if not is_comment(l) else "  (comment-ok): ") + l[:140])
    if bad: ok = False
d = sh('for d in tauri-app/src/pet-anim tauri-app/src/pet-engine tauri-app/src/pet-state tauri-app/public/assets/pet; do [ -e "$d" ] && echo "EXISTS: $d"; done; true').stdout
f = sh('for f in tauri-app/src/components/PetCanvas.tsx tauri-app/src/components/petCharacter.ts tauri-app/src/components/petTransform.ts tauri-app/src-tauri/src/click_through.rs; do [ -e "$f" ] && echo "EXISTS: $f"; done; true').stdout
print("step3:", repr(d)); print("step4:", repr(f))
if d.strip() or f.strip(): ok = False
ct = sh('grep -rn "click_through" tauri-app/src-tauri/src').stdout
print("step5 hits:", len(ct.splitlines()))
if ct.strip(): ok = False
pm = sh('grep -rniE "petmodels" tauri-app/src').stdout
print("step6 hits:", len(pm.splitlines()))
if pm.strip(): ok = False
t = sh("cd tauri-app && npx tsc --noEmit")
print("step7 tsc exit:", t.returncode)
if t.returncode != 0: ok = False
print("VERDICT_PASS" if ok else "VERDICT_FAIL")
PY
t1=$(now); V=fail; grep -q VERDICT_PASS $A/S09-scan.txt && V=pass
record WBUI-S09-pet-removal S09-scan.txt $V "TC-WB-09 步骤1-7 oracle 原文" "$t0" "$t1"

# ── S11（TC-WB-11 修正后步骤 1(a)(b) + 1b + 2 + 3）──
t0=$(now)
{
A1=$(grep -rnE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' tauri-app/src/views tauri-app/src/components/WorkbenchShell.tsx tauri-app/src/components/Sidebar.tsx tauri-app/src/components/SessionList.tsx tauri-app/src/components/ui.tsx 2>/dev/null | grep -vE '\.test\.'; true)
echo "1a-hits:"; echo "$A1"; N1=$(echo -n "$A1" | grep -c . || true)
B1=$(git diff 644ab16..HEAD -- tauri-app/src/components/SettingsPanel.tsx tauri-app/src/components/CapabilityCenterPanel.tsx tauri-app/src/components/SkillStorePanel.tsx | grep -E "^\+" | grep -vE "^\+\+\+" | grep -E '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()'; true)
echo "1b-diff-hits:"; echo "$B1"; N2=$(echo -n "$B1" | grep -c . || true)
H1=$(find tauri-app -name "*.html" -not -path "*/node_modules/*" -not -path "*/dist/*" -not -path "*/target/*" -exec grep -lnE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' {} + 2>/dev/null; true)
echo "html-hits:"; echo "$H1"; N3=$(echo -n "$H1" | grep -c . || true)
echo "counts: new=$N1 diff=$N2 html=$N3"
[ "$N1" = "0" ] && [ "$N2" = "0" ] && [ "$N3" = "0" ] && echo VERDICT_PASS || echo VERDICT_FAIL
} > $A/S11-theme.txt 2>&1
t1=$(now); V=fail; grep -q VERDICT_PASS $A/S11-theme.txt && V=pass
record WBUI-S11-theme-vars S11-theme.txt $V "TC-WB-11 修正后主判据(a)(b)+1b HTML+步骤2/3" "$t0" "$t1"

# ── S12 ──
t0=$(now)
{
OK=1
cd tauri-app
npx tsc --noEmit && echo TSC_OK || OK=0
VOUT=$(npx vitest run 2>&1 | grep -E "Test Files|Tests "); echo "$VOUT"
echo "$VOUT" | grep -q "75 passed" || OK=0
npx vitest run --coverage >/dev/null 2>&1 && echo COVERAGE_OK || OK=0
npm run build >/dev/null 2>&1 && echo BUILD_OK || OK=0
C=$(cd src-tauri && cargo test --lib 2>&1 | grep "test result"); echo "$C"
echo "$C" | grep -q " 0 failed" || OK=0
E=$(cd src-tauri && cargo check 2>&1 | grep -c "^error"); echo "cargo-errors=$E"; [ "$E" = "0" ] || OK=0
cd ../backend
P=$(timeout 300 env PYTHONPATH=/Users/denny/projects/simple_harness .venv/bin/python -m pytest tests/companion/ -q 2>&1 | tail -1)
echo "companion: $P（基线 9F/641P 为本机既有红，见 baseline.md）"
echo "$P" | grep -q "9 failed, 641 passed" || OK=0
cd ..
[ $OK = 1 ] && echo VERDICT_PASS || echo VERDICT_FAIL
} > $A/S12-build.txt 2>&1
t1=$(now); V=fail; grep -q VERDICT_PASS $A/S12-build.txt && V=pass
record WBUI-S12-build-green S12-build.txt $V "tsc+vitest+coverage+build+cargo+companion(baseline-equal)" "$t0" "$t1"

# ── S02 script 半边（TC-WB-02 步骤 4/5/6 原文）──
t0=$(now)
{
S4=$(grep -n "message-panel" tauri-app/src-tauri/tauri.conf.json tauri-app/src-tauri/capabilities/default.json; true)
echo "step4:"; echo "$S4"; N4=$(echo -n "$S4" | grep -c . || true)
S5=$(grep -rniE "open_message_panel|close_message_panel|dock_message_panel|toggle_message_panel" tauri-app/src tauri-app/src-tauri/src; true)
echo "step5:"; echo "$S5"; N5=$(echo -n "$S5" | grep -c . || true)
S6=$(grep -rn "message-panel\|message_panel" tauri-app/src tauri-app/src-tauri/src; true)
echo "step6 all hits:"; echo "$S6"
BAD6=$(echo "$S6" | grep -v "controlWs.ts" | grep -viE "注释|//|/\*| \* |#" | grep -c . || true)
echo "step6 disallowed=$BAD6（允许：controlWs sid 常量与注释）"
[ "$N4" = "0" ] && [ "$N5" = "0" ] && [ "$BAD6" = "0" ] && echo VERDICT_PASS || echo VERDICT_FAIL
} > $A/S02-script.txt 2>&1
t1=$(now); V=fail; grep -q VERDICT_PASS $A/S02-script.txt && V=pass
record WBUI-S02-single-window S02-script.txt $V "TC-WB-02 步骤4/5/6 oracle 原文（script 半边）" "$t0" "$t1"

# ── S04 script 半边 ──
t0=$(now)
{
cd backend
S=$(timeout 300 env PYTHONPATH=/Users/denny/projects/simple_harness .venv/bin/python -m pytest tests/companion/test_window_control_credentials.py tests/companion/test_store_schema.py tests/companion/test_store_transactions.py -q 2>&1 | tail -1)
echo "credential-chain: $S"
echo "$S" | grep -qE "passed" && ! echo "$S" | grep -q "failed" && SUB=1 || SUB=0
F=$(timeout 300 env PYTHONPATH=/Users/denny/projects/simple_harness .venv/bin/python -m pytest tests/companion/ -q 2>&1 | tail -1)
echo "full-suite: $F（基线等值判定，9 红既有）"
echo "$F" | grep -q "9 failed, 641 passed" && FULL=1 || FULL=0
cd ..
[ ${SUB:-0} = 1 ] && [ ${FULL:-0} = 1 ] && echo VERDICT_PASS || echo VERDICT_FAIL
} > $A/S04-script.txt 2>&1
t1=$(now); V=fail; grep -q VERDICT_PASS $A/S04-script.txt && V=pass
record WBUI-S04-chat-companion S04-script.txt $V "companion 凭据链 pytest（script 半边 a 层）" "$t0" "$t1"

# ── S10 script 半边 ──
t0=$(now)
{
R=$(cd tauri-app/src-tauri && cargo test --lib window_geometry 2>&1 | grep "test result"); echo "$R"
echo "$R" | grep -q " 0 failed" && echo VERDICT_PASS || echo VERDICT_FAIL
} > $A/S10-geometry.txt 2>&1
t1=$(now); V=fail; grep -q VERDICT_PASS $A/S10-geometry.txt && V=pass
record WBUI-S10-geometry-memory S10-geometry.txt $V "window_geometry 单测（script 半边）" "$t0" "$t1"

echo "--- r2 script lane 完毕 ---"
