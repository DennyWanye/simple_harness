# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""The historical text of migration 14 (fragment validation, removed 2026-10-02).

Kept only because an existing library is opened by comparing every recorded migration's
checksum; migration 31 drops the table."""

FRAGMENT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fragment_validations (
    fragment_id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL REFERENCES missions(mission_id),
    origin_result_id TEXT NOT NULL REFERENCES results(result_id),
    validation_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
    projection_receipt_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS fragment_validations_mission
ON fragment_validations(mission_id, created_at, fragment_id);
"""
