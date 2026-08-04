# 生成 beta.9 的 latest.json(GitHub url) + latest.cos.json(COS url)
# beta.9 = beta.8 的真正修复版：beta.7/8 因 dist-portable 旧目录 + 构建 venv 缺
# tomlkit，把 6/8 旧后端打进安装包，空 Bearer 崩溃/feature flag 未点亮照旧。
# 本版修打包源（junction 指向 6/28 新后端）+ 构建 venv 补 tomlkit → 后端修复真正进包。
import json
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.6.0-beta.9"
BUNDLE = Path(r"G:\projects\deskpet\tauri-app\src-tauri\target\release\bundle\nsis")
EXE_NAME = f"DeskPet_{VERSION}_x64-setup.exe"
SIG = (BUNDLE / f"{EXE_NAME}.sig").read_text(encoding="utf-8").strip()

NOTES = (
    "修复 beta.7/8 的发布管线缺陷：安装包此前误打入 6/8 的旧后端"
    "（dist-portable 打包源指向旧目录 + 构建环境缺 tomlkit），导致登录后"
    "「空 Bearer 请求头崩溃」与部分新能力 flag 未点亮的修复其实从未真正随包发出。"
    "本版将打包源绑定到含完整修复的最新后端、补齐 tomlkit 依赖，"
    "使「用户数据目录单一事实源绑定 + 无可用 key 友好提示而非崩溃 + 特性开关回填」"
    "真正进入安装包生效。建议干净重装一次以确保后端可执行文件被替换。"
)

PUB_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

GH_URL = f"https://github.com/DennyWanye/deskpet/releases/latest/download/{EXE_NAME}"
COS_URL = f"https://defaultbucket-1300194691.cos.ap-guangzhou.myqcloud.com/deskpet/{EXE_NAME}"


def manifest(url: str) -> dict:
    return {
        "version": VERSION,
        "notes": NOTES,
        "pub_date": PUB_DATE,
        "platforms": {
            "windows-x86_64": {"signature": SIG, "url": url},
        },
    }


out_gh = BUNDLE / "latest.json"
out_cos = BUNDLE / "latest.cos.json"
out_gh.write_text(json.dumps(manifest(GH_URL), ensure_ascii=False, indent=2), encoding="utf-8")
out_cos.write_text(json.dumps(manifest(COS_URL), ensure_ascii=False, indent=2), encoding="utf-8")

print("WROTE:", out_gh)
print("WROTE:", out_cos)
print("version:", VERSION, "| pub_date:", PUB_DATE, "| sig_len:", len(SIG))
print("GH url :", GH_URL)
print("COS url:", COS_URL)
