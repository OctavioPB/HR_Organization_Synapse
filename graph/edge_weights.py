"""Channel × direction semantic edge weighting (MODEL.md §2.2, MATH_MODEL.md §2).

    w(s, t) = Σ_e  weight(e) · ω_channel(channel(e)) · ω_direction(direction(e))

A code review carries more tacit knowledge than a Slack @mention; a calendar
invite (synchronous time together) more than an email.  Until this module
existed the documented CHANNEL_WEIGHT_* / DIRECTION_WEIGHT_* variables were
read nowhere and every interaction counted as 1.0.

Every SQL loader that turns raw_events into graph edges must use
``weighted_edge_sql()`` so all metrics see the same graph.  Set
``CHANNEL_WEIGHTING=off`` to fall back to raw event weights (useful for A/B
comparison and for reproducing pre-weighting scores).

Hybrid/remote orgs: MODEL.md §2.2.7 recommends lowering CHANNEL_WEIGHT_CALENDAR
to 1.8–2.0 when more than half the workforce is remote.
"""

from __future__ import annotations

import os

# Channel and direction vocabularies are fixed by ingestion/schemas/collaboration_event.py.
_CHANNEL_DEFAULTS: dict[str, float] = {
    "github": 3.0,
    "calendar": 2.5,
    "jira": 1.5,
    "email": 1.0,
    "slack": 1.0,
    "teams": 1.0,
}
_DIRECTION_DEFAULTS: dict[str, float] = {
    "reviewed": 1.5,
    "assigned": 1.2,
    "invited": 1.0,
    "mentioned": 0.8,
    "sent": 0.7,
}


def _read(prefix: str, defaults: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, default in defaults.items():
        value = float(os.environ.get(f"{prefix}{key.upper()}", str(default)))
        if value < 0:
            raise ValueError(f"{prefix}{key.upper()} must be >= 0, got {value}")
        out[key] = value
    return out


CHANNEL_WEIGHTS: dict[str, float] = _read("CHANNEL_WEIGHT_", _CHANNEL_DEFAULTS)
DIRECTION_WEIGHTS: dict[str, float] = _read("DIRECTION_WEIGHT_", _DIRECTION_DEFAULTS)
ENABLED: bool = os.environ.get("CHANNEL_WEIGHTING", "on").strip().lower() not in {"off", "0", "false", "no"}


def omega(channel: str | None, direction: str | None) -> float:
    """Semantic multiplier ω(channel, direction).  Unknown values count as 1.0."""
    if not ENABLED:
        return 1.0
    return CHANNEL_WEIGHTS.get(channel or "", 1.0) * DIRECTION_WEIGHTS.get(direction or "", 1.0)


def _case(column: str, weights: dict[str, float]) -> str:
    # Keys are fixed literals from this module and values are parsed floats, so
    # inlining them is injection-safe; it keeps every caller's parameter list unchanged.
    whens = " ".join(f"WHEN '{k}' THEN {float(v)!r}" for k, v in weights.items())
    return f"(CASE {column} {whens} ELSE 1.0 END)"


def weighted_edge_sql(alias: str = "re") -> str:
    """SQL expression for one event's semantic weight, for use inside SELECT / SUM().

    Example:
        f"SELECT re.source_id, re.target_id, {weighted_edge_sql('re')} AS weight FROM raw_events re"
    """
    if not alias.isidentifier():
        raise ValueError(f"invalid SQL alias: {alias!r}")
    if not ENABLED:
        return f"{alias}.weight"
    return (
        f"({alias}.weight * {_case(f'{alias}.channel', CHANNEL_WEIGHTS)}"
        f" * {_case(f'{alias}.direction', DIRECTION_WEIGHTS)})"
    )
