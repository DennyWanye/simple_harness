# 审计用：解码任意 UTF-16LE log + grep。用法: python audit_grep.py <logfile> [<正则>]
import re, sys, pathlib

logfile = sys.argv[1]
pat = sys.argv[2] if len(sys.argv) > 2 else None
LOG = pathlib.Path(__file__).parent / logfile

raw = LOG.read_bytes()
for enc in ("utf-16-le", "utf-8", "latin-1"):
    try:
        text = raw.decode(enc, errors="replace")
        break
    except Exception:
        continue

lines = [ln.replace("\x00", "").rstrip("\r") for ln in text.split("\n")]
lines = [ln for ln in lines if ln.strip()]

if pat:
    rx = re.compile(pat, re.IGNORECASE)
    hits = [ln for ln in lines if rx.search(ln)]
    print(f"=== {len(hits)} hits for /{pat}/ in {logfile} (total {len(lines)} lines) ===")
    for ln in hits[-80:]:
        print(ln[:500])
else:
    print(f"=== {logfile}: total {len(lines)} lines; tail 30 ===")
    for ln in lines[-30:]:
        print(ln[:300])
