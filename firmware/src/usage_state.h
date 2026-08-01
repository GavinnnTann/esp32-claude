#pragma once

#include <stdint.h>

// Data contract shared with host/usage_state.py — keep both in sync.
// See docs/handover.md section 4.
struct __attribute__((packed)) UsageState {
  uint8_t version;       // 1
  uint32_t ts;            // epoch seconds, UTC — when host built this
  uint32_t day_tokens;
  uint32_t day_cents;
  uint32_t block_tokens;
  uint32_t block_cents;
  uint32_t block_reset;   // epoch seconds, UTC
  uint8_t block_pct;      // 0-100, calibrated against a user-configured ceiling (see handover.md)
};                        // 26 bytes, little-endian

static const uint8_t USAGE_STATE_VERSION = 1;

// Custom 128-bit UUIDs, generated once for this project — keep in sync with host/usage_state.py.
#define SERVICE_UUID     "059b7bd7-0687-434c-bcfb-38f72a72f9a7"
#define USAGE_STATE_UUID "ff009db7-f2b8-4df0-b3fa-b1de2c8729ae"
#define TIME_SYNC_UUID   "3f18f996-262b-4d33-97dc-4b937a151772"
