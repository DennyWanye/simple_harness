#!/usr/bin/env bash
# r5 script lane：六项一次跑齐（BC-01/BC-02 修正后 oracle）
set -uo pipefail
cd /Users/denny/projects/simple_harness
RD=plans/2026-08-04-workbench-ui/verification/workbench-ui-20260808-r8
GATE=/Users/denny/.claude/skills/plan-test/scripts/plan_test_gate.py
A=$RD/artifacts
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

rec() { # $1 scenario $2 outfile $3 desc $4 t0 $5 t1
  local V=fail; grep -q VERDICT_PASS "$A/$2" && V=pass
  python3 $GATE record-timing --run-dir $RD --phase script-lane --task "$1" --activity-class automated_test --command "$3" --declared-start "$4" --declared-end "$5" >/dev/null
  python3 $GATE attach-evidence --run-dir $RD --path "artifacts/$2" --kind primary --scenario "$1" --replace >/dev/null
  python3 $GATE record-run --run-dir $RD --scenario "$1" --kind root --result $V --lane script --driver ai --command "$3" --engine-terminal script --business-terminal $V >/dev/null
  echo "== $1 -> $V"
}

# S09
t0=$(now)
python3 - > "$A/S09-scan.txt" 2>&1 << 'PY'
import subprocess, re
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True)
ok = True
def is_comment(line):
    m = re.match(r"^[^:]+:\d*:?\s*(.*)$", line)
    body = (m.group(1) if m else line).strip()
    return body.startswith(("//","/*","*","#","{/*")) or "SPDX" in body
for cmd in ['grep -riE "petcanvas|pet-anim|pet-engine" tauri-app/src',
            'grep -riE "petcharacter|pettransform|petmodels|petstatemachine" tauri-app/src']:
    hits = [l for l in sh(cmd).stdout.splitlines() if l.strip()]
    bad = [l for l in hits if not is_comment(l)]
    print(cmd, "->", len(hits), "hits,", len(bad), "non-comment")
    for l in bad: print("BAD:", l)
    if bad: ok = False
probes = ['for d in tauri-app/src/pet-anim tauri-app/src/pet-engine tauri-app/src/pet-state tauri-app/public/assets/pet; do [ -e "$d" ] && echo EXISTS; done; true',
          'grep -rn "click_through" tauri-app/src-tauri/src || true',
          'grep -rniE "petmodels" tauri-app/src || true']
for probe in probes:
    o = sh(probe).stdout.strip()
    print(probe[:50], "->", repr(o[:100]))
    if o: ok = False
t = sh("cd tauri-app && npx tsc --noEmit")
print("tsc:", t.returncode)
if t.returncode != 0: ok = False
print("VERDICT_PASS" if ok else "VERDICT_FAIL")
PY
t1=$(now); rec WBUI-S09-pet-removal S09-scan.txt "TC-WB-09 步骤1-7" "$t0" "$t1"

