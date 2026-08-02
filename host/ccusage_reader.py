"""Reads ccusage --json output and builds a UsageState.

See docs/handover.md section 6. Two schema gotchas verified against a live
`ccusage 20.0.19` install before writing this (pin this version — the doc
that spec'd this project already flagged that behaviour drifts between
releases, and it does):

- `daily` entries are flat (inputTokens, outputTokens, cacheCreationTokens,
  cacheReadTokens); `blocks[].tokenCounts` nests the same four counters under
  *different* key names (cacheCreationInputTokens, cacheReadInputTokens).
- The doc assumed `blocks[].totalTokens` counts only inputTokens+outputTokens.
  On this ccusage version it actually equals the full four-way sum, same as
  `daily`. Rather than depend on what `totalTokens` happens to mean in a given
  release, both paths below sum the four raw counters explicitly.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone

from transcript_reader import read_current_model_effort, read_last_activity
from usage_limits import read_usage_limits
from usage_state import VERSION, UsageState


class CcusageError(RuntimeError):
    pass


def _run_ccusage(subcommand: str) -> dict:
    # shell=True so `npx` resolves via PATHEXT (npx.cmd) on Windows without
    # needing to hardcode an extension; subcommand is a fixed literal from
    # this module, never user input, so this isn't an injection risk.
    cmd = f"npx ccusage {subcommand} --json --offline"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        raise CcusageError(f"`{cmd}` failed (exit {result.returncode}): {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise CcusageError(f"`{cmd}` did not return valid JSON: {e}") from e


def _sum_four(entry: dict, keys: tuple[str, str, str, str]) -> int:
    return sum(entry.get(k, 0) for k in keys)


_FLAT_TOKEN_KEYS = ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens")

# ccusage runs are slow: three `npx` subprocess spawns that each re-scan every
# transcript, several seconds total. The quota percentages, by contrast, come
# from a single local JSON read costing microseconds. Caching the ccusage half
# lets the caller poll frequently for fresh percentages without paying for
# subprocesses every time.
_CCUSAGE_TTL_S = 300
_ccusage_cache: dict | None = None
_ccusage_cache_at: float = 0.0


def _ccusage_totals() -> dict:
    """Token/cost totals from ccusage, cached for _CCUSAGE_TTL_S."""
    global _ccusage_cache, _ccusage_cache_at
    if _ccusage_cache is not None and (time.monotonic() - _ccusage_cache_at) < _CCUSAGE_TTL_S:
        return _ccusage_cache

    daily = _run_ccusage("daily")
    weekly = _run_ccusage("weekly")
    blocks = _run_ccusage("blocks")

    today = datetime.now().strftime("%Y-%m-%d")
    today_entry = next((d for d in daily.get("daily", []) if d.get("period") == today), None)

    if today_entry:
        day_tokens = _sum_four(today_entry, _FLAT_TOKEN_KEYS)
        day_cents = round(today_entry.get("totalCost", 0.0) * 100)
    else:
        # No usage recorded yet today — genuinely zero, not an error.
        day_tokens = 0
        day_cents = 0

    # `weekly` entries use the same flat schema as `daily` (verified against
    # ccusage 20.0.19). Take the last (most recent) week bucket rather than
    # trying to match today's date against ccusage's week-start convention
    # (Mon vs Sun) — self-corrects the moment any usage lands in a new week.
    week_entries = weekly.get("weekly", [])
    if week_entries:
        week_entry = week_entries[-1]
        week_tokens = _sum_four(week_entry, _FLAT_TOKEN_KEYS)
        week_cents = round(week_entry.get("totalCost", 0.0) * 100)
    else:
        week_tokens = 0
        week_cents = 0

    active_block = next((b for b in blocks.get("blocks", []) if b.get("isActive")), None)

    if active_block:
        counts = active_block.get("tokenCounts", {})
        block_tokens = _sum_four(
            counts, ("inputTokens", "outputTokens", "cacheCreationInputTokens", "cacheReadInputTokens")
        )
        block_cents = round(active_block.get("costUSD", 0.0) * 100)
    else:
        # Outside any 5-hour session window right now — also not an error.
        block_tokens = 0
        block_cents = 0

    _ccusage_cache = {
        "day_tokens": day_tokens,
        "day_cents": day_cents,
        "week_tokens": week_tokens,
        "week_cents": week_cents,
        "block_tokens": block_tokens,
        "block_cents": block_cents,
    }
    _ccusage_cache_at = time.monotonic()
    return _ccusage_cache


def read_usage_state() -> UsageState:
    """Build the current UsageState from ccusage plus two non-ccusage sources.

    Token/cost totals come from `ccusage daily`, `weekly`, and `blocks`, cached
    for 5 minutes because those are slow subprocess calls. The quota
    *percentages* are re-read every call: they come from Claude Code's own
    cache in `~/.claude.json` via `usage_limits.py`, which is a cheap local
    file read, and they're what the user actually watches change. Model and
    effort likewise come from `transcript_reader.py`, also cheap.

    So calling this often is fine — it only pays for ccusage every 5 minutes.
    """
    totals = _ccusage_totals()

    # Not from ccusage — it drops `effort` entirely during aggregation, so
    # these come straight from Claude Code's transcripts.
    model, effort = read_current_model_effort()

    # Also not from ccusage: the real quota percentages and reset instants.
    # Note these reset times are the actual quota boundaries, which differ
    # from ccusage's rolling 5-hour block `endTime` (seen 12:00 vs 12:20 SGT).
    limits = read_usage_limits()

    return UsageState(
        version=VERSION,
        ts=int(datetime.now(timezone.utc).timestamp()),
        model=model,
        effort=effort,
        session_reset=limits.session_reset,
        week_reset=limits.week_reset,
        limits_fetched=limits.fetched_at,
        last_activity=read_last_activity(),
        session_pct=limits.session_pct,
        week_pct=limits.week_pct,
        limits_ok=1 if limits.ok else 0,
        **totals,
    )
