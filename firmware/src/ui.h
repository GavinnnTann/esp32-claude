#pragma once

#include <stdint.h>

#include "usage_state.h"

enum class ConnState { Fresh, Stale, Disconnected };

void ui_init();

// Push a whole UsageState to the display. Verbose mode: every field gets its
// own row rather than being summarised, so nothing is hidden while the layout
// is still being decided (button-driven screen navigation comes later, once
// the board's two button GPIOs are known).
void ui_set_usage(const UsageState &state);

// Update the connection dot and age caption. `haveData` is false until the
// first UsageState has ever been received (distinct from "stale").
void ui_set_connection(ConnState state, uint32_t age_seconds, bool haveData);
