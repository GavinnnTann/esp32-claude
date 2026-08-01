"""Local calibration config for esp32-claude. See docs/handover.md section 4,
"block_pct needs calibration" — ccusage cannot tell you your plan's block
limit, Anthropic doesn't expose it anywhere.

Leave BLOCK_TOKEN_CEILING as None until you've actually hit a rate limit and
noted the block_tokens value logged by this script at that moment, then set
it here. Until calibrated, block_pct always reads 0 and the arc gauge stays
empty rather than showing a made-up number.
"""

BLOCK_TOKEN_CEILING = None  # e.g. 19_000_000 — set after calibrating against a real limit

# docs/handover.md section 5: host polls/notifies every 5 minutes.
POLL_INTERVAL_S = 5 * 60
