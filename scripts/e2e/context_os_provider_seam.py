#!/usr/bin/env python3
"""Deterministic control overlay for the Context OS provider fixture.

The reusable response fixture intentionally stays small.  This executable adds
the stateful E2E controls required by P3/P5 without changing that base fixture.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import context_os_provider_fixture as fixture


def _now() -> float:
    return time.time()


def _error(message: str) -> dict:
    return {"error": {"message": message, "type": "scenario_error"}}


def _ensure_state() -> None:
    state = fixture.STATE
    if not hasattr(state, "model_faults"):
        state.model_faults = {}
    if not hasattr(state, "fallback_arm"):
        state.fallback_arm = None
    if not hasattr(state, "control_sequence"):
        state.control_sequence = 0


def _new_arm(kind: str, **values: object) -> dict:
    fixture.STATE.control_sequence += 1
    return {
        "arm_id": f"{kind}-{fixture.STATE.control_sequence}",
        "armed_at": _now(),
        "trigger_count": 0,
        "cleared_at": None,
        **values,
    }


class Handler(fixture.H):
    def _state_payload(self) -> dict:
        _ensure_state()
        return {
            "ok": True,
            "models": {
                model: fixture.STATE.models[model]
                for model in sorted(fixture.STATE.models)
            },
            "model_catalog": [
                {"id": model, "context_window": fixture.STATE.models[model]}
                for model in sorted(fixture.STATE.models)
            ],
            "faults": {
                model: record["error"]
                for model, record in sorted(fixture.STATE.model_faults.items())
                if record.get("active")
            },
            "model_faults": {
                model: dict(record)
                for model, record in sorted(fixture.STATE.model_faults.items())
            },
            "fallback": (
                dict(fixture.STATE.fallback_arm)
                if fixture.STATE.fallback_arm is not None
                else None
            ),
            "force_finish": (
                dict(fixture.STATE.force) if fixture.STATE.force is not None else None
            ),
            "paused": sorted(fixture.STATE.paused),
            "pause_tools": dict(fixture.STATE.pause_tools),
            "scenario_count": len(fixture.STATE.scenarios),
        }

    def _send_force_terminal(self, body: dict, *, request_id: str, attempt_id: str, marker: str):
        model = str(body.get("model", ""))
        digest = fixture.hashlib.sha256(fixture.canon(body).encode()).hexdigest()
        message = {"role": "assistant", "content": "FORCE-FINISH-ACK"}
        prompt = fixture.token_count(
            {
                "messages": body.get("messages") or [],
                "tools": body.get("tools"),
                "tool_choice": body.get("tool_choice"),
            }
        )
        completion = fixture.token_count(message)
        response = {
            "id": "chatcmpl-" + attempt_id[-12:],
            "object": "chat.completion",
            "created": int(_now()),
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            },
        }
        fixture.STATE.event(
            "attempt_received",
            purpose="force_finish",
            request_id=request_id,
            attempt_id=attempt_id,
            model=model,
            marker=marker,
            body_sha256=digest,
            tools_count=0,
            tool_names=[],
            stream=bool(body.get("stream")),
        )
        fixture.STATE.event(
            "attempt_terminal",
            purpose="force_finish",
            request_id=request_id,
            attempt_id=attempt_id,
            model=model,
            marker=marker,
            finish_reason="stop",
            prompt_tokens=prompt,
            body_sha256=digest,
            observed_tools_none=True,
        )
        if not body.get("stream"):
            return self.sendj(200, response)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunk = {
            "id": response["id"],
            "object": "chat.completion.chunk",
            "created": response["created"],
            "model": model,
            "choices": [{"index": 0, "delta": message, "finish_reason": None}],
        }
        terminal = {
            **chunk,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": response["usage"],
        }
        for value in (chunk, terminal):
            self.wfile.write(("data: " + fixture.canon(value) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/__control/state":
            with fixture.STATE.lock:
                return self.sendj(200, self._state_payload())
        if path.startswith("/__control/model-fault/"):
            model = path.rsplit("/", 1)[1]
            with fixture.STATE.lock:
                _ensure_state()
                record = fixture.STATE.model_faults.get(model)
                return self.sendj(
                    200 if record is not None else 404,
                    {"ok": record is not None, "model": model, "fault": record},
                )
        return super().do_GET()

    def do_DELETE(self):
        path = urlparse(self.path).path
        with fixture.STATE.lock:
            _ensure_state()
            if path.startswith("/__control/model-fault/"):
                model = path.rsplit("/", 1)[1]
                record = fixture.STATE.model_faults.pop(model, None)
                if record is not None:
                    record["cleared_at"] = _now()
                    fixture.STATE.event(
                        "fault_cleared",
                        fault_name="model_fault",
                        model=model,
                        arm_id=record["arm_id"],
                        trigger_count=record["trigger_count"],
                        cleared_at=record["cleared_at"],
                    )
                return self.sendj(200, {"ok": True, "cleared": record is not None})
            if path == "/__control/fallback":
                record = fixture.STATE.fallback_arm
                fixture.STATE.fallback_arm = None
                if record is not None:
                    record["cleared_at"] = _now()
                    fixture.STATE.event("fallback_cleared", **record)
                return self.sendj(200, {"ok": True, "cleared": record is not None})
            if path == "/__control/force-finish":
                record = fixture.STATE.force
                fixture.STATE.force = None
                if record is not None:
                    record["cleared_at"] = _now()
                    fixture.STATE.event("force_finish_cleared", **record)
                return self.sendj(200, {"ok": True, "cleared": record is not None})
        return super().do_DELETE()

    def control(self, command: str, body: dict):
        with fixture.STATE.lock:
            _ensure_state()
            if command == "catalog":
                model = str(body.get("model", "")).strip()
                window = int(body.get("window", 0) or 0)
                if not model or window <= 0:
                    return self.sendj(400, _error("catalog_requires_model_and_positive_window"))
                fixture.STATE.models[model] = window
                fixture.STATE.event("control_catalog", model=model, window=window)
                return self.sendj(200, {"ok": True, "model": model, "window": window})
            if command == "model-fault":
                model = str(body.get("model", "")).strip()
                if model not in fixture.STATE.models:
                    return self.sendj(409, _error("model_fault_requires_cataloged_model"))
                existing = fixture.STATE.model_faults.get(model)
                if existing is not None and existing.get("active"):
                    return self.sendj(409, _error("model_fault_already_armed"))
                record = _new_arm(
                    "model-fault",
                    fault_name="model_fault",
                    model=model,
                    error=str(body.get("error") or "fixture_model_fault"),
                    active=True,
                    triggered_request_id=None,
                )
                fixture.STATE.model_faults[model] = record
                fixture.STATE.event("fault_armed", **record)
                return self.sendj(200, {"ok": True, "fault": dict(record)})
            if command == "fallback":
                if fixture.STATE.fallback_arm is not None:
                    return self.sendj(409, _error("fallback_already_armed"))
                marker = str(body.get("marker", "")).strip()
                from_model = str(body.get("from") or "ctx-primary")
                to_model = str(body.get("to") or "ctx-fallback")
                if not marker or from_model == to_model:
                    return self.sendj(400, _error("invalid_fallback_arm"))
                if from_model not in fixture.STATE.models or to_model not in fixture.STATE.models:
                    return self.sendj(409, _error("fallback_model_not_cataloged"))
                fixture.STATE.fallback_arm = _new_arm(
                    "fallback",
                    fault_name="provider_fallback",
                    marker=marker,
                    **{"from": from_model, "to": to_model},
                    from_model=from_model,
                    to_model=to_model,
                    state="S0",
                    request_id=None,
                    triggered_request_id=None,
                    primary_attempt_id=None,
                )
                fixture.STATE.event("fallback_armed", **fixture.STATE.fallback_arm)
                return self.sendj(200, {"ok": True, "fallback": dict(fixture.STATE.fallback_arm)})
            if command == "force-finish":
                if fixture.STATE.force is not None:
                    return self.sendj(409, _error("force_finish_already_armed"))
                marker = str(body.get("marker", "")).strip()
                rounds = int(body.get("tool_rounds", 3) or 0)
                if not marker or rounds <= 0:
                    return self.sendj(400, _error("invalid_force_finish_arm"))
                fixture.STATE.force = _new_arm(
                    "force-finish",
                    fault_name="force_finish",
                    marker=marker,
                    tool_rounds=rounds,
                    round=0,
                    state="F0",
                    request_id=None,
                    triggered_request_id=None,
                    attempt_ids=[],
                )
                fixture.STATE.event("force_finish_armed", **fixture.STATE.force)
                return self.sendj(200, {"ok": True, "force_finish": dict(fixture.STATE.force)})
            if command == "reset":
                fixture.STATE.model_faults.clear()
                fixture.STATE.fallback_arm = None
                fixture.STATE.force = None
                fixture.STATE.paused.clear()
                fixture.STATE.pause_tools.clear()
                fixture.STATE.gate.set()
                fixture.STATE.scenarios.clear()
                fixture.STATE.completed.clear()
                fixture.STATE.event("control_reset")
                return self.sendj(200, {"ok": True})
        return super().control(command, body)

    def chat(self, body: dict):
        _ensure_state()
        model = str(body.get("model", ""))
        messages = body.get("messages") or []
        tools = body.get("tools")
        marker = fixture.marker_from(messages)
        purpose = self.headers.get("X-DeskPet-Purpose", "agent_response")
        request_id = self.headers.get("X-DeskPet-Request-Id", "")
        attempt_id = self.headers.get("X-DeskPet-Attempt-Id", "")

        with fixture.STATE.lock:
            fault = fixture.STATE.model_faults.get(model)
            if fault is not None and fault.get("active"):
                fault["active"] = False
                fault["trigger_count"] += 1
                fault["triggered_request_id"] = request_id
                fault["cleared_at"] = _now()
                fixture.STATE.event(
                    "fault_triggered",
                    **fault,
                    attempt_id=attempt_id,
                    purpose=purpose,
                )
                fixture.STATE.event(
                    "attempt_error",
                    purpose=purpose,
                    request_id=request_id,
                    attempt_id=attempt_id,
                    model=model,
                    marker=marker,
                    status=503,
                    fault_name="model_fault",
                )
                return self.sendj(
                    503,
                    {"error": {"message": fault["error"], "type": "fixture_model_error", "code": fault["error"]}},
                )

            fallback = fixture.STATE.fallback_arm
            if fallback is not None and marker == fallback["marker"]:
                if purpose != "agent_response":
                    return self.sendj(409, _error("fallback_requires_agent_response"))
                if fallback["state"] == "S0":
                    if model != fallback["from_model"]:
                        return self.sendj(409, _error("fallback_expected_primary"))
                    fallback["state"] = "S1"
                    fallback["request_id"] = request_id
                    fallback["triggered_request_id"] = request_id
                    fallback["primary_attempt_id"] = attempt_id
                    fallback["trigger_count"] = 1
                    fixture.STATE.event(
                        "attempt_received",
                        purpose=purpose,
                        request_id=request_id,
                        attempt_id=attempt_id,
                        model=model,
                        marker=marker,
                        stream=bool(body.get("stream")),
                    )
                    fixture.STATE.event("fallback_primary_terminal", **fallback)
                    fixture.STATE.event(
                        "attempt_error",
                        purpose=purpose,
                        request_id=request_id,
                        attempt_id=attempt_id,
                        model=model,
                        marker=marker,
                        status=503,
                    )
                    return self.sendj(503, {"error": {"message": "fixture_primary_unavailable", "type": "fixture_error", "code": "fixture_primary_unavailable"}})
                if (
                    model != fallback["to_model"]
                    or request_id != fallback["request_id"]
                    or attempt_id == fallback["primary_attempt_id"]
                ):
                    return self.sendj(409, _error("fallback_retry_causality_mismatch"))
                fallback["state"] = "DONE"
                fallback["cleared_at"] = _now()
                completed = dict(fallback)
                result = super().chat(body)
                fixture.STATE.fallback_arm = None
                fixture.STATE.event("fallback_terminal", fallback_attempt_id=attempt_id, **completed)
                return result

            force = fixture.STATE.force
            if force is not None and marker == force["marker"]:
                if purpose not in {"agent_response", "force_finish"}:
                    return self.sendj(409, _error("force_finish_invalid_purpose"))
                if force["request_id"] is None:
                    force["request_id"] = request_id
                    force["triggered_request_id"] = request_id
                    force["trigger_count"] = 1
                if request_id != force["request_id"] or attempt_id in force["attempt_ids"]:
                    return self.sendj(409, _error("force_finish_attempt_causality_mismatch"))
                force["attempt_ids"].append(attempt_id)
                rounds = int(force["tool_rounds"])
                current = int(force["round"])
                current_turn = messages[max((i for i, item in enumerate(messages) if item.get("role") == "user"), default=-1) + 1 :]
                tool_results = [item for item in current_turn if item.get("role") == "tool"]
                if current < rounds:
                    if tools is None or len(tool_results) < current:
                        return self.sendj(409, _error("force_finish_missing_tools_or_result"))
                    force["state"] = f"F{current + 1}"
                    round_event = dict(force)
                    round_event["next_round"] = current + 1
                    fixture.STATE.event("force_finish_round", **round_event)
                    return super().chat(body)
                if tools is not None or len(tool_results) < rounds:
                    return self.sendj(409, _error("force_finish_terminal_requires_tools_none_and_results"))
                completed = dict(force)
                completed["state"] = "DONE"
                completed["cleared_at"] = _now()
                force["cleared_at"] = completed["cleared_at"]
                result = self._send_force_terminal(
                    body,
                    request_id=request_id,
                    attempt_id=attempt_id,
                    marker=marker,
                )
                fixture.STATE.force = None
                fixture.STATE.event("force_finish_terminal", **completed)
                return result

        return super().chat(body)


fixture.H = Handler


if __name__ == "__main__":
    fixture.main()
