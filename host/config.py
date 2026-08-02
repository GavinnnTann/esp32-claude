"""Local config for esp32-claude.

NOTE: `BLOCK_TOKEN_CEILING` used to live here. docs/handover.md section 4
assumed nothing exposed the plan's limit, so block_pct had to be estimated
against a hand-calibrated token ceiling. That turned out to be unnecessary:
Claude Code caches the server's own utilization figures in `~/.claude.json`,
and `host/usage_limits.py` reads them directly. The percentages on the
display are now the same numbers Claude Code's own Account & Usage panel
shows — no calibration, no drift.
"""

# docs/handover.md section 5: host polls/notifies every 5 minutes.
POLL_INTERVAL_S = 5 * 60
