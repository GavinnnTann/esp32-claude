#include <Arduino.h>
#include <TFT_eSPI.h>
#include <lvgl.h>

#include "ui/ui.h"

static const uint32_t SCREEN_WIDTH = 240;
static const uint32_t SCREEN_HEIGHT = 240;

static TFT_eSPI tft(SCREEN_WIDTH, SCREEN_HEIGHT);

/* Partial-render draw buffer: 1/10th of the screen is plenty for a 240x240 SPI panel */
static uint8_t draw_buf[SCREEN_WIDTH * (SCREEN_HEIGHT / 10) * (LV_COLOR_DEPTH / 8)];

static void display_flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map) {
  uint32_t w = area->x2 - area->x1 + 1;
  uint32_t h = area->y2 - area->y1 + 1;

  tft.startWrite();
  tft.setAddrWindow(area->x1, area->y1, w, h);
  tft.pushColors((uint16_t *)px_map, w * h, true);
  tft.endWrite();

  lv_display_flush_ready(disp);
}

static void touchpad_read_cb(lv_indev_t *indev, lv_indev_data_t *data) {
  uint16_t x, y;
  if (tft.getTouch(&x, &y)) {
    data->state = LV_INDEV_STATE_PRESSED;
    data->point.x = x;
    data->point.y = y;
  } else {
    data->state = LV_INDEV_STATE_RELEASED;
  }
}

void setup() {
  Serial.begin(115200);

  tft.begin();
  tft.setRotation(LVGL_TFT_ROTATION);

  lv_init();
  lv_tick_set_cb(millis);

  lv_display_t *disp = lv_display_create(SCREEN_WIDTH, SCREEN_HEIGHT);
  lv_display_set_flush_cb(disp, display_flush_cb);
  lv_display_set_buffers(disp, draw_buf, NULL, sizeof(draw_buf), LV_DISPLAY_RENDER_MODE_PARTIAL);

  lv_indev_t *indev = lv_indev_create();
  lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
  lv_indev_set_read_cb(indev, touchpad_read_cb);

  ui_init();
}

void loop() {
  lv_timer_handler();
  delay(5);
}
