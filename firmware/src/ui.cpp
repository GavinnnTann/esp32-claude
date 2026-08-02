#include "ui.h"

#include <lvgl.h>
#include <stdio.h>
#include <string.h>

namespace {

lv_obj_t *arc = nullptr;
lv_obj_t *modelLabel = nullptr;
lv_obj_t *dayLabel = nullptr;
lv_obj_t *weekLabel = nullptr;
lv_obj_t *blockLabel = nullptr;
lv_obj_t *pctLabel = nullptr;
lv_obj_t *resetLabel = nullptr;
lv_obj_t *ageLabel = nullptr;
lv_obj_t *connDot = nullptr;

void format_compact(uint32_t value, char *out, size_t out_len) {
  if (value < 1000) {
    snprintf(out, out_len, "%lu", (unsigned long)value);
  } else if (value < 1000000) {
    snprintf(out, out_len, "%.1fK", value / 1000.0);
  } else {
    snprintf(out, out_len, "%.1fM", value / 1000000.0);
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
  snprintf(out, out_len, "resets %02lu:%02lu SGT", (unsigned long)(sod / 3600UL),
           (unsigned long)((sod % 3600UL) / 60UL));
}

// One "<label> <tokens>  $<dollars>" row, e.g. "day 111.7M  $26.19".
void set_usage_row(lv_obj_t *label, const char *name, uint32_t tokens, uint32_t cents) {
  char compact[16];
  format_compact(tokens, compact, sizeof(compact));
  char buf[40];
  snprintf(buf, sizeof(buf), "%s %s  $%lu.%02lu", name, compact, (unsigned long)(cents / 100),
           (unsigned long)(cents % 100));
  lv_label_set_text(label, buf);
}

// The struct's char arrays are not guaranteed null-terminated (they're exactly
// full when the source string fills the slot), so copy into a padded buffer
// before treating them as C strings.
void copy_fixed(const char *src, size_t src_len, char *out, size_t out_len) {
  size_t n = 0;
  while (n < src_len && n + 1 < out_len && src[n] != '\0') {
    out[n] = src[n];
    n++;
  }
  out[n] = '\0';
}

// Traffic-light thresholds, same idea as Claude.ai's own usage bar: green
// while there's plenty of room in the block, amber as it gets close, red
// once it's nearly (or fully) used up.
lv_color_t arc_color_for_pct(uint8_t pct) {
  if (pct < 70) return lv_palette_main(LV_PALETTE_GREEN);
  if (pct < 90) return lv_palette_main(LV_PALETTE_ORANGE);
  return lv_palette_main(LV_PALETTE_RED);
}

lv_obj_t *add_row(lv_obj_t *parent, const char *initial) {
  lv_obj_t *label = lv_label_create(parent);
  lv_label_set_text(label, initial);
  return label;
}

}  // namespace

void ui_init() {
  lv_display_t *disp = lv_display_get_default();
  lv_theme_t *theme = lv_theme_default_init(disp, lv_palette_main(LV_PALETTE_BLUE), lv_palette_main(LV_PALETTE_RED),
                                             true, LV_FONT_DEFAULT);
  lv_display_set_theme(disp, theme);

  lv_obj_t *scr = lv_screen_active();
  lv_obj_set_style_bg_color(scr, lv_color_black(), LV_PART_MAIN);

  // Arc gauge around the rim, tracking block_pct like a quota meter
  // (handover.md #7, plus traffic-light coloring matching Claude.ai's bar).
  arc = lv_arc_create(scr);
  lv_obj_set_size(arc, 232, 232);
  lv_obj_center(arc);
  lv_arc_set_rotation(arc, 270);
  lv_arc_set_bg_angles(arc, 0, 360);
  lv_arc_set_range(arc, 0, 100);
  lv_arc_set_value(arc, 0);
  lv_obj_remove_style(arc, NULL, LV_PART_KNOB);
  lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_style_arc_width(arc, 10, LV_PART_MAIN);
  lv_obj_set_style_arc_width(arc, 10, LV_PART_INDICATOR);
  // Dim, neutral track behind a bright rounded-cap indicator — flat caps on a
  // thin indicator read as a stray blocky tick mark at low percentages.
  lv_obj_set_style_arc_color(arc, lv_palette_darken(LV_PALETTE_GREY, 3), LV_PART_MAIN);
  lv_obj_set_style_arc_rounded(arc, true, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(arc, arc_color_for_pct(0), LV_PART_INDICATOR);

  // Verbose text block, stacked vertically inside the arc. Sized to the square
  // that fits within the arc's inner circle (~150px across) so long rows can't
  // run under the rim; everything uses font 10 for the same reason.
  lv_obj_t *col = lv_obj_create(scr);
  lv_obj_remove_style_all(col);
  lv_obj_set_size(col, 150, 150);
  lv_obj_center(col);
  lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_flex_align(col, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_row(col, 1, LV_PART_MAIN);
  lv_obj_remove_flag(col, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_remove_flag(col, LV_OBJ_FLAG_CLICKABLE);

  modelLabel = add_row(col, "-- / --");
  lv_obj_set_style_text_color(modelLabel, lv_palette_main(LV_PALETTE_BLUE), LV_PART_MAIN);

  // The two real quota figures, straight from Claude Code's own cache — these
  // are the headline numbers, so they sit directly under the model line.
  pctLabel = add_row(col, "sess --%  week --%");

  dayLabel = add_row(col, "day --");
  weekLabel = add_row(col, "week --");
  blockLabel = add_row(col, "block --");

  resetLabel = add_row(col, "");
  lv_obj_set_style_text_color(resetLabel, lv_palette_lighten(LV_PALETTE_GREY, 1), LV_PART_MAIN);

  ageLabel = add_row(col, "waiting to connect...");
  lv_obj_set_style_text_color(ageLabel, lv_palette_lighten(LV_PALETTE_GREY, 1), LV_PART_MAIN);

  // Connection dot: green/yellow/red for fresh/stale/disconnected.
  connDot = lv_led_create(scr);
  lv_obj_set_size(connDot, 10, 10);
  lv_obj_align(connDot, LV_ALIGN_BOTTOM_MID, 0, -26);
  lv_led_set_color(connDot, lv_palette_main(LV_PALETTE_GREY));
  lv_led_off(connDot);
}

void ui_set_usage(const UsageState &state) {
  // The arc tracks the real "Session (5hr)" figure — the same number Claude
  // Code's own Account & Usage panel shows, not a token-ratio estimate.
  uint8_t pct = state.session_pct > 100 ? 100 : state.session_pct;
  lv_arc_set_value(arc, state.limits_ok ? pct : 0);
  lv_obj_set_style_arc_color(arc, state.limits_ok ? arc_color_for_pct(pct)
                                                  : lv_palette_darken(LV_PALETTE_GREY, 1),
                             LV_PART_INDICATOR);

  char model[sizeof(state.model) + 1];
  char effort[sizeof(state.effort) + 1];
  copy_fixed(state.model, sizeof(state.model), model, sizeof(model));
  copy_fixed(state.effort, sizeof(state.effort), effort, sizeof(effort));
  char modelBuf[40];
  snprintf(modelBuf, sizeof(modelBuf), "%s / %s", model[0] ? model : "?", effort[0] ? effort : "?");
  lv_label_set_text(modelLabel, modelBuf);

  char pctBuf[32];
  if (state.limits_ok) {
    snprintf(pctBuf, sizeof(pctBuf), "sess %u%%  week %u%%", (unsigned)pct, (unsigned)state.week_pct);
  } else {
    snprintf(pctBuf, sizeof(pctBuf), "quota unavailable");
  }
  lv_label_set_text(pctLabel, pctBuf);
  lv_obj_set_style_text_color(pctLabel, state.limits_ok ? arc_color_for_pct(pct)
                                                        : lv_palette_lighten(LV_PALETTE_GREY, 1),
                              LV_PART_MAIN);

  set_usage_row(dayLabel, "day", state.day_tokens, state.day_cents);
  set_usage_row(weekLabel, "week", state.week_tokens, state.week_cents);
  set_usage_row(blockLabel, "block", state.block_tokens, state.block_cents);

  char resetBuf[24];
  format_reset_sgt(state.session_reset, resetBuf, sizeof(resetBuf));
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
