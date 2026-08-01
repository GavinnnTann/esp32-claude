"""Data contract shared with firmware/src/usage_state.h — keep both in sync.

See docs/handover.md section 4.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# v1 was the original 26-byte struct (no week_tokens/week_cents). Bumped so a
# stale host build talking to new firmware (or vice versa) gets cleanly
# rejected by the version check in firmware/src/ble_server.cpp instead of
# being misparsed.
VERSION = 2

# Custom 128-bit UUIDs, generated once for this project — keep in sync with
# firmware/src/usage_state.h.
SERVICE_UUID = "059b7bd7-0687-434c-bcfb-38f72a72f9a7"
USAGE_STATE_UUID = "ff009db7-f2b8-4df0-b3fa-b1de2c8729ae"
TIME_SYNC_UUID = "3f18f996-262b-4d33-97dc-4b937a151772"

# Little-endian: uint8, uint32 x8, uint8 = 34 bytes. Matches the firmware's
# __attribute__((packed)) struct exactly (no padding on either side).
_STRUCT_FORMAT = "<BIIIIIIIIB"
STRUCT_SIZE = struct.calcsize(_STRUCT_FORMAT)
assert STRUCT_SIZE == 34


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
        )

    @classmethod
    def unpack(cls, data: bytes) -> "UsageState":
        return cls(*struct.unpack(_STRUCT_FORMAT, data))