# S11
t0=$(now)
{
  A1=$(grep -rnE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' tauri-app/src/views tauri-app/src/components/WorkbenchShell.tsx tauri-app/src/components/Sidebar.tsx tauri-app/src/components/SessionList.tsx tauri-app/src/components/ui.tsx 2>/dev/null | grep -vE '\.test\.' || true)
  echo "1a:"; echo "$A1"; N1=$(printf '%s' "$A1" | grep -c . || true)
  B1=$(git diff 644ab16..HEAD -- tauri-app/src/components/SettingsPanel.tsx tauri-app/src/components/CapabilityCenterPanel.tsx tauri-app/src/components/SkillStorePanel.tsx | grep -E '^\+' | grep -vE '^\+\+\+' | grep -E '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' || true)
  echo "1b:"; echo "$B1"; N2=$(printf '%s' "$B1" | grep -c . || true)
  H1=$(find tauri-app -name '*.html' -not -path '*/node_modules/*' -not -path '*/dist/*' -not -path '*/target/*' -exec grep -lnE '(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()' {} + 2>/dev/null || true)
  echo "html:"; echo "$H1"; N3=$(printf '%s' "$H1" | grep -c . || true)
  echo "counts new=$N1 diff=$N2 html=$N3"
  if [ "$N1" = "0" ] && [ "$N2" = "0" ] && [ "$N3" = "0" ]; then echo VERDICT_PASS; else echo VERDICT_FAIL; fi
} > "$A/S11-theme.txt" 2>&1
t1=$(now); rec WBUI-S11-theme-vars S11-theme.txt "TC-WB-11 修正判据（BC-01）" "$t0" "$t1"

# S12
t0=$(now)
{
  OK=1
  cd tauri-app
  npx tsc --noEmit && echo TSC_OK || OK=0
  VOUT=$(npx vitest run 2>&1 | grep -E "Test Files|Tests " || true); echo "$VOUT"
  # 判据意图 = "整套 vitest 全绿且测试文件没少"。原先写死 "75 passed"，
  # cad272e 新增 src/relativeTime.test.ts 后变 76 → 误判 FAIL（脆常量，不是回归）。
  # 改为：① 不得出现 failed；② 文件数不得低于 76（防止测试被悄悄删掉）。
  echo "$VOUT" | grep -qi "failed" && OK=0
  VFILES=$(printf '%s' "$VOUT" | sed -n 's/.*Test Files *\([0-9][0-9]*\) passed.*/\1/p')
  echo "vitest_files=$VFILES (floor 76)"
  [ -n "$VFILES" ] && [ "$VFILES" -ge 76 ] || OK=0
  npx vitest run --coverage >/dev/null 2>&1 && echo COVERAGE_OK || OK=0
  npm run build >/dev/null 2>&1 && echo BUILD_OK || OK=0
  C=$(cd src-tauri && cargo test --lib 2>&1 | grep "test result" || true); echo "$C"
  echo "$C" | grep -q " 0 failed" || OK=0
  cd ../backend
  P=""
  # `tail -1` 会抓到进度点行（".sssss....."）而不是汇总行 —— 实测踩过。
  # 改为按内容抓最后一条含 passed/failed 的汇总行。
  for i in 1 2 3; do
    P=$(timeout 300 env PYTHONPATH=/Users/denny/projects/simple_harness .venv/bin/python -m pytest tests/companion/ -q 2>&1 \
          | grep -E "[0-9]+ (passed|failed)" | tail -1)
    echo "companion($i): $P"
    if printf '%s' "$P" | grep -q "failed"; then break; fi
  done
  printf '%s' "$P" | grep -q "9 failed, 643 passed" || OK=0
  cd ..
  if [ "$OK" = "1" ]; then echo VERDICT_PASS; else echo VERDICT_FAIL; fi
} > "$A/S12-build.txt" 2>&1
t1=$(now); rec WBUI-S12-build-green S12-build.txt "tsc+vitest+coverage+build+cargo+companion(基线等值)" "$t0" "$t1"

# S02 script 半边（BC-02 口径）
t0=$(now)
{
  S4=$(grep -n "message-panel" tauri-app/src-tauri/tauri.conf.json tauri-app/src-tauri/capabilities/default.json || true)
  N4=$(printf '%s' "$S4" | grep -c . || true)
  S5=$(grep -rniE "open_message_panel|close_message_panel|dock_message_panel|toggle_message_panel" tauri-app/src tauri-app/src-tauri/src || true)
  N5=$(printf '%s' "$S5" | grep -c . || true)
  echo "step4($N4):"; echo "$S4"; echo "step5($N5):"; echo "$S5"
  S6=$(grep -rn "message-panel\|message_panel" tauri-app/src tauri-app/src-tauri/src || true)
  echo "step6 hits:"; echo "$S6"
  BAD6=0
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in *controlWs.ts*) continue;; esac
    # BC-02：webview_permissions 拒绝断言豁免
    case "$line" in *webview_permissions.rs*assert*is_err*) continue;; esac
    body=$(printf '%s' "$line" | sed -E 's/^[^:]+:[0-9]+://; s/^[[:space:]]+//')
    case "$body" in "//"*|"/*"*|"*"*|"#"*|"{/*"*) continue;; esac
    echo "DISALLOWED: $line"; BAD6=$((BAD6+1))
  done << EOF6
$S6
EOF6
  echo "step6-disallowed=$BAD6"
  if [ "$N4" = "0" ] && [ "$N5" = "0" ] && [ "$BAD6" = "0" ]; then echo VERDICT_PASS; else echo VERDICT_FAIL; fi
} > "$A/S02-script.txt" 2>&1
t1=$(now); rec WBUI-S02-single-window S02-script.txt "TC-WB-02 步骤4/5/6（BC-02 口径，script 半边）" "$t0" "$t1"

# S04 script 半边（带重试）
t0=$(now)
{
  cd backend
  S=$(timeout 300 env PYTHONPATH=/Users/denny/projects/simple_harness .venv/bin/python -m pytest tests/companion/test_window_control_credentials.py tests/companion/test_store_schema.py tests/companion/test_store_transactions.py -q 2>&1 | tail -1)
  echo "credential-chain: $S"
  F=""
  for i in 1 2 3; do
    F=$(timeout 300 env PYTHONPATH=/Users/denny/projects/simple_harness .venv/bin/python -m pytest tests/companion/ -q 2>&1 | tail -1)
    echo "full($i): $F"
    if printf '%s' "$F" | grep -q "failed"; then break; fi
  done
  cd ..
  SUBOK=0; printf '%s' "$S" | grep -qE "^[0-9]+ passed" && SUBOK=1
  FULLOK=0; printf '%s' "$F" | grep -q "9 failed, 643 passed" && FULLOK=1
  echo "subok=$SUBOK fullok=$FULLOK"
  if [ "$SUBOK" = "1" ] && [ "$FULLOK" = "1" ]; then echo VERDICT_PASS; else echo VERDICT_FAIL; fi
} > "$A/S04-script.txt" 2>&1
t1=$(now); rec WBUI-S04-chat-companion S04-script.txt "companion 凭据链+全套件基线等值（script 半边）" "$t0" "$t1"

# S10 script 半边
t0=$(now)
{
  R=$(cd tauri-app/src-tauri && cargo test --lib window_geometry 2>&1 | grep "test result" || true); echo "$R"
  if echo "$R" | grep -q " 0 failed"; then echo VERDICT_PASS; else echo VERDICT_FAIL; fi
} > "$A/S10-geometry.txt" 2>&1
t1=$(now); rec WBUI-S10-geometry-memory S10-geometry.txt "window_geometry 单测（script 半边）" "$t0" "$t1"

echo "--- r5 script lane 完毕 ---"
