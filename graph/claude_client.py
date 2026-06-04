"""Thin wrapper around the Anthropic messages API.

Used by graph analytics (org_health), ETL tasks (departure reports, transfer
plans, weekly digest), and API routers (manager suggestions) so the SDK
instantiation and TextBlock extraction are not repeated across five modules.

The function raises on any SDK or network error — callers own their fallback.
"""

from __future__ import annotations

import os

_DEFAULT_MODEL = "claude-sonnet-4-6"


def call_claude(
    prompt: str,
    *,
    max_tokens: int = 300,
    model: str | None = None,
) -> str:
    """Call the Claude messages API and return the first text block's content.

    Args:
        prompt:     The user message to send.
        max_tokens: Maximum tokens in the response (default 300).
        model:      Model ID override; falls back to ``CLAUDE_MODEL`` env var,
                    then ``claude-sonnet-4-6``.

    Returns:
        Stripped text from the first TextBlock in the response.

    Raises:
        anthropic.APIError: on any API-level failure.
        Any other exception propagated from the SDK.
    """
    import anthropic
    from anthropic.types import TextBlock

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    response = client.messages.create(
        model=model or os.environ.get("CLAUDE_MODEL", _DEFAULT_MODEL),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return next((b.text for b in response.content if isinstance(b, TextBlock)), "").strip()
