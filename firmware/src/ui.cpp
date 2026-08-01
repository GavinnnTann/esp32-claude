#include "ui.h"

#include <lvgl.h>
#include <stdio.h>

namespace {

lv_obj_t *arc = nullptr;
lv_obj_t *weekLabel = nullptr;
lv_obj_t *numeralLabel = nullptr;
lv_obj_t *ageLabel = nullptr;
lv_obj_t *resetLabel = nullptr;
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

// Singapore Standard Time is a fixed UTC+8 year-round (no DST), so this is
// safe as plain integer math — no timezone database needed on the MCU.
void format_reset_sgt(uint32_t block_reset_utc, char *out, size_t out_len) {
  if (block_reset_utc == 0) {
    snprintf(out, out_len, "no active block");
    return;
  }
  uint32_t sg = block_reset_utc + 8 * 3600UL;
  uint32_t sod = sg % 86400UL;
  uint32_t hh = sod / 3600UL;
  uint32_t mm = (sod % 3600UL) / 60UL;
  snprintf(out, out_len, "resets %02lu:%02lu SGT", (unsigned long)hh, (unsigned long)mm);
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

  // Small caption above the numeral: weekly total.
  weekLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(weekLabel, &lv_font_montserrat_10, LV_PART_MAIN);
  lv_label_set_text(weekLabel, "week --");
  lv_obj_align(weekLabel, LV_ALIGN_CENTER, 0, -65);

  // Centre numeral: day_tokens, compact-formatted.
  numeralLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(numeralLabel, &lv_font_montserrat_48, LV_PART_MAIN);
  lv_label_set_text(numeralLabel, "--");
  lv_obj_align(numeralLabel, LV_ALIGN_CENTER, 0, -10);

  // Small caption: staleness age.
  ageLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(ageLabel, &lv_font_montserrat_10, LV_PART_MAIN);
  lv_label_set_text(ageLabel, "waiting to connect...");
  lv_obj_align(ageLabel, LV_ALIGN_CENTER, 0, 38);

  // Small caption: current block's reset time, in Singapore local time.
  resetLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(resetLabel, &lv_font_montserrat_10, LV_PART_MAIN);
  lv_label_set_text(resetLabel, "");
  lv_obj_align(resetLabel, LV_ALIGN_CENTER, 0, 62);

  // Connection dot: green/yellow/red for fresh/stale/disconnected.
  connDot = lv_led_create(scr);
  lv_obj_set_size(connDot, 14, 14);
  lv_obj_align(connDot, LV_ALIGN_BOTTOM_MID, 0, -12);
  lv_led_set_color(connDot, lv_palette_main(LV_PALETTE_GREY));
  lv_led_off(connDot);
}

void ui_set_usage(uint32_t day_tokens, uint32_t week_tokens, uint8_t block_pct, uint32_t block_reset_utc) {
  if (block_pct > 100) block_pct = 100;
  lv_arc_set_value(arc, block_pct);

  char buf[16];
  format_compact(day_tokens, buf, sizeof(buf));
  lv_label_set_text(numeralLabel, buf);

  char weekBuf[24];
  char weekCompact[16];
  format_compact(week_tokens, weekCompact, sizeof(weekCompact));
  snprintf(weekBuf, sizeof(weekBuf), "week %s", weekCompact);
  lv_label_set_text(weekLabel, weekBuf);

  char resetBuf[24];
  format_reset_sgt(block_reset_utc, resetBuf, sizeof(resetBuf));
  lv_label_set_text(resetLabel, resetBuf);
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
