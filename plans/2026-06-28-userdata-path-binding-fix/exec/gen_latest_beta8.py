# 生成 beta.8 的 latest.json(GitHub url) + latest.cos.json(COS url)
# README §4 步骤4：用 Python 生成(PowerShell 会坏中文 notes)。
import json
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.6.0-beta.8"
BUNDLE = Path(r"G:\projects\deskpet\tauri-app\src-tauri\target\release\bundle\nsis")
EXE_NAME = f"DeskPet_{VERSION}_x64-setup.exe"
SIG = (BUNDLE / f"{EXE_NAME}.sig").read_text(encoding="utf-8").strip()

NOTES = (
    "修复装机版（安装到自定义目录，如 F:\\）聊天报错崩溃的问题："
    "用户数据目录（config.toml / 数据库）在登录与重启之间发生路径漂移，"
    "导致已登录的 LLM provider 丢失、退化到空 key 拼出非法 Bearer 请求头使对话崩溃。"
    "本版将用户数据目录与安装目录做单一事实源绑定（Rust/Python 统一解析 + 启动自愈迁移历史数据），"
    "并在无可用 key 时给出『请重新登录』友好提示而非崩溃；附带 keyring 打包加固。"
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
