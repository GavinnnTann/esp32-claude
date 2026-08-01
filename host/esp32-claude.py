"""Entry point: reads ccusage on a timer and pushes it to the esp32-claude
desk display over BLE. See docs/handover.md.

Usage: python esp32-claude.py
"""

from __future__ import annotations

import asyncio
import sys

import ble_client
import config
from ccusage_reader import read_usage_state

# Force line-buffered stdout: when this runs unattended (piped to a log file,
# or under Task Scheduler) Python otherwise fully block-buffers stdout, so log
# lines sit invisible in memory for a long time instead of showing up as they happen.
sys.stdout.reconfigure(line_buffering=True)


def get_state():
    return read_usage_state(config.BLOCK_TOKEN_CEILING)


def main() -> None:
    if config.BLOCK_TOKEN_CEILING is None:
        print("[config] BLOCK_TOKEN_CEILING is not set — block_pct will always read 0. See host/config.py.")
    asyncio.run(ble_client.run_forever(config.POLL_INTERVAL_S, get_state))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
