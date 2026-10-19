# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""钉死的 DeepSeek V4.1 分词文件：认证计数器只认这一份（按 SHA-256 核对）。

``ensure_deepseek_tokenizer(dest)``：``dest`` 已是那份文件就直接返回；否则从 DeepSeek 官方仓库的
钉死提交下载、核对哈希、原子落盘。下载只在调用方明确调用时发生。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import httpx

from ..runtime.deepseek_tokens import TOKENIZER_SHA256, TOKENIZER_URL


def tokenizer_matches(path: str | Path) -> bool:
    """``path`` 是不是钉死的那份分词文件。"""

    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() == TOKENIZER_SHA256
    except OSError:
        return False


def ensure_deepseek_tokenizer(dest: str | Path, *, timeout: float = 120.0) -> Path:
    """返回核对过的分词文件路径；不在就下载到 ``dest``。哈希不对就抛 ``ValueError``。"""

    target = Path(dest)
    if tokenizer_matches(target):
        return target
    response = httpx.get(TOKENIZER_URL, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    raw = response.content
    if hashlib.sha256(raw).hexdigest() != TOKENIZER_SHA256:
        raise ValueError("下载到的 DeepSeek 分词文件与钉死的哈希不符")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(raw)
    os.replace(partial, target)
    return target


__all__ = ("TOKENIZER_SHA256", "TOKENIZER_URL", "ensure_deepseek_tokenizer", "tokenizer_matches")
