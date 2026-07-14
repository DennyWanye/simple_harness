from __future__ import annotations

import pytest

from config import AppConfig, ConfigError, load_config


def test_workflow_defaults_are_enabled_for_test_stage():
    workflows = AppConfig().workflows
    assert workflows.enabled is True
    assert workflows.deep_research is True
    assert workflows.ppt_pro is True
    assert workflows.code_complex is True
    assert workflows.trace_enabled is True


def test_workflow_explicit_false_is_preserved(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """[workflows]
enabled = true
deep_research = false
ppt_pro = false
code_complex = false
trace_enabled = true
terminal_retention_days = 7
evaluation_retention_days = 30
orphan_grace_hours = 2
""",
        encoding="utf-8",
    )
    workflows = load_config(path).workflows
    assert workflows.deep_research is False
    assert workflows.ppt_pro is False
    assert workflows.code_complex is False


def test_workflow_retention_rejects_invalid_order(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """[workflows]
terminal_retention_days = 30
evaluation_retention_days = 7
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="evaluation_retention_days"):
        load_config(path)
