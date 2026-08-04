"""真 relay 图像生成探针：调 generate_images 一个简单 prompt，报是否拿到真 PNG。
不打印任何密钥。用 dev venv python 跑，需设 DESKPET_USER_DATA_DIR。"""
import os
import sys
import time
from pathlib import Path

# 让 backend 包可导入
BACKEND = Path(r"G:\projects\deskpet\backend")
sys.path.insert(0, str(BACKEND))

os.environ.setdefault(
    "DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata"
)

from deskpet.tools.image_tools import _resolve_endpoint, generate_images  # noqa: E402

base_url, api_key = _resolve_endpoint()
print(f"[endpoint] base_url={base_url!r}  api_key_present={bool(api_key)}")
if not base_url:
    print("RESULT: NO_ENDPOINT — llm_runtime.json/config 没有 base_url")
    sys.exit(2)

t0 = time.time()
results = generate_images(["a simple flat teal circle on white background"])
dt = time.time() - t0
r = results[0]
print(f"[timing] {dt:.1f}s")
if r.get("path"):
    p = Path(r["path"])
    size = p.stat().st_size if p.is_file() else -1
    print(f"RESULT: OK — path={p}  exists={p.is_file()}  bytes={size}")
    sys.exit(0)
else:
    print(f"RESULT: FAIL — error={r.get('error')!r}")
    sys.exit(1)
