"""Some OpenAI-compatible providers return ``message.content`` as a list of parts (seen live from
the TokenRouter/Qwen fallback); the response model wants text. Found by the Phase 6 smoke eval."""

from __future__ import annotations

from packages.orchestrator.llm.openai_compat import text_content


def test_text_content_accepts_every_shape_the_providers_send() -> None:
    assert text_content("plain") == "plain"
    assert text_content(None) is None
    assert text_content("") == ""
    parts = [
        {"type": "text", "text": "first"},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "second"},
    ]
    assert text_content(parts) == "first\nsecond"
    assert (
        text_content([{"type": "reasoning", "text": "…"}]) == ""
    )  # no text parts → empty, not None
    assert text_content(42) == "42"
