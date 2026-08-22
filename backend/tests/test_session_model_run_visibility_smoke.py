from __future__ import annotations

from backend.scripts import smoke_session_model_run_visibility as smoke


def test_smoke_matrix_covers_each_independent_automation_surface():
    backend = "\n".join(smoke.BACKEND_MATRIX)
    frontend = "\n".join(smoke.FRONTEND_MATRIX)

    assert "test_provider_projection_pump.py" in backend
    assert "test_pinned_to_deleted_provider_fails_closed" in backend
    assert "test_agent_activity_projection.py" in backend
    assert "test_official_memory_product_integration.py" in backend
    assert "test_cancelled_run_rejects_late_result" in backend
    assert "HarnessInspectorPanel.test.tsx" in frontend
    assert "AgentActivityMessage.test.tsx" in frontend


def test_backend_only_runs_no_frontend_surface(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(smoke, "_parse_args", lambda: type("Args", (), {"backend_only": True})())
    monkeypatch.setattr(smoke, "run_backend_matrix", lambda: calls.append("backend"))
    monkeypatch.setattr(smoke, "run_frontend_matrix", lambda: calls.append("frontend"))

    assert smoke.main() == 0
    assert calls == ["backend"]
