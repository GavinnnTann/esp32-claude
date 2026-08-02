"""Data contract shared with firmware/src/usage_state.h — keep both in sync.

See docs/handover.md section 4.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# v1: original 26-byte struct. v2: +week_tokens/week_cents.
# v3: +model/effort (read from Claude Code transcripts — ccusage drops effort).
# v4: +real quota percentages and reset instants from ~/.claude.json's
#     cachedUsageUtilization, replacing the guess-a-token-ceiling approach.
# v5: +last_activity (epoch UTC of the newest transcript's mtime), so the
#     display can tell "Claude is waiting for you" from "Claude is working".
# Bump on every layout change so a stale build on either side is cleanly
# rejected by the firmware's version check instead of misparsed.
VERSION = 5

# Custom 128-bit UUIDs, generated once for this project — keep in sync with
# firmware/src/usage_state.h.
SERVICE_UUID = "059b7bd7-0687-434c-bcfb-38f72a72f9a7"
USAGE_STATE_UUID = "ff009db7-f2b8-4df0-b3fa-b1de2c8729ae"
TIME_SYNC_UUID = "3f18f996-262b-4d33-97dc-4b937a151772"

# Little-endian: uint8, uint32 x11, uint8 x3, char[16], char[8] = 72 bytes.
# Matches the firmware's __attribute__((packed)) struct exactly.
# Built from counts rather than a literal run of "I"s — hand-typing them is
# exactly how this silently went one field short during the v4 change.
MODEL_LEN = 16
EFFORT_LEN = 8
_U32_FIELDS = 11  # ts, day_tokens, day_cents, week_*, block_*, *_reset, limits_fetched, last_activity
_U8_TAIL_FIELDS = 3  # session_pct, week_pct, limits_ok
_STRUCT_FORMAT = "<B" + "I" * _U32_FIELDS + "B" * _U8_TAIL_FIELDS + f"{MODEL_LEN}s{EFFORT_LEN}s"
STRUCT_SIZE = struct.calcsize(_STRUCT_FORMAT)
assert STRUCT_SIZE == 72, f"struct is {STRUCT_SIZE} bytes, firmware expects 72"


@dataclass
class UsageState:
    version: int
    ts: int
    day_tokens: int
    day_cents: int
    week_tokens: int
    week_cents: int
    block_tokens: int
    block_cents: int
    session_reset: int
    week_reset: int
    limits_fetched: int
    last_activity: int
    session_pct: int
    week_pct: int
    limits_ok: int
    model: str = ""
    effort: str = ""

    def pack(self) -> bytes:
        return struct.pack(
            _STRUCT_FORMAT,
            self.version,
            self.ts,
            self.day_tokens,
            self.day_cents,
            self.week_tokens,
            self.week_cents,
            self.block_tokens,
            self.block_cents,
            self.session_reset,
            self.week_reset,
            self.limits_fetched,
            self.last_activity,
            self.session_pct,
            self.week_pct,
            self.limits_ok,
            # struct's `s` pads with NULs, which is exactly what the firmware's
            # char arrays want; truncate first so a long name can't overflow.
            self.model.encode("ascii", "replace")[:MODEL_LEN],
            self.effort.encode("ascii", "replace")[:EFFORT_LEN],
        )

    @classmethod
    def unpack(cls, data: bytes) -> "UsageState":
        fields = list(struct.unpack(_STRUCT_FORMAT, data))
        fields[-2] = fields[-2].rstrip(b"\x00").decode("ascii", "replace")
        fields[-1] = fields[-1].rstrip(b"\x00").decode("ascii", "replace")
        return cls(*fields)
