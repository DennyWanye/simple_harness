# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Embedding port for the native plane's session index (the old Journal-wide window
search was removed on 2026-10-03)."""

from .embedding import EmbeddingPort, EmbeddingUnavailable, HashEmbedder, cosine

__all__ = (
    "EmbeddingPort",
    "EmbeddingUnavailable",
    "HashEmbedder",
    "cosine",
)
