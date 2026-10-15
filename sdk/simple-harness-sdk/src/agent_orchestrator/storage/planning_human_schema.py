# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Planning human-request DDL (migration 24): durable questions and their authenticated answers."""

from __future__ import annotations

DDL = """
CREATE TABLE planning_human_requests (
 decision_id TEXT PRIMARY KEY,
 mission_id TEXT NOT NULL REFERENCES missions(mission_id),
 subject_key TEXT NOT NULL,
 request_json TEXT NOT NULL,
 request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('PENDING','ANSWERED','STALE')),
 answer_json TEXT,
 version INTEGER NOT NULL DEFAULT 1,
 created_at REAL NOT NULL,
 answered_at REAL
) STRICT;
CREATE INDEX planning_human_pending ON planning_human_requests(mission_id,state);
"""
