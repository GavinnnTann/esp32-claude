"""Reads the real quota percentages Claude Code itself displays.

This is the authoritative source for "how much of my limit have I used" and
replaces the guess-a-ceiling approach docs/handover.md section 4 had to fall
back on (it assumed nothing exposed the plan limit — something does).

Claude Code caches the server's answer in `~/.claude.json` under
`cachedUsageUtilization`, the same numbers shown in its Account & Usage panel:

    utilization.five_hour.utilization  -> session %   (the "Session (5hr)" bar)
    utilization.seven_day.utilization  -> weekly %    (the "Weekly (7 day)" bar)
    utilization.limits[].percent       -> same values, per limit kind
    *.resets_at                        -> real reset instants

Two things to keep in mind:

- **It is a cache, not live.** `fetchedAtMs` says when Claude Code last
  refreshed it; if Claude Code isn't running, these numbers freeze. We pass
  that timestamp through so the display can flag stale percentages rather
  than showing an old number as if it were current.
- **The percentages are not a linear function of ccusage's raw token counts.**
  Claude Code weights usage (longer contexts cost more even when cached), so
  deriving % from tokens would drift. Read the real value; don't recompute it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

CLAUDE_JSON = os.path.join(os.path.expanduser("~"), ".claude.json")


@dataclass
class UsageLimits:
    session_pct: int = 0
    week_pct: int = 0
    session_reset: int = 0  # epoch seconds, UTC
    week_reset: int = 0     # epoch seconds, UTC
    fetched_at: int = 0     # epoch seconds, UTC — when Claude Code last refreshed
    ok: bool = False        # False if the cache was missing/unreadable


def _parse_iso(ts: Optional[str]) -> int:
    if not ts:
        return 0
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _pct(node: Optional[dict]) -> int:
    if not isinstance(node, dict):
        return 0
    value = node.get("utilization")
    if not isinstance(value, (int, float)):
        return 0
    return max(0, min(100, int(value)))


def read_usage_limits() -> UsageLimits:
    """Returns the cached quota utilization, or an all-zero `ok=False` result.

    A missing or malformed cache is not worth crashing the poll loop over —
    the display just shows the percentages as unavailable.
    """
    try:
        with open(CLAUDE_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return UsageLimits()

    cached = data.get("cachedUsageUtilization")
    if not isinstance(cached, dict):
        return UsageLimits()
    util = cached.get("utilization")
    if not isinstance(util, dict):
        return UsageLimits()

    five_hour = util.get("five_hour")
    seven_day = util.get("seven_day")

    return UsageLimits(
        session_pct=_pct(five_hour),
        week_pct=_pct(seven_day),
        session_reset=_parse_iso((five_hour or {}).get("resets_at")),
        week_reset=_parse_iso((seven_day or {}).get("resets_at")),
        fetched_at=int(cached.get("fetchedAtMs", 0) / 1000),
        ok=True,
    )
