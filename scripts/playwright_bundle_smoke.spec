from pathlib import Path
import sys

repo_root = Path(SPECPATH).resolve().parents[0]
sys.path.insert(0, str(repo_root / "scripts"))
from playwright_bundle_spec_support import collect_playwright_bundle

datas, hiddenimports = collect_playwright_bundle(repo_root)
a = Analysis(
    [str(repo_root / "scripts" / "playwright_bundle_smoke.py")],
    pathex=[str(repo_root / "backend")], binaries=[], datas=datas,
    hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="playwright-bundle-smoke", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="playwright-bundle-smoke")
