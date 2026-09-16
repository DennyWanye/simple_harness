from pathlib import Path
import json
base = Path(__file__).resolve().parent.parent
read = lambda name: json.loads((base / name).read_text(encoding="utf-8"))
r = read("requirements.json")["requirements"]
a = read("acceptance-scenarios.json")["scenarios"]
w = read("work-packages.json")["packages"]
s = read("sources.json")["sources"]
c = read("original-chapter-map.json")["chapters"]
rids = {x["id"] for x in r}; aids={x["id"] for x in a}; wids={x["id"] for x in w}; sids={x["id"] for x in s}
assert len(rids)==len(r)==60
assert len(aids)==len(a)==84
assert len(wids)==len(w)==7
assert {x["chapter"] for x in c} == set(range(1,32))
assert all(x["acceptance_status"]=="NOT_RUN" for x in r)
assert all(x["status"]=="NOT_RUN" for x in a)
for x in r:
 assert set(x["current_evidence"])<=sids, x["id"]
 assert set(x["acceptance_ids"])<=aids, x["id"]
 assert x["work_package"] in wids
for x in a:
 assert set(x["requirement_ids"])<=rids
 assert x["work_package"] in wids
for x in c: assert set(x["requirement_ids"])<=rids
assert set().union(*(set(x["requirement_ids"]) for x in c)) == rids
for x in w:
 assert set(x["depends_on"])<=wids
 assert set(x["requirements"])<=rids
 assert set(x["acceptance_ids"])<=aids
seen=set()
while len(seen)<len(w):
 ready=[x["id"] for x in w if x["id"] not in seen and set(x["depends_on"])<=seen]
 assert ready, "work package dependency cycle"
 seen.update(ready)
print(json.dumps({"artifact_structure":"valid","requirements":len(r),"scenarios":len(a),"original_chapters":len(c),"work_packages":len(w),"repository_tests_run":False},ensure_ascii=False,indent=2))
