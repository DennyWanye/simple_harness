"""Structured OS-level tools.

Handlers use the existing ToolRegistry JSON-string contract. Process,
application, and download handlers are async; filesystem primitives remain
sync where no waiting is needed.

Permission gating happens *outside* the handler (in
``ToolRegistry.execute_tool``); the handlers themselves assume they are
allowed to run. They still validate inputs and return error envelopes
for filesystem / network / process failures.

Spec: openspec/changes/deskpet-skill-platform/specs/os-tools/spec.md
"""
