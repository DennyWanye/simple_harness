# SPDX-License-Identifier: Apache-2.0
"""The certified word counter of the scripted test lanes (Host and SDK share this one copy since
2026-10-03; the identity strings are unchanged, so existing execution pools keep their identity)."""

from __future__ import annotations


class FixtureWordCounter:
    """One token per whitespace word: the certified counter of the fixture lanes.

    The Host tests run on native pools, and a native pool needs a certified counter.  Exact for the
    scripted providers, whose replies are counted the same way.  Trusted test composition
    only — never selected for a real model endpoint.
    """

    fingerprint = "host-test-exact-words:v1"
    count_mode = "EXACT"
    requires_prior_output_reserve = False
    tool_schema_mode = "legacy"
    bound_protocol = "host-test-exact-words-v1"

    def count_text(self, text: str) -> int:
        return len(text.split())

    def estimate_input_tokens(self, request) -> int:  # type: ignore[no-untyped-def]
        return sum(self.count_text(m.content if isinstance(m.content, str) else "") for m in request.messages)

    def meter_factory(self, counter, *, input_limit_tokens, max_output_tokens, prior_reserve=None):  # type: ignore[no-untyped-def]
        from simple_harness.agents.arp.meter import MeterBinding, model_limits
        from simple_harness.agents.arp.pins import Pin
        from simple_harness.agents.arp.strict import digest

        limits = model_limits(model="agent-model", tokenizer=counter, input_limit_tokens=input_limit_tokens,
                              max_output_tokens=max_output_tokens, provider_id="host-test")
        return MeterBinding(tokenizer=counter, model_limits=limits, certification_ref=Pin(
            "receipt", "meter-certification:host-test-exact-words", 0, digest({"rule": "one token per word"})),
            prior_reserve=prior_reserve)
