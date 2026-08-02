#include <Arduino.h>
#include <TFT_eSPI.h>
#include <lvgl.h>

#include "ble_server.h"
#include "buttons.h"
#include "ui.h"
#include "usage_state.h"

static const uint32_t SCREEN_WIDTH = 240;
static const uint32_t SCREEN_HEIGHT = 240;

// handover.md #5: host polls/notifies every 5 minutes; call it stale a few
// intervals past that rather than the instant one poll is late.
static const uint32_t STALE_AFTER_S = 15 * 60;

static TFT_eSPI tft(SCREEN_WIDTH, SCREEN_HEIGHT);

/* Partial-render draw buffer: 1/10th of the screen. No PSRAM on this board (handover.md #2),
 * so a full 240x240x16bpp framebuffer (~115KB) is not an option.
 * alignas() is required: a plain uint8_t[] only guarantees 1-byte alignment, but
 * lv_display_set_buffers() asserts the pointer is aligned to LV_DRAW_BUF_ALIGN (4). */
alignas(LV_DRAW_BUF_ALIGN) static uint8_t draw_buf[SCREEN_WIDTH * (SCREEN_HEIGHT / 10) * (LV_COLOR_DEPTH / 8)];

static UsageState lastState{};
static bool haveState = false;
static uint32_t lastStateEpoch = 0;

// ThorVG's Lottie parser and path rasteriser recurse deeply — measured at
// ~21KB of stack — and every draw runs on whichever task calls
// lv_timer_handler(), i.e. Arduino's loopTask, whose default stack is 8KB.
// That overflow crash-loops with LoadProhibited *regardless of free heap*,
// so shrinking the animation never helps and it looks like a memory bug.
//
// LVGL's own guard (#error "Increase LV_DRAW_THREAD_STACK_SIZE to at least
// 32KB for ThorVG") is nested inside `#if LV_USE_OS`, so a no-OS build like
// ours gets no warning at all. LV_DRAW_THREAD_STACK_SIZE in lv_conf.h is
// inert here for the same reason.
//
// arduino-esp32 declares this __attribute__((weak)), so overriding it here is
// enough: no patching sdkconfig.h in the framework package (which a reinstall
// would silently undo) and no -D flag a header can redefine out from under us.
size_t getArduinoLoopTaskStackSize(void) {
  return 32 * 1024;
}

static void lv_log_print_cb(lv_log_level_t level, const char *buf) {
  Serial.print("[lvgl] ");
  Serial.println(buf);
}

static void display_flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map) {
  uint32_t w = area->x2 - area->x1 + 1;
  uint32_t h = area->y2 - area->y1 + 1;

  tft.startWrite();
  tft.setAddrWindow(area->x1, area->y1, w, h);
  tft.pushColors((uint16_t *)px_map, w * h, true);
  tft.endWrite();

  lv_display_flush_ready(disp);
}

void setup() {
  Serial.begin(115200);
  delay(300);  // let the USB-serial bridge settle before the first print
  Serial.println("[boot] serial up");

  Serial.println("[boot] tft.begin()...");
  tft.begin();
  Serial.println("[boot] tft.begin() done");
  tft.setRotation(LVGL_TFT_ROTATION);
  Serial.println("[boot] tft.setRotation() done");

  lv_init();
  lv_log_register_print_cb(lv_log_print_cb);
  lv_tick_set_cb(millis);
  Serial.println("[boot] lv_init() done");

  lv_display_t *disp = lv_display_create(SCREEN_WIDTH, SCREEN_HEIGHT);
  lv_display_set_flush_cb(disp, display_flush_cb);
  lv_display_set_buffers(disp, draw_buf, NULL, sizeof(draw_buf), LV_DISPLAY_RENDER_MODE_PARTIAL);
  Serial.println("[boot] lv_display created");

  ui_init();
  Serial.println("[boot] ui_init() done");

  buttons_init();

  ble_server_init();
  Serial.println("[boot] ble_server_init() done, entering loop()");

  // Runtime heap is the real memory budget, not the link-time RAM figure the
  // build prints: NimBLE and LVGL both allocate heavily at runtime. Anything
  // that needs a big buffer (e.g. a Lottie/canvas frame buffer at 4 bytes per
  // pixel) has to fit in largest-free-block, not just total free.
  Serial.printf("[mem] free heap: %u B, largest free block: %u B, min free ever: %u B\n",
                (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap(),
                (unsigned)ESP.getMinFreeHeap());
}

void loop() {
  switch (buttons_tick()) {
    case ButtonEvent::Up:
      ui_prev_view();
      Serial.printf("[btn] up -> view %d\n", (int)ui_current_view());
      break;
    case ButtonEvent::Down:
      ui_next_view();
      Serial.printf("[btn] down -> view %d\n", (int)ui_current_view());
      break;
    case ButtonEvent::None:
      break;
  }

  UsageState incoming;
  if (ble_server_take_new_state(incoming)) {
    lastState = incoming;
    haveState = true;
    // Assumes the host writes Time Sync before UsageState on every connect
    // (host/ble_client.py does this), so the clock is already synced here.
    lastStateEpoch = ble_server_synced_epoch();
    ui_set_usage(lastState);
  }

  bool connected = ble_server_is_connected();
  uint32_t now = ble_server_synced_epoch();
  ui_set_now(now);
  uint32_t age = (haveState && now >= lastStateEpoch) ? (now - lastStateEpoch) : 0;

  ConnState connState;
  if (!connected) {
    // handover.md #5: never blank the screen on disconnect — keep showing
    // cached values (ui_set_usage above already did its job) with an age.
    connState = ConnState::Disconnected;
  } else if (haveState && age <= STALE_AFTER_S) {
    connState = ConnState::Fresh;
  } else {
    connState = ConnState::Stale;
  }

  // Only touch the labels when something visibly changed. Rewriting them every
  // ~5ms marks them dirty every iteration, forcing LVGL to re-render the text
  // (and anything overlapping it) continuously — pure waste. Age is compared
  // directly because the caption only ever shows whole seconds/minutes/hours.
  static ConnState lastConnState = ConnState::Disconnected;
  static uint32_t lastShownAge = UINT32_MAX;
  static bool lastHaveState = false;
  if (connState != lastConnState || age != lastShownAge || haveState != lastHaveState) {
    lastConnState = connState;
    lastShownAge = age;
    lastHaveState = haveState;
    ui_set_connection(connState, age, haveState);
  }

  // Time spent in lv_timer_handler is the honest cost of whatever is on
  // screen: the mascot's software vector rendering can push this near 100%,
  // where button response starts to suffer. Reported alongside heap so a
  // regression in either is visible without reflashing an instrumented build.
  uint32_t t0 = micros();
  lv_timer_handler();
  static uint32_t busyMicros = 0;
  busyMicros += micros() - t0;

  static uint32_t lastReport = 0;
  if (millis() - lastReport > 60000) {
    uint32_t elapsedMs = millis() - lastReport;
    lastReport = millis();
    Serial.printf("[mem] heap %u B (largest %u B, min ever %u B)  lvgl busy %lu%%  view %d\n",
                  (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap(),
                  (unsigned)ESP.getMinFreeHeap(),
                  (unsigned long)(busyMicros / (elapsedMs * 10)), (int)ui_current_view());
    busyMicros = 0;
  }

  delay(5);
}
