import io, sys
p = r"C:/Users/24378/AppData/Roaming/deskpet/config.toml"
want = sys.argv[1] if len(sys.argv) > 1 else "on"
lines = io.open(p, encoding="utf-8").read().splitlines(keepends=True)
out, in_skills, has_skills = [], False, False
for l in lines:
    s = l.strip()
    if s.startswith("["):
        in_skills = (s == "[skills]")
        if in_skills:
            has_skills = True
    if in_skills and s.startswith("knowledge_enabled"):
        continue  # drop old
    out.append(l)
if want == "on":
    if has_skills:
        res = []
        for l in out:
            res.append(l)
            if l.strip() == "[skills]":
                res.append("knowledge_enabled = true\n")
        out = res
    else:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append("\n[skills]\nknowledge_enabled = true\n")
io.open(p, "w", encoding="utf-8", newline="").write("".join(out))
txt = io.open(p, encoding="utf-8").read()
print("RESULT want=%s present=%s has_skills=%s" % (want, "knowledge_enabled = true" in txt, has_skills))
