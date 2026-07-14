from pathlib import Path

from config import AppConfig, load_config


def test_context_os_v1_is_factory_default_on() -> None:
    assert AppConfig().features.context_os_v1 is True


def test_repository_config_enables_context_os_v1() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.toml"
    assert load_config(config_path).features.context_os_v1 is True


def test_explicit_false_remains_the_single_rollback_contract(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[features]\ncontext_os_v1 = false\n",
        encoding="utf-8",
    )
    assert load_config(config_path).features.context_os_v1 is False
