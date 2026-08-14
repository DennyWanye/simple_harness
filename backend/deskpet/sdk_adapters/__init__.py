"""Product SDK adapters - bridge between Simple Harness product and SDK protocols.

This package implements the SDK Protocol interfaces defined in simple-harness-sdk,
allowing the product to consume SDK workflows and execution model while retaining
product-owned concerns (UI, persistence, companion, capabilities).

Architecture:
- composition: Runtime construction entry point
- ingress: Request admission and routing
- provider: LLM provider configuration
- authorization: Tool permission enforcement
- context: Memory and context assembly
- tools: Tool execution delegation
- reconciliation: State synchronization
- personal_catalog: Personal workflow catalog
- capability_host: Capability platform integration
- delivery: Event delivery to UI
- runtime_paths: File system path resolution
"""

from __future__ import annotations

__all__ = ()
