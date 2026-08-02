"""Data contract shared with firmware/src/usage_state.h — keep both in sync.

See docs/handover.md section 4.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# v1: original 26-byte struct. v2: added week_tokens/week_cents.
# v3: added model/effort (read from Claude Code transcripts, not ccusage —
# ccusage drops the effort field during aggregation).
# Bump whenever the struct layout changes so a stale build on either side is
# cleanly rejected by the firmware's version check instead of misparsed.
VERSION = 3

# Custom 128-bit UUIDs, generated once for this project — keep in sync with
# firmware/src/usage_state.h.
SERVICE_UUID = "059b7bd7-0687-434c-bcfb-38f72a72f9a7"
USAGE_STATE_UUID = "ff009db7-f2b8-4df0-b3fa-b1de2c8729ae"
TIME_SYNC_UUID = "3f18f996-262b-4d33-97dc-4b937a151772"

# Little-endian: uint8, uint32 x8, uint8, char[16], char[8] = 58 bytes.
# Matches the firmware's __attribute__((packed)) struct exactly (no padding
# on either side). 58 <= 61 (MTU 64 - 3) so this still fits one ATT write.
_STRUCT_FORMAT = "<BIIIIIIIIB16s8s"
STRUCT_SIZE = struct.calcsize(_STRUCT_FORMAT)
assert STRUCT_SIZE == 58

MODEL_LEN = 16
EFFORT_LEN = 8


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
    block_reset: int
    block_pct: int
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
            self.block_reset,
            self.block_pct,
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
