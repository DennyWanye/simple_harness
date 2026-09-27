# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""NEXT-TG-1.0 §9: the publish directory has a Settings entry again.

A directory is authorised only if it exists, is a directory, can carry the hard
link the connector commits with, and does not overlap the app's own data.  The
value is written back to ``config.toml`` (other keys untouched) and the
orchestration service restarts once so the connector is assembled from it.  An
empty path revokes; nothing already published is touched.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

import pytest

from deskpet.orchestration.publish_settings import (
    PublishDirRefused,
    set_publish_dir,
    validate_publish_dir,
    write_publish_dir,
)

MAIN = Path(__file__).parents[2] / "main.py"


def test_validation_refuses_what_cannot_be_published_to(tmp_path):
    data = tmp_path / "userdata"
    data.mkdir()
    (tmp_path / "file.txt").write_text("x")
    cases = {
        "relative/dir": "not_absolute",
        str(tmp_path / "missing"): "not_found",
        str(tmp_path / "file.txt"): "not_directory",
        str(data / "inside"): "overlaps_app_data",
        str(tmp_path): "overlaps_app_data",  # contains the app data
        123: "invalid_request",
    }
    (data / "inside").mkdir()
    for raw, code in cases.items():
        with pytest.raises(PublishDirRefused) as refused:
            validate_publish_dir(raw, protected=[data])
        assert refused.value.code == code, raw
    target = tmp_path / "published"
    target.mkdir()
    assert validate_publish_dir(str(target), protected=[data]) == str(target.resolve())
    assert validate_publish_dir("  ", protected=[data]) == ""


def test_write_keeps_the_rest_of_the_config_and_revoke_removes_the_key(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('# 注释保留\n[orchestration]\nmax_concurrency = 2\n\n[other]\nx = 1\n', encoding="utf-8")
    write_publish_dir(config, "/tmp/pub")
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["orchestration"] == {"max_concurrency": 2, "publish_dir": "/tmp/pub"}
    assert parsed["other"] == {"x": 1} and "# 注释保留" in config.read_text(encoding="utf-8")
    write_publish_dir(config, "")
    assert "publish_dir" not in tomllib.loads(config.read_text(encoding="utf-8"))["orchestration"]


def test_set_restarts_once_and_a_refusal_changes_nothing(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[orchestration]\n", encoding="utf-8")
    target = tmp_path / "pub"
    target.mkdir()
    restarts = []

    async def restart():
        restarts.append(tomllib.loads(config.read_text(encoding="utf-8"))["orchestration"].get("publish_dir"))

    async def case():
        await set_publish_dir(str(target), config_path=config, protected=[tmp_path / "data"], restart=restart)
        with pytest.raises(PublishDirRefused):
            await set_publish_dir(str(tmp_path / "nope"), config_path=config, protected=[], restart=restart)
        await set_publish_dir("", config_path=config, protected=[], restart=restart)

    asyncio.run(case())
    assert restarts == [str(target.resolve()), None]  # the restart reads the value already written


def test_the_control_channel_handles_both_verbs_before_the_orchestration_prefix():
    source = MAIN.read_text(encoding="utf-8")
    verbs = source.index('msg_type in ("orchestration_publish_dir_get", "orchestration_publish_dir_set")')
    prefix = source.index('msg_type.startswith(("mission_", "orchestration_", "taskgraph."))')
    assert verbs < prefix
    assert "restart=_restart_orchestration" in source
