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

There are now TWO sources for the same numbers, and this module returns
whichever was fetched more recently:

1. `~/.claude.json` - always present, but a cache Claude Code refreshes on its
   own schedule. That schedule is irregular rather than merely slow: measured
   at 142s old at one moment and 47 MINUTES stale at another, mid-session.
2. `%LOCALAPPDATA%/esp32-claude/rate_limits.json` - written by the status line
   script (host/statusline_usage.py), which Claude Code re-runs every few
   seconds and hands the live figures. Only exists if that is installed, and
   only updates while a session is open.

Preferring the fresher of the two means installing the status line is a pure
improvement and removing it degrades gracefully, with no flag to keep in sync.

Two more things to keep in mind:

- **Neither source is live.** `fetched_at` says when the numbers were last
  refreshed; if Claude Code isn't running, they freeze. We pass that timestamp
  through so the display can flag stale percentages rather than showing an old
  number as if it were current.
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

# Kept outside the repo deliberately - see the note in statusline_usage.py
# about OneDrive taking exclusive locks on files it is syncing.
STATUSLINE_JSON = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "esp32-claude", "rate_limits.json")


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


@dataclass
class _Source:
    """One reading, with each window independently present or absent.

    Windows are tracked separately because Claude Code documents that
    `five_hour` and `seven_day` may each be absent from the status line
    payload. Treating a source as all-or-nothing meant a file carrying only
    the weekly window would win the freshness comparison outright and report
    session_pct=0 - overwriting a perfectly good cached session figure with a
    number that means "spent nothing", which is the most misleading direction
    to be wrong in.
    """
    session: Optional[tuple] = None  # (pct, reset_epoch)
    week: Optional[tuple] = None
    fetched_at: int = 0


def _read_statusline() -> _Source:
    """Figures published by the status line script, if it is installed.

    Shape differs from ~/.claude.json in two ways that matter: the key is
    `pct` not `utilization`, and `resets_at` is already epoch seconds rather
    than ISO 8601.
    """
    try:
        with open(STATUSLINE_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _Source()

    def win(node):
        if not isinstance(node, dict) or not isinstance(node.get("pct"), (int, float)):
            return None
        resets = node.get("resets_at")
        return (max(0, min(100, int(node["pct"]))),
                int(resets) if isinstance(resets, (int, float)) else 0)

    # TypeError/ValueError as well as the read errors above: this file is
    # written by our own status line script, but "our own script wrote it" is
    # not a guarantee of shape - a half-written or hand-edited file must
    # degrade to "no reading" rather than take the poll loop down with it.
    try:
        fetched = data.get("fetched_at")
        return _Source(
            session=win(data.get("five_hour")),
            week=win(data.get("seven_day")),
            fetched_at=int(fetched) if isinstance(fetched, (int, float)) else 0,
        )
    except (TypeError, ValueError):
        return _Source()


def read_usage_limits() -> UsageLimits:
    """Returns the freshest quota utilization available, window by window.

    Falls back cleanly: if the status line is not installed, or has not seen
    an API response yet this session, its file is absent or empty and the
    ~/.claude.json cache is used unchanged.
    """
    live = _read_statusline()
    cached = _read_claude_json_source()

    def pick(a: Optional[tuple], a_at: int, b: Optional[tuple], b_at: int):
        """Fresher of two readings of the SAME window; whichever exists if one does."""
        if a is None:
            return b, b_at
        if b is None:
            return a, a_at
        return (a, a_at) if a_at >= b_at else (b, b_at)

    session, s_at = pick(live.session, live.fetched_at, cached.session, cached.fetched_at)
    week, w_at = pick(live.week, live.fetched_at, cached.week, cached.fetched_at)

    if session is None and week is None:
        return UsageLimits()

    return UsageLimits(
        session_pct=session[0] if session else 0,
        week_pct=week[0] if week else 0,
        session_reset=session[1] if session else 0,
        week_reset=week[1] if week else 0,
        # The OLDER of the two, so "how stale is this reading" never overstates
        # freshness for the pair the display shows together.
        fetched_at=min(x for x in (s_at, w_at) if x) if (s_at or w_at) else 0,
        ok=True,
    )


def _read_claude_json_source() -> _Source:
    """Returns the cached quota utilization, or an empty source.

    A missing or malformed cache is not worth crashing the poll loop over —
    the display just shows the percentages as unavailable.
    """
    try:
        with open(CLAUDE_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _Source()

    cached = data.get("cachedUsageUtilization")
    if not isinstance(cached, dict):
        return _Source()
    util = cached.get("utilization")
    if not isinstance(util, dict):
        return _Source()

    def win(node):
        if not isinstance(node, dict) or not isinstance(node.get("utilization"), (int, float)):
            return None
        return (_pct(node), _parse_iso(node.get("resets_at")))

    return _Source(
        session=win(util.get("five_hour")),
        week=win(util.get("seven_day")),
        fetched_at=int(cached.get("fetchedAtMs", 0) / 1000),
    )
