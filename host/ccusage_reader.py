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
from datetime import datetime, timezone
from typing import Optional

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


def _parse_iso(ts: str) -> int:
    # ccusage timestamps look like "2026-08-01T20:00:00.000Z".
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def _sum_four(entry: dict, keys: tuple[str, str, str, str]) -> int:
    return sum(entry.get(k, 0) for k in keys)


_FLAT_TOKEN_KEYS = ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens")


def read_usage_state(block_token_ceiling: Optional[int]) -> UsageState:
    """Build the current UsageState from `ccusage daily`, `weekly`, and `blocks`.

    `block_token_ceiling` is the user-calibrated token count that represents
    100% of a 5-hour block (see docs/handover.md section 4, "block_pct needs
    calibration"). Pass None until it's been calibrated; block_pct will read 0.
    """
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
        block_reset = _parse_iso(active_block["endTime"])
    else:
        # Outside any 5-hour session window right now — also not an error.
        block_tokens = 0
        block_cents = 0
        block_reset = 0

    if block_token_ceiling:
        block_pct = min(100, round(block_tokens / block_token_ceiling * 100))
    else:
        block_pct = 0

    return UsageState(
        version=VERSION,
        ts=int(datetime.now(timezone.utc).timestamp()),
        day_tokens=day_tokens,
        day_cents=day_cents,
        week_tokens=week_tokens,
        week_cents=week_cents,
        block_tokens=block_tokens,
        block_cents=block_cents,
        block_reset=block_reset,
        block_pct=block_pct,
    )
