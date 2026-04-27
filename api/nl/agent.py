"""Claude-powered agentic loop for the NL query interface (F7).

The agent receives a natural-language HR question, decides which org-graph
tools to call (tool_use), executes them against the live DB, and returns a
plain-language answer with a trace of every tool invocation.

Design constraints:
  - Sync client (anthropic.Anthropic) — the FastAPI route runs it in a
    thread pool via asyncio.to_thread so we don't block the event loop.
  - Max 6 agent turns (tool call rounds) to prevent runaway billing.
  - Each tool result is appended as a "tool" role message before the next
    model call — standard Anthropic multi-turn tool_use protocol.
  - No retries on API errors — propagate as HTTPException to caller.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import anthropic

from api.nl.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
_MAX_TURNS = int(os.environ.get("NL_MAX_TURNS", "6"))

_SYSTEM_PROMPT = """\
You are an HR intelligence assistant for Organizational Synapse, a platform \
that analyses collaboration metadata to surface organisational risk.

You have access to tools that query a live graph database. Use them to answer \
the user's question accurately. Rules:
1. Always call search_employees first when the user mentions a person by name.
2. Combine multiple tools when needed (e.g. risk scores + knowledge scores).
3. Keep answers concise — two to four sentences unless detail is requested.
4. Round numeric values to two decimal places in your answer.
5. Never reveal raw UUIDs unless the user explicitly asks.
6. If a tool returns an "error" key, explain what data is missing and how to \
   fix it (e.g. "run the succession_dag Airflow pipeline first").
7. Respond in the same language as the user's question.
"""


def run_query(question: str, conn) -> dict[str, Any]:
    """Execute the agentic loop for *question* using *conn* for DB access.

    Returns:
        {
            "answer": str,
            "tools_used": [{"name": str, "input": dict, "result_summary": str}],
            "model": str,
            "turns": int,
            "latency_ms": int,
        }
    """
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": question}]
    tools_used: list[dict] = []
    turn = 0
    t0 = time.monotonic()

    while turn < _MAX_TURNS:
        turn += 1
        response = client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        # Append assistant message (may contain tool_use blocks)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Extract text answer from the final response
            answer = _extract_text(response.content)
            break

        if response.stop_reason == "tool_use":
            # Execute every requested tool and collect results
            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input or {}
                logger.info("NL agent calling tool: %s input=%s", tool_name, tool_input)

                result = execute_tool(tool_name, tool_input, conn)
                result_summary = _summarise(result)
                tools_used.append(
                    {"name": tool_name, "input": tool_input, "result_summary": result_summary}
                )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _serialise(result),
                    }
                )

            messages.append({"role": "user", "content": tool_results})
            continue  # Let Claude process the tool results

        # Unexpected stop reason — treat as end
        answer = _extract_text(response.content) or f"(stop_reason={response.stop_reason})"
        break
    else:
        answer = "The query required too many reasoning steps. Please narrow your question."

    latency_ms = int((time.monotonic() - t0) * 1000)
    return {
        "answer": answer,
        "tools_used": tools_used,
        "model": _MODEL,
        "turns": turn,
        "latency_ms": latency_ms,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _extract_text(content: list) -> str:
    """Return concatenated text from all TextBlock items in *content*."""
    parts = []
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return " ".join(parts).strip()


def _summarise(result: dict) -> str:
    """One-line summary of a tool result for the tools_used trace."""
    if "error" in result:
        return f"error: {result['error']}"
    keys = [k for k in result if k not in ("error",)]
    if not keys:
        return "empty result"
    # Show first meaningful key and length hint
    first = keys[0]
    val = result[first]
    if isinstance(val, list):
        return f"{first}: {len(val)} items"
    return f"{first}: {str(val)[:80]}"


def _serialise(obj: Any) -> str:
    """Convert a dict to a JSON string for the tool_result content field."""
    import json

    def _default(o: Any) -> str:
        from datetime import date, datetime
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return str(o)

    return json.dumps(obj, default=_default)
