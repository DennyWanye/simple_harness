#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""把首启模型策展 + 生成 manifest + 上传到腾讯 COS（Option A 配套）。

provisioner（backend/deskpet/model_provisioner.py）首启时从 COS 直下：
``<base>/<subdir>/manifest.json`` + 各文件。本脚本负责把本机模型按 manifest
布局推到 COS。

前置：
  - coscli 已安装并配置好（或用 --secret-id/--secret-key 现配）。
    安装：https://cloud.tencent.com/document/product/436/63143
  - 桶已设「公有读私有写」（updater/provisioner 都匿名下载）。

用法（PowerShell，凭据可从 .env 注入）：
  $env:COS_SECRET_ID=...; $env:COS_SECRET_KEY=...
  python scripts/publish_models_to_cos.py \
      --models-dir F:/DeskPetData/models \
      --bucket defaultbucket-1300194691 --region ap-guangzhou

  # 仅生成 manifest、打印将上传的清单，不真上传：
  python scripts/publish_models_to_cos.py --models-dir F:/DeskPetData/models --dry-run

策展：bge-m3 的 onnx/（~2.2GB，FlagEmbedding pytorch 路径不用）+ imgs/docs 默认
排除，把单模型从 4.3GB 降到 ~2.25GB。如担心，--no-curate 上传完整目录。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# COS 上的前缀：<bucket>/COS_PREFIX/<subdir>/...
COS_PREFIX = "deskpet/models"

# 需要上传的模型子目录（与 provisioner._MODELS 对齐）。
MODEL_SUBDIRS = ["bge-m3-int8", "faster-whisper-large-v3-turbo"]

# 策展：排除这些（相对 subdir 的）目录/文件 —— 推理不需要，纯属体积。
CURATE_EXCLUDE_DIRS = {"onnx", "imgs"}
CURATE_EXCLUDE_FILES = {"long.jpg", "README.md", ".gitattributes"}


def _iter_files(root: Path, curate: bool):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = rel.parts
        if curate:
            if parts and parts[0] in CURATE_EXCLUDE_DIRS:
                continue
            if rel.name in CURATE_EXCLUDE_FILES:
                continue
        yield p, rel


def build_manifest(model_dir: Path, curate: bool) -> dict:
    files = []
    total = 0
    for p, rel in _iter_files(model_dir, curate):
        size = p.stat().st_size
        files.append({"path": str(rel).replace("\\", "/"), "size": size})
        total += size
    return {"files": files, "total_bytes": total}


def _coscli(args: list[str], bucket: str, region: str, dry: bool) -> None:
    cmd = ["coscli", *args]
    if dry:
        print("  [dry-run]", " ".join(cmd))
        return
    env = dict(os.environ)
    # coscli 读 COS_SECRETID/COS_SECRETKEY 或自身配置；这里两种命名都给上。
    env.setdefault("COS_SECRETID", env.get("COS_SECRET_ID", ""))
    env.setdefault("COS_SECRETKEY", env.get("COS_SECRET_KEY", ""))
    subprocess.run(cmd, check=True, env=env)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models-dir", required=True, type=Path)
    ap.add_argument("--bucket", default=os.environ.get("COS_BUCKET", "defaultbucket-1300194691"))
    ap.add_argument("--region", default=os.environ.get("COS_REGION", "ap-guangzhou"))
    ap.add_argument("--no-curate", action="store_true", help="上传完整目录（含 onnx 等）")
    ap.add_argument("--dry-run", action="store_true", help="只生成 manifest + 打印，不上传")
    args = ap.parse_args()

    curate = not args.no_curate
    models_dir: Path = args.models_dir
    if not models_dir.is_dir():
        print(f"models dir not found: {models_dir}", file=sys.stderr)
        return 2

    cos_base = f"cos://{args.bucket}/{COS_PREFIX}"
    grand_total = 0
    for subdir in MODEL_SUBDIRS:
        mdir = models_dir / subdir
        if not mdir.is_dir():
            print(f"WARN: 模型目录缺失，跳过: {mdir}", file=sys.stderr)
            continue
        manifest = build_manifest(mdir, curate)
        gb = manifest["total_bytes"] / 1e9
        grand_total += manifest["total_bytes"]
        print(f"\n=== {subdir}: {len(manifest['files'])} 文件, {gb:.2f}GB (curate={curate}) ===")
        manifest_path = mdir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"  manifest → {manifest_path}")

        # 上传每个文件（coscli sync 更省事，但 curate 排除需逐文件；这里用 cp 列表）。
        for f in manifest["files"]:
            src = mdir / f["path"]
            dst = f"{cos_base}/{subdir}/{f['path']}"
            _coscli(["cp", str(src), dst], args.bucket, args.region, args.dry_run)
        # 最后传 manifest（确保文件都到位后才可见 manifest）。
        _coscli(["cp", str(manifest_path), f"{cos_base}/{subdir}/manifest.json"], args.bucket, args.region, args.dry_run)

    print(f"\n首启总下载量(策展后) ≈ {grand_total/1e9:.2f}GB")
    if args.dry_run:
        print("（dry-run：未真上传。去掉 --dry-run 执行。）")
    else:
        print("完成。验证：curl -I "
              f"https://{args.bucket}.cos.{args.region}.myqcloud.com/{COS_PREFIX}/bge-m3-int8/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
