#include "buttons.h"

#include <Arduino.h>

namespace {

// Board's two navigation buttons. Neither collides with the display, which
// uses 12/14/15/5/27/33/32 plus 21 for touch (see lib/TFT_eSPI_Setup/User_Setup.h).
constexpr uint8_t PIN_UP = 4;
constexpr uint8_t PIN_DOWN = 18;

// Same debounce window FobBob settled on. Buttons are wired to GND and read
// through the internal pull-up, so pressed == LOW.
constexpr uint32_t DEBOUNCE_MS = 30;

struct BtnState {
  bool lastRaw;
  bool debounced;
  uint32_t lastChangeMs;
};

BtnState upState{};
BtnState downState{};

bool read_pressed(uint8_t pin) { return digitalRead(pin) == LOW; }

// Returns true on the press edge (not-pressed -> pressed) after debouncing.
bool update(BtnState &s, bool raw) {
  if (raw != s.lastRaw) {
    s.lastRaw = raw;
    s.lastChangeMs = millis();
  }
  bool prev = s.debounced;
  if ((millis() - s.lastChangeMs) >= DEBOUNCE_MS) {
    s.debounced = raw;
  }
  return s.debounced && !prev;
}

}  // namespace

void buttons_init() {
  pinMode(PIN_UP, INPUT_PULLUP);
  pinMode(PIN_DOWN, INPUT_PULLUP);

  // Seed the debounce state from the current level so a button that happens to
  // be held at boot doesn't register as a fresh press on the first tick.
  bool up = read_pressed(PIN_UP);
  bool down = read_pressed(PIN_DOWN);
  upState = {up, up, millis()};
  downState = {down, down, millis()};

  Serial.printf("[btn] init: up=GPIO%u down=GPIO%u (active low, internal pull-up)\n",
                (unsigned)PIN_UP, (unsigned)PIN_DOWN);
}

ButtonEvent buttons_tick() {
  bool upEdge = update(upState, read_pressed(PIN_UP));
  bool downEdge = update(downState, read_pressed(PIN_DOWN));

  // No auto-repeat: there are only a couple of views to cycle, so a held button
  // racing through them would be more annoying than useful.
  if (upEdge) return ButtonEvent::Up;
  if (downEdge) return ButtonEvent::Down;
  return ButtonEvent::None;
}
