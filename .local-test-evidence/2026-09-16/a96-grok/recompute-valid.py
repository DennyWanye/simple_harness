"""Re-derive valid_success in stored result.json files with the per-arm rule (S: clean; R: clean+selected; D/F: clean+mission).
Needed because the first smoke worker (S 530b157_1, R 530b157_1, S 0d8a4ee_1, R 0d8a4ee_1) was started before the rule fix.
Only the valid_success field changes; a 'valid_success_recomputed' marker is added. Idempotent."""
import json, sys
from pathlib import Path
OUT = Path(__file__).resolve().parent / "matrix-grok46-256k-v1"
changed = 0
for p in sorted((OUT / "episodes").glob("*/result.json")):
    r = json.loads(p.read_text())
    clean = ("error_type" not in r and "score_error_type" not in r and r.get("admission_denials") == []
             and r.get("unknown_usage_calls") == 0 and r.get("official_utility") is True)
    if r["arm"] == "S":
        v = clean
    elif r["arm"] == "R":
        v = clean and r.get("selected_candidate") in (1, 2)
    else:
        v = clean and r.get("mission_success") is True
    if r.get("valid_success") != v:
        r["valid_success_recomputed"] = {"from": r.get("valid_success"), "to": v}
        r["valid_success"] = v
        p.write_text(json.dumps(r, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        changed += 1
print("recomputed", changed)
