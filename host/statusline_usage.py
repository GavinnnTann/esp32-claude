#!/usr/bin/env python3
"""Claude Code status line that doubles as a fresh quota feed for the display.

Claude Code hands status line scripts a JSON blob on stdin containing the same
rate limits `/usage` shows:

    rate_limits.five_hour.used_percentage   0-100
    rate_limits.five_hour.resets_at         Unix epoch SECONDS
    rate_limits.seven_day.*                 same shape

Those come straight off the last API response's `anthropic-ratelimit-unified-*`
headers, not from a cache, which is what makes this path worth having. It also
means they change only when a request completes, NOT on every status line run -
see `_write` for why that distinction decides the `fetched_at` we publish.

This script prints a status line and, as a side effect, writes those numbers to
a file that host/usage_limits.py reads.

WHY THIS EXISTS
---------------
`usage_limits.py` originally had only one source: `cachedUsageUtilization` in
`~/.claude.json`. That is a cache Claude Code refreshes on its own schedule,
and the schedule is irregular rather than merely slow - measured at 142s old at
one moment and 47 MINUTES stale at another, mid-session. Long enough that the
arc could show a comfortable number while the session was nearly spent, which
is the one thing the display exists to prevent.

Status lines re-run on session events plus a `refreshInterval` (minimum 1s), so
this path is as fresh as the display could want.

DELIBERATELY NOT the undocumented `https://api.anthropic.com/api/oauth/usage`
endpoint. That one works with no session open, but it is undocumented and
spends the OAuth token in `~/.claude/.credentials.json`. This uses only what
Claude Code already hands us.

LIMITS worth knowing
--------------------
- `rate_limits` appears only for Pro/Max subscribers, and only after the first
  API response in a session. Every field here is treated as optional.
- It only runs while a session is open. That is fine: with nothing running,
  usage is not changing, and the firmware ages its own quota window from
  `resets_at` regardless.

Install with host/install_statusline.ps1 (which has an -Uninstall flag).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

# Outside the repo ON PURPOSE. The repo lives in OneDrive, whose Files
# On-Demand filter takes a transient exclusive lock while it syncs a file - a
# file rewritten every few seconds there would be a reliable source of
# PermissionError for whichever side lost the race. %LOCALAPPDATA% is local
# only, and already holds host.log.
OUT_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "esp32-claude")
OUT_FILE = os.path.join(OUT_DIR, "rate_limits.json")


def _window(node):
    """Extract one rate-limit window, or None if it isn't usable."""
    if not isinstance(node, dict):
        return None
    pct = node.get("used_percentage")
    if not isinstance(pct, (int, float)):
        return None
    resets = node.get("resets_at")
    return {
        # Round rather than truncate: the wire format is a uint8 percent, and
        # truncating 54.9 to 54 would under-report every single time.
        "pct": max(0, min(100, int(round(pct)))),
        # Already epoch seconds here, unlike ~/.claude.json's ISO 8601.
        "resets_at": int(resets) if isinstance(resets, (int, float)) else 0,
    }


def _prior():
    """Whatever we published last time, or None if there is nothing readable."""
    try:
        with open(OUT_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(five, seven):
    """Atomically publish the newest figures.

    Written to a temp file and renamed, because host/ble_client.py polls this
    on its own timer: a half-written file would parse as corrupt JSON and drop
    a reading for no reason.
    """
    payload = {}
    if five:
        payload["five_hour"] = five
    if seven:
        payload["seven_day"] = seven

    # `fetched_at` has to mean "when these numbers were MEASURED", not "when
    # this script last ran", because usage_limits.py compares it against
    # ~/.claude.json's fetchedAtMs to decide which source wins.
    #
    # Those are very different instants. `rate_limits` is read from the last
    # API response's headers, so it only changes when a request completes -
    # but this script re-runs every refreshInterval (5s) and would restamp the
    # identical numbers as brand new each time. That timestamp would then beat
    # ~/.claude.json unconditionally, including immediately after `/usage`
    # forced a real server refresh, so the display could never be corrected by
    # a fresher source.
    #
    # We cannot see the measurement instant, so infer it: numbers identical to
    # what we last published are not new, and keep their original timestamp.
    # A window's `resets_at` is part of the comparison too - at rollover the
    # percentage can land back on its old value while the window itself is
    # genuinely new.
    prior = _prior()
    carried = (
        prior is not None
        and prior.get("five_hour") == five
        and prior.get("seven_day") == seven
        and isinstance(prior.get("fetched_at"), (int, float))
    )
    # When the numbers do hold steady across a real refresh this understates
    # freshness rather than overstating it, which is the same direction
    # usage_limits.py already errs in when it reports the older of two windows.
    payload["fetched_at"] = int(prior["fetched_at"]) if carried else int(time.time())

    os.makedirs(OUT_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=OUT_DIR, prefix=".rate_limits-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, OUT_FILE)
    except OSError:
        # Never let a failed write break the status line itself.
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _printable(text):
    """Drop control characters before this reaches the terminal.

    What main() prints goes to stdout raw - Claude Code renders it as the
    status line, unescaped. An ESC byte surviving this far would be executed as
    an escape sequence rather than displayed, so anything non-printable is
    removed. Claude Code is the one feeding us, so this is defence in depth
    rather than a known vector, but it is one line and the print is the only
    unescaped path in the project.
    """
    if not text:
        return ""
    return "".join(c for c in str(text) if c.isprintable())


def _bar(pct):
    """Five-cell bar. Cheap to read at a glance, unlike a bare number."""
    filled = int(round(pct / 20.0))
    return "#" * filled + "." * (5 - filled)


def main() -> int:
    # Read bytes and decode with utf-8-SIG, not json.load(sys.stdin).
    # PowerShell prepends a UTF-8 BOM when it pipes to a native command, and a
    # leading \xef\xbb\xbf makes json.load raise - which showed up as the
    # status line silently printing nothing and never writing the file. Claude
    # Code itself feeds clean UTF-8, but being invoked from a shell that adds
    # one is exactly how this gets tested by hand. utf-8-sig strips a BOM if
    # present and is a no-op if not.
    raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0

    rate = data.get("rate_limits")
    rate = rate if isinstance(rate, dict) else {}
    five = _window(rate.get("five_hour"))
    seven = _window(rate.get("seven_day"))

    # Only publish when there is something to publish. Writing an empty file
    # before the session's first API response would look "fresh" to the reader
    # while carrying no numbers, and it would win the freshness comparison
    # against a perfectly good ~/.claude.json.
    if five or seven:
        _write(five, seven)

    model = _printable((data.get("model") or {}).get("display_name") or "claude")
    effort = _printable((data.get("effort") or {}).get("level"))
    ctx = (data.get("context_window") or {}).get("used_percentage")

    parts = [f"{model}/{effort}" if effort else model]
    if five:
        parts.append(f"5h {_bar(five['pct'])} {five['pct']}%")
    if seven:
        parts.append(f"7d {_bar(seven['pct'])} {seven['pct']}%")
    if isinstance(ctx, (int, float)):
        parts.append(f"ctx {int(ctx)}%")

    print("  ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
