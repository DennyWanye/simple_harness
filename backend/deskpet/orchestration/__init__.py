# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host wiring of the SDK's ``agent_orchestrator`` (plan 2026-09-11, user's Phase3 P3.1).

The Host never writes the orchestration library itself: every command goes through the
SDK facade ``agent_orchestrator.api.facade.MissionControlV1``; this package only owns the
process-level concerns — the data directory and its instance lock, the provider snapshot,
the driver loop, the deployment manifest, the control-channel protocol and the change
pump.  See ``ARCHITECTURE/AGENT_ORCHESTRATION.md``.
"""
