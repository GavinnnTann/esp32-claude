#pragma once

#include <stdint.h>

enum class ConnState { Fresh, Stale, Disconnected };

void ui_init();

// Update the arc gauge (block_pct) and centre numeral (day_tokens).
void ui_set_usage(uint32_t day_tokens, uint8_t block_pct);

// Update the connection dot and age caption. `haveData` is false until the
// first UsageState has ever been received (distinct from "stale").
void ui_set_connection(ConnState state, uint32_t age_seconds, bool haveData);
