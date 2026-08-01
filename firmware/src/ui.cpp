#include "ui.h"

#include <lvgl.h>
#include <stdio.h>

namespace {

lv_obj_t *arc = nullptr;
lv_obj_t *numeralLabel = nullptr;
lv_obj_t *ageLabel = nullptr;
lv_obj_t *connDot = nullptr;

void format_compact(uint32_t value, char *out, size_t out_len) {
  if (value < 1000) {
    snprintf(out, out_len, "%lu", (unsigned long)value);
  } else if (value < 1000000) {
    snprintf(out, out_len, "%.1fK", value / 1000.0);
  } else {
    snprintf(out, out_len, "%.2fM", value / 1000000.0);
  }
}

void format_age(uint32_t seconds, char *out, size_t out_len) {
  if (seconds < 60) {
    snprintf(out, out_len, "%lus", (unsigned long)seconds);
  } else if (seconds < 3600) {
    snprintf(out, out_len, "%lum", (unsigned long)(seconds / 60));
  } else {
    snprintf(out, out_len, "%luh", (unsigned long)(seconds / 3600));
  }
}

}  // namespace

void ui_init() {
  lv_display_t *disp = lv_display_get_default();
  lv_theme_t *theme = lv_theme_default_init(disp, lv_palette_main(LV_PALETTE_BLUE), lv_palette_main(LV_PALETTE_RED),
                                             true, LV_FONT_DEFAULT);
  lv_display_set_theme(disp, theme);

  lv_obj_t *scr = lv_screen_active();
  lv_obj_set_style_bg_color(scr, lv_color_black(), LV_PART_MAIN);

  // Arc gauge around the rim, tracking block_pct (handover.md #7).
  arc = lv_arc_create(scr);
  lv_obj_set_size(arc, 220, 220);
  lv_obj_center(arc);
  lv_arc_set_rotation(arc, 270);
  lv_arc_set_bg_angles(arc, 0, 360);
  lv_arc_set_range(arc, 0, 100);
  lv_arc_set_value(arc, 0);
  lv_obj_remove_style(arc, NULL, LV_PART_KNOB);
  lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_style_arc_width(arc, 14, LV_PART_MAIN);
  lv_obj_set_style_arc_width(arc, 14, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(arc, lv_palette_main(LV_PALETTE_BLUE), LV_PART_INDICATOR);

  // Centre numeral: day_tokens, compact-formatted.
  numeralLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(numeralLabel, &lv_font_montserrat_48, LV_PART_MAIN);
  lv_label_set_text(numeralLabel, "--");
  lv_obj_align(numeralLabel, LV_ALIGN_CENTER, 0, -10);

  // Small caption: staleness age.
  ageLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(ageLabel, &lv_font_montserrat_10, LV_PART_MAIN);
  lv_label_set_text(ageLabel, "waiting to connect...");
  lv_obj_align(ageLabel, LV_ALIGN_CENTER, 0, 40);

  // Connection dot: green/yellow/red for fresh/stale/disconnected.
  connDot = lv_led_create(scr);
  lv_obj_set_size(connDot, 14, 14);
  lv_obj_align(connDot, LV_ALIGN_BOTTOM_MID, 0, -18);
  lv_led_set_color(connDot, lv_palette_main(LV_PALETTE_GREY));
  lv_led_off(connDot);
}

void ui_set_usage(uint32_t day_tokens, uint8_t block_pct) {
  if (block_pct > 100) block_pct = 100;
  lv_arc_set_value(arc, block_pct);

  char buf[16];
  format_compact(day_tokens, buf, sizeof(buf));
  lv_label_set_text(numeralLabel, buf);
}

void ui_set_connection(ConnState state, uint32_t age_seconds, bool haveData) {
  lv_color_t dotColor;
  const char *prefix;
  switch (state) {
    case ConnState::Fresh:
      dotColor = lv_palette_main(LV_PALETTE_GREEN);
      prefix = "updated";
      break;
    case ConnState::Stale:
      dotColor = lv_palette_main(LV_PALETTE_YELLOW);
      prefix = "stale";
      break;
    case ConnState::Disconnected:
    default:
      dotColor = lv_palette_main(LV_PALETTE_RED);
      prefix = "disconnected";
      break;
  }
  lv_led_set_color(connDot, dotColor);
  lv_led_on(connDot);

  char buf[32];
  if (!haveData) {
    snprintf(buf, sizeof(buf), state == ConnState::Disconnected ? "waiting to connect..." : "waiting for data...");
  } else {
    char ageBuf[16];
    format_age(age_seconds, ageBuf, sizeof(ageBuf));
    snprintf(buf, sizeof(buf), "%s %s ago", prefix, ageBuf);
  }
  lv_label_set_text(ageLabel, buf);
}
