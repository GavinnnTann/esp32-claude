#pragma once

#include <stdint.h>

// Data contract shared with host/usage_state.py — keep both in sync.
// See docs/handover.md section 4.
//
// Size note: 58 bytes fits in a single ATT write at the negotiated MTU of 64
// (payload = MTU - 3 = 61). If this struct grows past 61 bytes, writes start
// relying on the stack's queued/long-write path — re-verify on hardware if so.
struct __attribute__((packed)) UsageState {
  uint8_t version;         // 3
  uint32_t ts;              // epoch seconds, UTC — when host built this
  uint32_t day_tokens;
  uint32_t day_cents;
  uint32_t week_tokens;
  uint32_t week_cents;
  uint32_t block_tokens;
  uint32_t block_cents;
  uint32_t block_reset;     // epoch seconds, UTC
  uint8_t block_pct;        // 0-100, calibrated against a user-configured ceiling (see handover.md)
  char model[16];           // e.g. "sonnet-5" — NOT null-terminated if exactly 16 chars
  char effort[8];           // e.g. "xhigh"   — same caveat
};                          // 58 bytes, little-endian

// v1: original 26-byte struct. v2: added week_tokens/week_cents.
// v3: added model/effort (read from Claude Code transcripts, not ccusage —
// ccusage drops the effort field during aggregation).
// Bump this whenever the struct layout changes so a stale build on either
// side is cleanly rejected by ble_server.cpp's version check, not misparsed.
static const uint8_t USAGE_STATE_VERSION = 3;

// Custom 128-bit UUIDs, generated once for this project — keep in sync with host/usage_state.py.
#define SERVICE_UUID     "059b7bd7-0687-434c-bcfb-38f72a72f9a7"
#define USAGE_STATE_UUID "ff009db7-f2b8-4df0-b3fa-b1de2c8729ae"
#define TIME_SYNC_UUID   "3f18f996-262b-4d33-97dc-4b937a151772"
