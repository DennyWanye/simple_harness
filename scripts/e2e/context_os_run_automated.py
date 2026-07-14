#!/usr/bin/env python3
"""Run the immutable Context OS manifest with per-subcommand evidence."""
from __future__ import annotations
import argparse,datetime,json,re,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
FAILED_NODE = re.compile(r"^FAILED\s+([^\s]+)", re.MULTILINE)

def run_command(spec):
    command=list(spec["command"])
    if spec.get("backend_shard"):
        shard=spec["backend_shard"];lo,hi=shard.split("-") if "-" in shard else (shard,shard)
        files=[str(path.relative_to(ROOT)).replace("\\","/") for path in sorted((ROOT/"backend/tests").glob("test_*.py")) if lo <= path.stem.removeprefix("test_")[0].lower() <= hi]
        command += files + ["-q", "--tb=no"]
    started=time.time()
    try:
        proc=subprocess.run(command,cwd=ROOT,text=True,encoding="utf-8",errors="replace",capture_output=True,shell=False,timeout=int(spec.get("timeout_seconds",300)))
    except subprocess.TimeoutExpired as exc:
        stdout=(exc.stdout or "") if isinstance(exc.stdout,str) else (exc.stdout or b"").decode("utf-8","replace")
        stderr=(exc.stderr or "") if isinstance(exc.stderr,str) else (exc.stderr or b"").decode("utf-8","replace")
        return {"name":spec["name"],"scope":spec.get("scope",""),"command":command,"exit_code":124,"duration_seconds":round(time.time()-started,3),"passed":False,"expected_redlines":sorted(spec.get("expected_redlines",[])),"observed_redlines":["command_timeout"],"unexpected_redlines":["command_timeout"],"missing_expected_redlines":[],"stdout":stdout[-30000:],"stderr":stderr[-30000:]}
    stdout=proc.stdout or "";stderr=proc.stderr or "";combined=stdout+"\n"+stderr
    observed=sorted(set(FAILED_NODE.findall(combined)))
    expected=sorted(spec.get("expected_redlines",[]))
    unexpected=sorted(set(observed)-set(expected));missing=sorted(set(expected)-set(observed))
    passed=(not unexpected and not missing and (proc.returncode==0 or bool(expected)))
    return {"name":spec["name"],"scope":spec.get("scope",""),"command":command,"exit_code":proc.returncode,"duration_seconds":round(time.time()-started,3),"passed":passed,"expected_redlines":expected,"observed_redlines":observed,"unexpected_redlines":unexpected,"missing_expected_redlines":missing,"stdout":stdout[-30000:],"stderr":stderr[-30000:]}

def main():
    p=argparse.ArgumentParser();p.add_argument("--manifest",type=Path,default=ROOT/"scripts/e2e/fixtures/context-os-automation-manifest.json");p.add_argument("--out",type=Path,default=ROOT/"plans/2026-07-13-context-os-v1/test-results/automation");p.add_argument("--id",action="append");p.add_argument("--subcommand",action="append");p.add_argument("--merge-existing",action="store_true");a=p.parse_args()
    manifest=json.loads(a.manifest.read_text(encoding="utf-8"));a.out.mkdir(parents=True,exist_ok=True);failed=0
    for case in manifest["cases"]:
        if a.id and case["id"] not in a.id:continue
        started=time.time();specs=case.get("commands") or [{"name":"main","command":case["command"],"expected_redlines":case.get("expected_redlines",[])}]
        if a.subcommand:specs=[spec for spec in specs if spec["name"] in a.subcommand]
        if not specs:continue
        commands=[run_command(spec) for spec in specs]
        existing_path=a.out/f"{case['id']}.json"
        if a.merge_existing and existing_path.exists():
            old=json.loads(existing_path.read_text(encoding="utf-8"));replaced={item["name"] for item in commands}
            commands=[item for item in old.get("subcommands",[]) if item["name"] not in replaced]+commands
        passed=all(item["passed"] for item in commands)
        result={"id":case["id"],"assertions":case["assertions"],"passed":passed,"duration_seconds":round(sum(float(item.get("duration_seconds",0)) for item in commands),3),"subcommands":commands,"expected_redlines":sorted({x for item in commands for x in item["expected_redlines"]}),"observed_redlines":sorted({x for item in commands for x in item["observed_redlines"]}),"unexpected_redlines":sorted({x for item in commands for x in item["unexpected_redlines"]}),"timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat()}
        (a.out/f"{case['id']}.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");failed+=not passed
    all_results=[]
    for path in a.out.glob("AT-CTX-*.json"):
        try:all_results.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:pass
    summary={"selected":len(all_results),"failed":sum(not item.get("passed",False) for item in all_results)};(a.out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8");raise SystemExit(1 if failed else 0)
if __name__=="__main__":main()
