"""Data contract shared with firmware/src/usage_state.h — keep both in sync.

See docs/handover.md section 4.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

VERSION = 1

# Custom 128-bit UUIDs, generated once for this project — keep in sync with
# firmware/src/usage_state.h.
SERVICE_UUID = "059b7bd7-0687-434c-bcfb-38f72a72f9a7"
USAGE_STATE_UUID = "ff009db7-f2b8-4df0-b3fa-b1de2c8729ae"
TIME_SYNC_UUID = "3f18f996-262b-4d33-97dc-4b937a151772"

# Little-endian: uint8, uint32 x6, uint8 = 26 bytes. Matches the firmware's
# __attribute__((packed)) struct exactly (no padding on either side).
_STRUCT_FORMAT = "<BIIIIIIB"
STRUCT_SIZE = struct.calcsize(_STRUCT_FORMAT)
assert STRUCT_SIZE == 26


@dataclass
class UsageState:
    version: int
    ts: int
    day_tokens: int
    day_cents: int
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
            self.block_tokens,
            self.block_cents,
            self.block_reset,
            self.block_pct,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "UsageState":
        return cls(*struct.unpack(_STRUCT_FORMAT, data))
