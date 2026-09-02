# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 6（H）：Task 4 真实车道曾观察到模型把 persona 文本追加进 README。

结论：Host ``write_file`` / ``edit_file`` handler 对模型给的 ``content`` **不做任何拼接**（原样 UTF-8 写入；
``mode='append'`` 只追加模型给的字节）——追加 persona 是纯模型行为（oracle 过弱，见 ARCHITECTURE 说明）。
本文件把"content 原样写入"锁成断言，防止 Host 侧未来引入非预期拼接。
"""

from __future__ import annotations

import json
from pathlib import Path

from deskpet.tools.os_tools.write_file import write_file


def test_write_file_writes_model_content_verbatim(tmp_path: Path) -> None:
    target = tmp_path / "README.md"
    content = "# 标题\n\n版本 1.2.0\n"  # 含多字节 + 结尾换行：一个字节都不多不少
    result = json.loads(write_file({"path": str(target), "content": content}))
    assert result["bytes_written"] == len(content.encode("utf-8"))
    assert target.read_bytes() == content.encode("utf-8")
    # 覆盖写：仍原样（无前缀/后缀/persona）。
    replaced = "只有这一行"
    json.loads(write_file({"path": str(target), "content": replaced, "overwrite": True}))
    assert target.read_bytes() == replaced.encode("utf-8")
    # 追加写：只追加模型给的字节。
    appended = "\n追加一行"
    json.loads(write_file({"path": str(target), "content": appended, "mode": "append"}))
    assert target.read_bytes() == (replaced + appended).encode("utf-8")
    # 空内容显式允许（写空文件），仍原样。
    empty = tmp_path / "empty.txt"
    json.loads(write_file({"path": str(empty), "content": ""}))
    assert empty.read_bytes() == b""
