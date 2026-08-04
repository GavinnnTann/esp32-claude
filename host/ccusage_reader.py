"""Reads ccusage --json output and builds a UsageState.

See docs/handover.md section 6. Two schema gotchas verified against a live
`ccusage 20.0.19` install before writing this — the doc that spec'd this
project flagged that behaviour drifts between releases, and it does, so the
version is now pinned in CCUSAGE_VERSION rather than left to the registry:

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
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from transcript_reader import read_current_model_effort, read_last_activity
from usage_limits import read_usage_limits
from usage_state import VERSION, UsageState


class CcusageError(RuntimeError):
    pass


# Pinned on purpose. Bare `npx ccusage` resolves to whatever the registry
# serves at that moment, and this module runs unattended every few minutes
# from login (install_autostart.ps1), so a bad release would execute as the
# logged-in user with no interaction. The two schema gotchas documented at the
# top of this module were verified against exactly this version, so the pin
# protects the parsing as well as the supply chain.
#
# For stronger integrity than a version pin, add a package.json plus lockfile
# and run the local binary; that trades a setup step for hash verification.
CCUSAGE_VERSION = "20.0.19"

_npx_path: str | None = None


def _resolve_npx() -> str:
    """Absolute path to npx, resolved from ABSOLUTE PATH entries only.

    Deliberately not shutil.which(): on Windows it prepends the current
    directory to the search path and will happily return `.\\npx.CMD`. Verified
    on 3.11.9 - it does this even when handed an explicit `path=` argument.

    That matters here more than it usually would. install_autostart.ps1 sets
    the shortcut's working directory to this repo, which lives in a synced
    OneDrive folder, so anything able to write a file called npx.cmd there
    would get executed at every login. Relative PATH entries - including the
    empty string, which means "current directory" - are the whole vector, so
    they are skipped rather than searched.
    """
    exts = [e for e in os.environ.get("PATHEXT", "").split(os.pathsep) if e]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        base = os.path.join(entry, "npx")
        # PATHEXT candidates BEFORE the bare name. Node ships both `npx.cmd`
        # and an extensionless `npx` (a POSIX sh script for Git Bash) in the
        # same directory on Windows, and only the .cmd is executable by
        # CreateProcess - preferring the bare name picks the one that cannot
        # run. On POSIX, PATHEXT is empty and this falls through to `base`.
        for cand in [base + e for e in exts] + [base]:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
    raise CcusageError("npx not found in any absolute PATH entry - is Node.js installed?")


def _npx() -> str:
    """Resolved once; the answer cannot change without the process restarting."""
    global _npx_path
    if _npx_path is None:
        _npx_path = _resolve_npx()
    return _npx_path


# Suppresses the console window CreateProcess would otherwise open. Windows
# only, 0 elsewhere so the call below stays portable.
#
# Needed because dropping shell=True lost something that was never obvious:
# CPython sets STARTF_USESHOWWINDOW/SW_HIDE *only* on the shell=True path, so
# the old code was hiding its console by accident. npx is a .cmd, which
# CreateProcess runs by launching cmd.exe regardless of shell=, and under
# pythonw.exe - which has no console to inherit - each of those got a visible
# window. Three per refresh, every five minutes, popping up over whatever the
# user was doing.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _invoke(subcommand: str, offline: bool) -> dict:
    # A list with shell=False. cmd.exe is still involved - npx is a batch file
    # - but the path handed to it is ABSOLUTE, which is the property that
    # matters: nothing is resolved against the current directory. `--yes`
    # because an unattended run must not block on npx's install prompt.
    cmd = [_npx(), "--yes", f"ccusage@{CCUSAGE_VERSION}", subcommand, "--json"]
    if offline:
        cmd.append("--offline")
    result = subprocess.run(cmd, capture_output=True, text=True,
                            creationflags=_NO_WINDOW)
    shown = " ".join(cmd)
    if result.returncode != 0:
        raise CcusageError(f"`{shown}` failed (exit {result.returncode}): {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise CcusageError(f"`{shown}` did not return valid JSON: {e}") from e


def _run_ccusage(subcommand: str) -> dict:
    """Live pricing by preference, bundled pricing as a fallback.

    `--offline` used to be unconditional, and it silently zeroed the money.
    Its bundled price table predates claude-opus-5, so a day spent entirely on
    that model reported 19,950,033 tokens at a cost of $0.00 while ccusage's
    own grand total was $1332.98. Dropping the flag prices the same day at
    $17.86. Verified identical on 20.0.19 and on latest, so this was never
    about the pinned version.

    Falling back rather than just removing the flag: live pricing is a network
    fetch, and on a machine that is offline the token counts are still worth
    having even when the cost beside them is stale.
    """
    try:
        return _invoke(subcommand, offline=False)
    except CcusageError:
        return _invoke(subcommand, offline=True)


def _sum_four(entry: dict, keys: tuple[str, str, str, str]) -> int:
    return sum(entry.get(k, 0) for k in keys)


_FLAT_TOKEN_KEYS = ("inputTokens", "outputTokens", "cacheCreationTokens", "cacheReadTokens")

# ccusage runs are slow: three `npx` subprocess spawns that each re-scan every
# transcript. Measured at ~130s PER CALL against a large history, so a full
# refresh is minutes, not the "several seconds" this comment used to claim.
# The quota percentages, by contrast, come from a single local JSON read
# costing microseconds. Caching the ccusage half lets the caller poll
# frequently for fresh percentages without paying for subprocesses every time.
_CCUSAGE_TTL_S = 300
_ccusage_cache: dict | None = None
_ccusage_cache_at: float = 0.0

# Guards the two above. A refresh now runs on its own thread, so the poll loop
# and the refresher genuinely touch these concurrently.
_ccusage_lock = threading.Lock()
_ccusage_refreshing = False

# Served until the first refresh lands. Indistinguishable on the wire from a
# genuine zero, which is acceptable here: a fresh start with no usage recorded
# yet really is zero, and the quota arc - the number that matters - is correct
# from the first push regardless.
_ZERO_TOTALS = {
    "day_tokens": 0,
    "day_cents": 0,
    "week_tokens": 0,
    "week_cents": 0,
    "block_tokens": 0,
    "block_cents": 0,
}


def _fetch_totals() -> dict:
    """The expensive part. Three subprocess spawns, minutes on a large history."""
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

    return {
        "day_tokens": day_tokens,
        "day_cents": day_cents,
        "week_tokens": week_tokens,
        "week_cents": week_cents,
        "block_tokens": block_tokens,
        "block_cents": block_cents,
    }


def _store(totals: dict) -> None:
    global _ccusage_cache, _ccusage_cache_at
    with _ccusage_lock:
        _ccusage_cache = totals
        _ccusage_cache_at = time.monotonic()


def _refresh_in_background() -> None:
    global _ccusage_refreshing
    try:
        _store(_fetch_totals())
    except Exception as e:
        # A failed refresh keeps the previous figures rather than taking the
        # display down; the next expiry simply tries again. Broad by intent -
        # this runs on a thread with nobody to propagate to.
        print(f"[ccusage] background refresh failed, keeping previous totals: {e}")
    finally:
        with _ccusage_lock:
            _ccusage_refreshing = False


def _ccusage_totals() -> dict:
    """Last known token/cost totals, refreshed off-thread. Never blocks.

    Stale-while-revalidate rather than refresh-on-demand. The old version
    recomputed inline the moment the TTL expired, and since read_usage_state()
    is called from the BLE poll loop, every expiry stopped the display dead for
    the duration - host.log showed repeated 11.5 MINUTE gaps between pushes
    against a 300s TTL, so the loop spent more time blocked than running.

    The first call does NOT block either, which matters more than it looks:
    esp32-claude.py calls read_usage_state() once before starting the BLE loop,
    so a blocking first fetch delays the CONNECTION by minutes and the display
    sits on "waiting to connect" the whole time. Returning zeros immediately
    gets the link up in seconds instead.

    Zeros are the right trade because of WHICH numbers these are. Token and
    cost totals are a secondary view and already up to 5 minutes stale by
    design; the quota percentages people actually watch come from
    usage_limits.py on every call and never touch this path. So the arc is
    correct within seconds of startup and only the token counters lag.
    """
    global _ccusage_refreshing
    with _ccusage_lock:
        cached = _ccusage_cache
        fresh = cached is not None and (time.monotonic() - _ccusage_cache_at) < _CCUSAGE_TTL_S
        # Only one refresh in flight; a second would just duplicate the work.
        start = not fresh and not _ccusage_refreshing
        if start:
            _ccusage_refreshing = True

    if start:
        threading.Thread(target=_refresh_in_background, name="ccusage-refresh",
                         daemon=True).start()
    return cached if cached is not None else _ZERO_TOTALS


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
