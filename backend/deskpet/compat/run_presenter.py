"""Compatibility name for the canonical product Run presenter builder."""

from deskpet.agent.run_presenter import build_product_run_presenter

build_legacy_run_presenter = build_product_run_presenter

__all__ = ["build_legacy_run_presenter"]
