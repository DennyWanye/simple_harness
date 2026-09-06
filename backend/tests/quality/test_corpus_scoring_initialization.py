"""Actual main initialization/cleanup only; network and model dispatch forbidden."""
import json
import socket
import sys
from pathlib import Path

import httpx
import pytest

from deskpet.quality.corpus_scoring import prepare_batch
from deskpet.quality.corpus_scoring_session import configure_process, run


@pytest.mark.asyncio
async def test_actual_main_initialize_close_and_original_input_isolation(tmp_path, monkeypatch):
    corpus = Path("/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/review-zh/successor-12x20")
    compiler = Path("/Users/denny/projects/simple-harness-memory-sdk/scripts")
    host = Path(__file__).resolve().parents[3]
    root = prepare_batch(corpus_root=corpus, compiler_root=compiler,
        case_ids=["C01-10"], output=tmp_path / "corpus")
    directory = root / "C01-10"
    original_input = (directory / "input.json").read_bytes()
    # This mutation is confined to the control's compiled oracle copy. If the
    # worker ever reads it, fail before dispatch; original frozen MD stays intact.
    (directory / "oracle.json").write_text('{"forbidden_oracle_canary":"DO_NOT_READ"}')
    original_read = Path.read_text
    reads = []
    def tracked_read(path, *args, **kwargs):
        reads.append(str(path))
        assert path.name not in {"oracle.json", "original-documents.json", "case.json"}
        assert str(path) != "/Users/denny/projects/simple_harness/.env"
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", tracked_read)
    network = []
    def deny_connect(*args, **kwargs):
        network.append("socket")
        raise AssertionError("network forbidden during initialization")
    async def deny_http(*args, **kwargs):
        network.append("http")
        raise AssertionError("HTTP forbidden during initialization")
    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connect)
    monkeypatch.setattr(httpx.AsyncClient, "send", deny_http)
    before_stdout, before_stderr = sys.stdout, sys.stderr
    try:
        key, endpoint = configure_process(directory, host, initialize_only=True)
        code = await run(directory, host, key, endpoint, initialize_only=True)
    finally:
        sys.stdout, sys.stderr = before_stdout, before_stderr
    result = json.loads(original_read(directory / "execution.json"))
    assert code == 0, result
    assert result["execution_status"] == "INITIALIZED_NOT_EXECUTED"
    assert result["cleanup_errors"] == []
    assert result["setup_receipt"]["outcome"] == "applied"
    assert result["trace"] is None
    assert result["embedder_status"]["state"] == "cold"
    assert result["embedder_status"]["warmup_state"] == "not_started"
    assert not network
    assert (directory / "input.json").read_bytes() == original_input
    assert set(result["setup_receipt"]["labels"]) == {"A"}
