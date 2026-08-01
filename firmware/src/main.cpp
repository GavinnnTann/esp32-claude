#include <Arduino.h>
#include <TFT_eSPI.h>
#include <lvgl.h>

#include "ble_server.h"
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

  ble_server_init();
  Serial.println("[boot] ble_server_init() done, entering loop()");
}

void loop() {
  UsageState incoming;
  if (ble_server_take_new_state(incoming)) {
    lastState = incoming;
    haveState = true;
    // Assumes the host writes Time Sync before UsageState on every connect
    // (host/ble_client.py does this), so the clock is already synced here.
    lastStateEpoch = ble_server_synced_epoch();
    ui_set_usage(lastState.day_tokens, lastState.block_pct);
  }

  bool connected = ble_server_is_connected();
  uint32_t now = ble_server_synced_epoch();
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

  ui_set_connection(connState, age, haveState);

  lv_timer_handler();
  delay(5);
}
