#pragma once

#include <stdint.h>

// Data contract shared with host/usage_state.py — keep both in sync.
// See docs/handover.md section 4.
//
// Size note: 68 bytes. Needs an ATT write payload of MTU-3, so the device
// requests MTU 247 (see ble_server.cpp). The negotiated MTU is printed at
// connect time — if it ever comes back below 71, writes silently fall back
// to the stack's queued/long-write path.
struct __attribute__((packed)) UsageState {
  uint8_t version;          // 4
  uint32_t ts;               // epoch seconds, UTC — when host built this
  uint32_t day_tokens;
  uint32_t day_cents;
  uint32_t week_tokens;
  uint32_t week_cents;
  uint32_t block_tokens;
  uint32_t block_cents;
  uint32_t session_reset;    // epoch UTC — REAL quota reset, not ccusage's block boundary
  uint32_t week_reset;       // epoch UTC
  uint32_t limits_fetched;   // epoch UTC — when Claude Code last refreshed the % cache
  uint8_t session_pct;       // 0-100, real "Session (5hr)" figure
  uint8_t week_pct;          // 0-100, real "Weekly (7 day)" figure
  uint8_t limits_ok;         // 0 = cache unavailable, percentages are meaningless
  char model[16];            // e.g. "sonnet-5" — NOT null-terminated if exactly 16 chars
  char effort[8];            // e.g. "xhigh"   — same caveat
};                           // 68 bytes, little-endian

// v1: original 26-byte struct. v2: +week_tokens/week_cents.
// v3: +model/effort (read from Claude Code transcripts — ccusage drops effort).
// v4: +real quota percentages and reset instants, read from
//     ~/.claude.json's cachedUsageUtilization. This replaced the
//     guess-a-token-ceiling approach entirely: handover.md #4 assumed nothing
//     exposed the plan limit, but Claude Code caches the server's own figures.
//     `block_reset` (ccusage's 5h block boundary) became `session_reset` (the
//     real reset) — they genuinely differ, e.g. 12:00 vs 12:20 SGT.
// Bump this on every layout change so a stale build on either side is cleanly
// rejected by ble_server.cpp's version check, not misparsed.
static const uint8_t USAGE_STATE_VERSION = 4;

// Custom 128-bit UUIDs, generated once for this project — keep in sync with host/usage_state.py.
#define SERVICE_UUID     "059b7bd7-0687-434c-bcfb-38f72a72f9a7"
#define USAGE_STATE_UUID "ff009db7-f2b8-4df0-b3fa-b1de2c8729ae"
#define TIME_SYNC_UUID   "3f18f996-262b-4d33-97dc-4b937a151772"
