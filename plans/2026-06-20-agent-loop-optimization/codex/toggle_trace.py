import io, sys
p = r"C:/Users/24378/AppData/Roaming/deskpet/config.toml"
want = sys.argv[1] if len(sys.argv) > 1 else "on"
lines = io.open(p, encoding="utf-8").read().splitlines(keepends=True)
# 先移除已有的 [agent] 段内 iteration_trace_enabled 行
out, in_agent = [], False
for l in lines:
    s = l.strip()
    if s.startswith("["):
        in_agent = (s == "[agent]")
    if in_agent and s.startswith("iteration_trace_enabled"):
        continue  # drop old
    out.append(l)
if want == "on":
    res = []
    for l in out:
        res.append(l)
        if l.strip() == "[agent]":
            res.append("iteration_trace_enabled = true\n")
    out = res
io.open(p, "w", encoding="utf-8", newline="").write("".join(out))
# verify
txt = io.open(p, encoding="utf-8").read()
print("RESULT want=%s present=%s" % (want, "iteration_trace_enabled = true" in txt))
