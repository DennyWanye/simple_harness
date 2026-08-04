from __future__ import annotations

import base64

from deskpet.agent.attachment_budget import (
    append_user_attachment_blocks,
    collect_attachment_budget,
    normalize_user_attachment_blocks,
)


def test_provider_content_blocks_become_body_free_attachment_refs() -> None:
    body = b"private-image-body"
    encoded = base64.b64encode(body).decode("ascii")
    blocks = normalize_user_attachment_blocks(
        [
            {"type": "text", "text": "ignored duplicate text"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            },
        ]
    )
    messages = append_user_attachment_blocks(
        [{"role": "user", "content": "inspect"}], blocks
    )
    refs, tokens = collect_attachment_budget(messages)

    assert messages[0]["content"][0] == {"type": "text", "text": "inspect"}
    assert messages[0]["content"][1]["type"] == "image_url"
    assert len(refs) == 1
    assert refs[0].media_type == "image_url"
    assert refs[0].byte_size == len(body)
    assert refs[0].estimated_tokens == tokens
    assert encoded not in repr(refs[0])


def test_remote_media_uses_conservative_unknown_upper_bound() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "listen"},
                {"type": "input_audio", "input_audio": {"url": "https://example.test/a.wav"}},
            ],
        }
    ]
    refs, tokens = collect_attachment_budget(messages)
    assert refs[0].byte_size == 0
    assert refs[0].estimate_method == "unknown_media_upper_bound"
    assert tokens == 4_096
