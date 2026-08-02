#include "ui.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <lvgl.h>
#include <stdio.h>
#include <string.h>

extern "C" {
extern const uint8_t lottie_mascot_asset[];
extern const size_t lottie_mascot_asset_size;
}

namespace {

lv_obj_t *arc = nullptr;
lv_obj_t *titleLabel = nullptr;
lv_obj_t *pctLabel = nullptr;
lv_obj_t *tokensLabel = nullptr;
lv_obj_t *resetLabel = nullptr;
lv_obj_t *footerLabel = nullptr;
lv_obj_t *connDot = nullptr;
lv_obj_t *pageDots[(int)View::_Count] = {};
lv_obj_t *mascot = nullptr;

// 80x80 rather than 120x120: measured on this board, 80 gives ~19fps with 59KB
// heap to spare, where 120 gives ~14fps with only 26KB. CPU sits ~90% either
// way (LVGL renders as fast as it can), so the extra size buys nothing.
constexpr int32_t kMascotSide = 80;

View currentView = View::Session;

// Last state received, kept so a view switch can redraw immediately from
// cached data instead of showing blank fields until the next BLE push, which
// can be up to 5 minutes away.
UsageState cachedState{};
bool haveCached = false;

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
void format_reset_sgt(uint32_t reset_utc, char *out, size_t out_len) {
  if (reset_utc == 0) {
    snprintf(out, out_len, "--");
    return;
  }
  uint32_t sg = reset_utc + 8 * 3600UL;
  uint32_t sod = sg % 86400UL;
  snprintf(out, out_len, "resets %02lu:%02lu SGT", (unsigned long)(sod / 3600UL),
           (unsigned long)((sod % 3600UL) / 60UL));
}

// The weekly window can be days out, where a bare "resets 05:00" is ambiguous.
void format_reset_far(uint32_t reset_utc, uint32_t now_utc, char *out, size_t out_len) {
  if (reset_utc == 0) {
    snprintf(out, out_len, "--");
    return;
  }
  char timeBuf[28];
  format_reset_sgt(reset_utc, timeBuf, sizeof(timeBuf));
  if (now_utc > 0 && reset_utc > now_utc) {
    uint32_t days = (reset_utc - now_utc) / 86400UL;
    if (days > 0) {
      snprintf(out, out_len, "%s +%lud", timeBuf, (unsigned long)days);
      return;
    }
  }
  snprintf(out, out_len, "%s", timeBuf);
}

// Traffic-light thresholds, same idea as Claude.ai's own usage bar.
lv_color_t color_for_pct(uint8_t pct) {
  if (pct < 70) return lv_palette_main(LV_PALETTE_GREEN);
  if (pct < 90) return lv_palette_main(LV_PALETTE_ORANGE);
  return lv_palette_main(LV_PALETTE_RED);
}

// The struct's char arrays are not guaranteed null-terminated (they're exactly
// full when the source string fills the slot), so copy into a padded buffer.
void copy_fixed(const char *src, size_t src_len, char *out, size_t out_len) {
  size_t n = 0;
  while (n < src_len && n + 1 < out_len && src[n] != '\0') {
    out[n] = src[n];
    n++;
  }
  out[n] = '\0';
}

void refresh_page_dots() {
  for (int i = 0; i < (int)View::_Count; i++) {
    if (pageDots[i] == nullptr) continue;
    bool active = (i == (int)currentView);
    lv_obj_set_style_bg_color(
        pageDots[i], active ? lv_color_white() : lv_palette_darken(LV_PALETTE_GREY, 2), LV_PART_MAIN);
  }
}

// Sets the arc + big percentage for a quota view. `ok` is limits_ok: when the
// quota cache is unreadable the numbers are meaningless, so show them greyed
// rather than a confident-looking 0%.
void set_quota_view(uint8_t pct, bool ok) {
  if (pct > 100) pct = 100;
  lv_arc_set_value(arc, ok ? pct : 0);
  lv_obj_set_style_arc_color(arc, ok ? color_for_pct(pct) : lv_palette_darken(LV_PALETTE_GREY, 1),
                             LV_PART_INDICATOR);
  if (ok) {
    char buf[8];
    snprintf(buf, sizeof(buf), "%u%%", (unsigned)pct);
    lv_label_set_text(pctLabel, buf);
    lv_obj_set_style_text_color(pctLabel, color_for_pct(pct), LV_PART_MAIN);
  } else {
    lv_label_set_text(pctLabel, "--");
    lv_obj_set_style_text_color(pctLabel, lv_palette_lighten(LV_PALETTE_GREY, 1), LV_PART_MAIN);
  }
}

void set_tokens_row(uint32_t tokens, uint32_t cents) {
  char compact[16];
  format_compact(tokens, compact, sizeof(compact));
  char buf[36];
  snprintf(buf, sizeof(buf), "%s tok  $%lu.%02lu", compact, (unsigned long)(cents / 100),
           (unsigned long)(cents % 100));
  lv_label_set_text(tokensLabel, buf);
}

// ThorVG rasterises in software and pegs the CPU near 90% while animating, so
// the mascot only runs on its own view. Hiding alone isn't enough — the LVGL
// animation keeps ticking and re-rendering the canvas — so the animation is
// paused too, which is what actually returns the CPU.
void set_mascot_active(bool active) {
  if (mascot == nullptr) return;
  if (active) {
    lv_obj_remove_flag(mascot, LV_OBJ_FLAG_HIDDEN);
  } else {
    lv_obj_add_flag(mascot, LV_OBJ_FLAG_HIDDEN);
  }
  lv_anim_t *a = lv_lottie_get_anim(mascot);
  if (a != nullptr) {
    if (active) {
      lv_anim_resume(a);
    } else {
      lv_anim_pause(a);
    }
  }
}

// Redraws every view-dependent widget from `cachedState`.
void render_view() {
  refresh_page_dots();

  bool onMascot = (currentView == View::Mascot);
  set_mascot_active(onMascot);

  // The mascot view is just the animation plus the arc, so the text rows would
  // only clutter it.
  lv_obj_t *textRows[] = {titleLabel, pctLabel, tokensLabel, resetLabel};
  for (lv_obj_t *o : textRows) {
    if (o == nullptr) continue;
    if (onMascot) {
      lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
    } else {
      lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
    }
  }
  if (onMascot) {
    // Still colour the rim by session usage so the view carries meaning.
    if (haveCached && cachedState.limits_ok) {
      uint8_t pct = cachedState.session_pct > 100 ? 100 : cachedState.session_pct;
      lv_arc_set_value(arc, pct);
      lv_obj_set_style_arc_color(arc, color_for_pct(pct), LV_PART_INDICATOR);
    }
    return;
  }

  static const char *kTitles[] = {"SESSION", "WEEKLY", "TODAY"};
  lv_label_set_text(titleLabel, kTitles[(int)currentView]);

  if (!haveCached) {
    lv_label_set_text(pctLabel, "--");
    lv_obj_set_style_text_color(pctLabel, lv_palette_lighten(LV_PALETTE_GREY, 1), LV_PART_MAIN);
    lv_label_set_text(tokensLabel, "waiting for data");
    lv_label_set_text(resetLabel, "");
    lv_arc_set_value(arc, 0);
    return;
  }

  const UsageState &s = cachedState;
  char resetBuf[36];

  switch (currentView) {
    case View::Session:
      set_quota_view(s.session_pct, s.limits_ok);
      set_tokens_row(s.block_tokens, s.block_cents);
      format_reset_sgt(s.session_reset, resetBuf, sizeof(resetBuf));
      lv_label_set_text(resetLabel, resetBuf);
      break;

    case View::Weekly:
      set_quota_view(s.week_pct, s.limits_ok);
      set_tokens_row(s.week_tokens, s.week_cents);
      format_reset_far(s.week_reset, s.ts, resetBuf, sizeof(resetBuf));
      lv_label_set_text(resetLabel, resetBuf);
      break;

    case View::Details:
    default: {
      // No percentage exists for "today" — it isn't a quota window — so this
      // view shows the raw token count and which model/effort is running.
      lv_arc_set_value(arc, 0);
      lv_obj_set_style_arc_color(arc, lv_palette_darken(LV_PALETTE_GREY, 2), LV_PART_INDICATOR);
      char compact[16];
      format_compact(s.day_tokens, compact, sizeof(compact));
      lv_label_set_text(pctLabel, compact);
      lv_obj_set_style_text_color(pctLabel, lv_color_white(), LV_PART_MAIN);

      char tokensBuf[36];
      snprintf(tokensBuf, sizeof(tokensBuf), "today  $%lu.%02lu", (unsigned long)(s.day_cents / 100),
               (unsigned long)(s.day_cents % 100));
      lv_label_set_text(tokensLabel, tokensBuf);

      char model[sizeof(s.model) + 1];
      char effort[sizeof(s.effort) + 1];
      copy_fixed(s.model, sizeof(s.model), model, sizeof(model));
      copy_fixed(s.effort, sizeof(s.effort), effort, sizeof(effort));
      snprintf(resetBuf, sizeof(resetBuf), "%s / %s", model[0] ? model : "?", effort[0] ? effort : "?");
      lv_label_set_text(resetLabel, resetBuf);
      break;
    }
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

  // Arc gauge around the rim. Rounded caps because flat caps on a thin
  // indicator read as a stray blocky tick at low percentages.
  arc = lv_arc_create(scr);
  lv_obj_set_size(arc, 232, 232);
  lv_obj_center(arc);
  lv_arc_set_rotation(arc, 270);
  lv_arc_set_bg_angles(arc, 0, 360);
  lv_arc_set_range(arc, 0, 100);
  lv_arc_set_value(arc, 0);
  lv_obj_remove_style(arc, NULL, LV_PART_KNOB);
  lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_style_arc_width(arc, 12, LV_PART_MAIN);
  lv_obj_set_style_arc_width(arc, 12, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(arc, lv_palette_darken(LV_PALETTE_GREY, 3), LV_PART_MAIN);
  lv_obj_set_style_arc_rounded(arc, true, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(arc, color_for_pct(0), LV_PART_INDICATOR);

  titleLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(titleLabel, &lv_font_montserrat_14, LV_PART_MAIN);
  lv_obj_set_style_text_color(titleLabel, lv_palette_lighten(LV_PALETTE_GREY, 1), LV_PART_MAIN);
  lv_label_set_text(titleLabel, "SESSION");
  lv_obj_align(titleLabel, LV_ALIGN_CENTER, 0, -52);

  pctLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(pctLabel, &lv_font_montserrat_34, LV_PART_MAIN);
  lv_label_set_text(pctLabel, "--");
  lv_obj_align(pctLabel, LV_ALIGN_CENTER, 0, -16);

  tokensLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(tokensLabel, &lv_font_montserrat_10, LV_PART_MAIN);
  lv_label_set_text(tokensLabel, "waiting for data");
  lv_obj_align(tokensLabel, LV_ALIGN_CENTER, 0, 16);

  resetLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(resetLabel, &lv_font_montserrat_10, LV_PART_MAIN);
  lv_obj_set_style_text_color(resetLabel, lv_palette_lighten(LV_PALETTE_GREY, 1), LV_PART_MAIN);
  lv_label_set_text(resetLabel, "");
  lv_obj_align(resetLabel, LV_ALIGN_CENTER, 0, 34);

  footerLabel = lv_label_create(scr);
  lv_obj_set_style_text_font(footerLabel, &lv_font_montserrat_10, LV_PART_MAIN);
  lv_obj_set_style_text_color(footerLabel, lv_palette_lighten(LV_PALETTE_GREY, 1), LV_PART_MAIN);
  lv_label_set_text(footerLabel, "waiting to connect...");
  lv_obj_align(footerLabel, LV_ALIGN_CENTER, 0, 52);

  // Page indicator dots, so it's discoverable that other views exist.
  const int total = (int)View::_Count;
  for (int i = 0; i < total; i++) {
    lv_obj_t *d = lv_obj_create(scr);
    lv_obj_remove_style_all(d);
    lv_obj_set_size(d, 5, 5);
    lv_obj_set_style_radius(d, LV_RADIUS_CIRCLE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(d, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_align(d, LV_ALIGN_BOTTOM_MID, (int)((i - (total - 1) / 2.0) * 9), -22);
    pageDots[i] = d;
  }

  connDot = lv_led_create(scr);
  lv_obj_set_size(connDot, 8, 8);
  lv_obj_align(connDot, LV_ALIGN_TOP_MID, 0, 24);
  lv_led_set_color(connDot, lv_palette_main(LV_PALETTE_GREY));
  lv_led_off(connDot);

  // Mascot canvas comes from the heap, not a static array: 80x80x4 = 25,600B
  // would blow the ESP32's dram0_0_seg static segment, which is capped
  // separately from (and much smaller than) the heap. A static 120x120 version
  // failed to link by 52KB. heap_caps_aligned_alloc keeps the
  // LV_DRAW_BUF_ALIGN guarantee that plain malloc doesn't promise.
  const size_t mascotBytes = (size_t)kMascotSide * kMascotSide * 4;
  void *mascotBuf = heap_caps_aligned_alloc(LV_DRAW_BUF_ALIGN, mascotBytes, MALLOC_CAP_8BIT);
  if (mascotBuf == nullptr) {
    // Not fatal — the other three views are the useful ones. Skipping the
    // widget entirely leaves View::Mascot showing just the arc.
    Serial.printf("[ui] mascot disabled: could not allocate %u B (largest free block %u B)\n",
                  (unsigned)mascotBytes, (unsigned)ESP.getMaxAllocHeap());
  } else {
    mascot = lv_lottie_create(scr);
    lv_lottie_set_src_data(mascot, lottie_mascot_asset, lottie_mascot_asset_size);
    lv_lottie_set_buffer(mascot, kMascotSide, kMascotSide, mascotBuf);
    lv_obj_center(mascot);
    lv_anim_t *a = lv_lottie_get_anim(mascot);
    if (a != nullptr) lv_anim_set_repeat_count(a, LV_ANIM_REPEAT_INFINITE);
    Serial.printf("[ui] mascot %dx%d ready (%u B), heap %u B\n", (int)kMascotSide, (int)kMascotSide,
                  (unsigned)mascotBytes, (unsigned)ESP.getFreeHeap());
  }

  refresh_page_dots();
  render_view();  // applies the initial show/hide + paused state
}

void ui_set_usage(const UsageState &state) {
  cachedState = state;
  haveCached = true;
  render_view();
}

void ui_next_view() {
  currentView = (View)(((int)currentView + 1) % (int)View::_Count);
  render_view();
}

void ui_prev_view() {
  currentView = (View)(((int)currentView + (int)View::_Count - 1) % (int)View::_Count);
  render_view();
}

View ui_current_view() { return currentView; }

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
  lv_label_set_text(footerLabel, buf);
}
