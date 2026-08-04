# 解码 tauri-dev.log (UTF-16LE，被 rust pipe 硬折行) 并 grep 关键事件。
# 用法: python loggrep.py [<正则>]
import re, sys, pathlib

LOG = pathlib.Path(__file__).parent / "tauri-dev.log"
pat = sys.argv[1] if len(sys.argv) > 1 else None

raw = LOG.read_bytes()
# 先按 utf-16-le 解，失败再退 utf-8
for enc in ("utf-16-le", "utf-8", "latin-1"):
    try:
        text = raw.decode(enc, errors="replace")
        break
    except Exception:
        continue

# 重组逻辑行：日志行以日志级别/时间戳开头；这里简单按换行拆 + 去 NUL
lines = [ln.replace("\x00", "").rstrip("\r") for ln in text.split("\n")]
lines = [ln for ln in lines if ln.strip()]

if pat:
    rx = re.compile(pat, re.IGNORECASE)
    hits = [ln for ln in lines if rx.search(ln)]
    print(f"=== {len(hits)} hits for /{pat}/ (total {len(lines)} lines) ===")
    for ln in hits[-60:]:
        print(ln[:400])
else:
    print(f"=== total {len(lines)} lines; tail 40 ===")
    for ln in lines[-40:]:
        print(ln[:300])
