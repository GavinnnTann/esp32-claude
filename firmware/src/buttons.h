#pragma once

#include <stdint.h>

enum class ButtonEvent : uint8_t {
  None,
  Up,
  Down,
};

// Call once in setup().
void buttons_init();

// Call every loop iteration. Returns a pending edge event, or None.
ButtonEvent buttons_tick();
