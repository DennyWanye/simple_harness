from __future__ import annotations

from scripts.verify_real_harness_public_fixture import REQUIRED_ENV, main


def test_gate_fails_when_real_fixture_source_is_not_configured(
    monkeypatch, capsys
) -> None:
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)

    assert main() == 2
    assert "fixture source is not configured" in capsys.readouterr().err


def test_gate_fails_when_real_fixture_database_is_missing(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv(REQUIRED_ENV[0], str(tmp_path / "missing-workflow.db"))
    monkeypatch.setenv(REQUIRED_ENV[1], str(tmp_path / "missing-state.db"))
    monkeypatch.setenv(REQUIRED_ENV[2], "session")
    monkeypatch.setenv(REQUIRED_ENV[3], "root")

    assert main() == 2
    assert "fixture source does not exist" in capsys.readouterr().err
