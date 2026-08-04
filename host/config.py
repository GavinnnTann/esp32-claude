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

# Which device to talk to. Empty means "trust whatever answers to the name",
# which is what this did originally and what most BLE examples do.
#
# The name is not a credential - anything in radio range can advertise as
# "esp32-claude", and the host would then hand it every push: token counts,
# spend, model and effort. Setting this to the board's address pins the
# connection to that one device.
#
# Find it in the host log at connect time:
#     [ble] connected to 08:A6:F7:46:7E:F2, MTU=247
#
# This does not authenticate the link - a determined attacker can spoof an
# address - and it does not stop anyone writing to the display, which needs
# encrypted characteristics in the firmware. It closes the easy half cheaply.
DEVICE_ADDRESS = ""
