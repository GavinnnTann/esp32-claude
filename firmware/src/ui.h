#pragma once

#include <stdint.h>

enum class ConnState { Fresh, Stale, Disconnected };

void ui_init();

// Update the arc gauge (block_pct), centre numeral (day_tokens), the weekly
// total caption, and the block-reset time (shown in Singapore time, UTC+8 —
// SG has no DST so a fixed offset is safe). `block_reset_utc` is the raw
// UTC epoch seconds from UsageState; 0 means "no active block right now".
void ui_set_usage(uint32_t day_tokens, uint32_t week_tokens, uint8_t block_pct, uint32_t block_reset_utc);

// Update the connection dot and age caption. `haveData` is false until the
// first UsageState has ever been received (distinct from "stale").
void ui_set_connection(ConnState state, uint32_t age_seconds, bool haveData);
