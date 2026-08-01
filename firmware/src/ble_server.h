#pragma once

#include <stdint.h>

#include "usage_state.h"

void ble_server_init();

// True while a central is connected.
bool ble_server_is_connected();

// If a new UsageState has arrived since the last call, copies it into `out` and
// returns true (clearing the pending flag). Safe to call from loop() every tick.
bool ble_server_take_new_state(UsageState &out);

// Current wall-clock epoch seconds, derived from the last Time Sync write plus
// elapsed millis(). Returns 0 if the host has never synced the clock.
uint32_t ble_server_synced_epoch();
