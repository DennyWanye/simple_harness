from __future__ import annotations

from pathlib import Path

from config import AppConfig, load_config
from deskpet.workflows.definitions.v2 import DEFAULT_DEEP_RESEARCH_VERSION as RECOVERY_V2_VERSION
from deskpet.workflows.definitions.v3 import DEFAULT_DEEP_RESEARCH_VERSION as RECOVERY_V3_VERSION
from deskpet.workflows.definitions.v4 import DEFAULT_DEEP_RESEARCH_VERSION as RECOVERY_V4_VERSION
from deskpet.workflows.definitions.v5 import DEFAULT_DEEP_RESEARCH_VERSION as RECOVERY_V5_VERSION
from deskpet.workflows.definitions.v6 import DEFAULT_DEEP_RESEARCH_VERSION
from deskpet.workflows.native import NativeExecutionPolicy


def test_new_deepresearch_runs_default_to_v6_with_bounded_parallelism() -> None:
    config = AppConfig()
    assert config.workflows.enabled is True
    assert config.workflows.deep_research is True
    assert config.workflows.deep_research_version == "v6"
    assert config.workflows.deep_research_default_revision == 6
    assert DEFAULT_DEEP_RESEARCH_VERSION == "v6"
    assert RECOVERY_V5_VERSION == "v5"
    assert RECOVERY_V4_VERSION == "v4"
    assert RECOVERY_V3_VERSION == "v3"
    assert RECOVERY_V2_VERSION == "v2"
    assert 2 <= config.workflows.deep_research_max_parallel_tasks <= 6
    assert NativeExecutionPolicy(config.workflows.deep_research_max_parallel_tasks).max_parallel_tasks == 4


def test_product_config_enables_deepresearch_v6() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "config.toml")
    assert config.workflows.deep_research_version == "v6"
    assert config.workflows.deep_research_default_revision == 6
    assert config.workflows.deep_research_max_parallel_tasks == 4
