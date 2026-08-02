#pragma once

#include <stdint.h>

#include "usage_state.h"

enum class ConnState { Fresh, Stale, Disconnected };

// Views cycled with the two navigation buttons. Session first — it's the one
// that actually runs out during a working session.
enum class View : uint8_t {
  Session = 0,
  Weekly,
  Details,
  Mascot,
  _Count,
};

void ui_init();

// Current wall-clock time (epoch UTC, 0 if the host hasn't synced yet). Needed
// to tell whether a cached quota percentage still describes the live window.
void ui_set_now(uint32_t now_utc);

// Push a whole UsageState to the display.
void ui_set_usage(const UsageState &state);

// Update the connection dot and age caption. `haveData` is false until the
// first UsageState has ever been received (distinct from "stale").
void ui_set_connection(ConnState state, uint32_t age_seconds, bool haveData);

// Switch views. Wraps around in both directions.
void ui_next_view();
void ui_prev_view();
View ui_current_view();
