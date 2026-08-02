#include "ui.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <lvgl.h>
#include <stdio.h>
#include <string.h>

// Private LVGL/ThorVG headers: needed to read the animation's real duration
// and override lv_lottie_set_src_data's hardcoded 60fps assumption.
#include <src/widgets/lottie/lv_lottie_private.h>

#include "crab_assets.h"

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
// Allocated once and reused across mood swaps, since the widget itself gets
// rebuilt each time (see apply_mood).
void *mascotBuf = nullptr;

// Full-panel stage for the rocking mood. Native LVGL rather than part of the
// Lottie: a 240x240 Lottie canvas would need 230KB (4 bytes/px) against ~44KB
// of free heap, and flat rects are far cheaper to fill than to rasterise
// through ThorVG.
constexpr int kGridLines = 4;  // per axis
lv_obj_t *stageBg = nullptr;
lv_obj_t *gridLines[kGridLines * 2] = {};
lv_anim_t *gridAnim = nullptr;

const lv_color_t kStageBg = lv_color_hex(0x2E1851);
const lv_color_t kStageGrid = lv_color_hex(0x9850E5);
// Fable's firelight. Deliberately dim at the trough and only a soft red at the
// peak - a saturated red behind an orange crab swallows the crab.
const lv_color_t kFireDim = lv_color_hex(0x120504);
const lv_color_t kFireGlow = lv_color_hex(0x8C261A);

// One pulse per hit, each way. Shared by both stages because both animations
// beat four times per loop; see FABLE_STRIKES in tools/make_crab_lottie.py.
constexpr uint32_t kStagePulseMs = 250;

enum class Stage : uint8_t { None, Rock, RockStill, Fire };
Stage currentStage = Stage::None;

// 96x96 = 36,864 bytes, and it must be ARGB8888 - lv_lottie_set_buffer hands
// the pointer straight to tvg_swcanvas_set_target as ARGB8888, so there is no
// 16-bit option to halve it. This is the single largest allocation in the
// firmware, which makes it the only real lever on headroom.
//
// 112 and 104 both proved too greedy. The trap is that the heap figure logged
// after apply_mood() is measured post-parse but PRE-render: ThorVG then
// allocates again during rasterisation, in proportion to painted area. The
// desk scene fills far more of the canvas than the bed scene does, so it costs
// ~9KB more at draw time despite being the SMALLER asset - and at 104 that
// was enough to starve the arc's anti-aliasing mask
// (circ_calc_aa4: cir_x != NULL) and wedge the device on LVGL's assert.
// Judge headroom by the largest free block while the desk scene is drawing,
// not by asset size. Check the [mem] line before growing this.
constexpr int32_t kMascotSide = 96;
constexpr int32_t SCREEN_SIDE = 240;

// Quota thresholds at which the crab starts winding down. Deliberately below
// 100 for "sleepy" so it acts as a warning while there's still headroom left,
// rather than only telling you once the session is already spent.
constexpr uint8_t kSleepyPct = 85;
constexpr uint8_t kAsleepPct = 100;

// How long since Claude Code last wrote to a transcript before the crab counts
// as waiting on you. Deliberately generous: a single long-running tool call (a
// build, a test suite) writes NOTHING for its whole duration, so a short
// threshold would call an active session idle. Five minutes is long enough
// that it only fires when you have actually stepped away.
constexpr uint32_t kIdleAfterS = 5 * 60;

CrabMood currentMood = CRAB_MOOD_COUNT;  // sentinel: nothing loaded yet

uint32_t nowUtc = 0;

// The percentages come from a cache Claude Code refreshes on its own schedule,
// so they can outlive the window they describe: if the cache was last fetched
// before `reset` and that moment has now passed, the quota window has rolled
// over and the cached figure is stale by definition - observed showing 75% for
// a session that had already reset 24 minutes earlier.
//
// Reporting "unknown" is the honest answer here. Silently showing 0% would be
// a guess (the new window may already have usage in it), and showing the old
// value would be plainly wrong.
bool quota_window_current(uint32_t reset_utc) {
  if (nowUtc == 0 || reset_utc == 0) return true;  // can't tell; don't cry wolf
  return nowUtc < reset_utc;
}

bool contains(const char *hay, size_t hay_len, const char *needle) {
  size_t n = strlen(needle);
  if (n == 0 || n > hay_len) return false;
  for (size_t i = 0; i + n <= hay_len && hay[i] != '\0'; i++) {
    if (strncmp(hay + i, needle, n) == 0) return true;
  }
  return false;
}

// The mood the model and effort alone would produce, before quota or idle get
// a say. Split out because the tired variants need to know which SET the crab
// is standing in, which means resolving this even when quota overrides it.
//
// Every model has its own pair, split at "high":
//
//   Fable   high+ fights a dragon     below  stands watch
//   Opus    high+ rocks out           below  plays on, stage not pulsing
//   Sonnet  high+ heads-down at desk  below  focused
//   Haiku   high+ delighted           below  chilled
//
// Note "high" matches "xhigh" as a substring, and that is now exactly what is
// wanted everywhere - "high and above" is one test. The old code had to check
// xhigh first to stop the looser test swallowing it; nothing needs that split
// any more.
CrabMood base_mood(const UsageState &s) {
  const bool hard = contains(s.effort, sizeof(s.effort), "high");
  if (contains(s.model, sizeof(s.model), "fable")) {
    return hard ? CRAB_FABLE_FIGHT : CRAB_FABLE_CALM;
  }
  if (contains(s.model, sizeof(s.model), "opus")) {
    return hard ? CRAB_ROCKING : CRAB_ROCKING_CALM;
  }
  if (contains(s.model, sizeof(s.model), "sonnet")) {
    return hard ? CRAB_WORKING : CRAB_FOCUSED;
  }
  if (contains(s.model, sizeof(s.model), "haiku")) {
    return hard ? CRAB_HAPPY : CRAB_CHILL;
  }
  // Unknown model - fall back on effort alone rather than guessing a family.
  return hard ? CRAB_WORKING : CRAB_CHILL;
}

// Running low on session quota, the crab nods off WHERE IT IS rather than
// cutting to the generic sleepy animation. Swapping a crab at a desk for a
// bare crab on black read as a different character appearing - the whole point
// of these variants is that the transition is a change of posture, not of set.
CrabMood tired_for(CrabMood base) {
  switch (base) {
    case CRAB_WORKING: return CRAB_WORKING_TIRED;
    case CRAB_ROCKING:
    case CRAB_ROCKING_CALM: return CRAB_ROCKING_TIRED;
    case CRAB_FABLE_CALM:
    case CRAB_FABLE_FIGHT: return CRAB_FABLE_TIRED;
    default: return CRAB_SLEEPY;  // moods with no set of their own
  }
}

// Session quota wins over everything: a crab that looks alert while the
// session is exhausted would be actively misleading.
CrabMood mood_for(const UsageState &s) {
  const CrabMood base = base_mood(s);

  // Only trust the quota for mood while its window is still live. Otherwise a
  // stale 100% would leave the crab asleep long after the session had reset.
  if (s.limits_ok && quota_window_current(s.session_reset)) {
    // 100% keeps the shared bed scene deliberately: at that point the session
    // is spent and the crab has stopped working, so leaving it slumped at its
    // desk would say the opposite of what has happened.
    if (s.session_pct >= kAsleepPct) return CRAB_ASLEEP;
    if (s.session_pct >= kSleepyPct) return tired_for(base);
  }

  // Nothing written to a transcript in a while: Claude is waiting on you, and
  // showing it mid-grind would be wrong. This outranks model and effort
  // because those describe the last thing that RAN, not what is happening now.
  // Guarded on both clocks being known, and on now being after the activity -
  // an unsynced clock or a skewed host must not park the crab on idle.
  if (nowUtc != 0 && s.last_activity != 0 && nowUtc > s.last_activity &&
      nowUtc - s.last_activity >= kIdleAfterS) {
    return CRAB_IDLE;
  }

  return base;
}

#ifdef CRAB_MOOD_DEMO
CrabMood demoMood() {
  return (CrabMood)((millis() / 4000) % CRAB_MOOD_COUNT);
}
#endif

void apply_mood(CrabMood mood) {
  if (mascotBuf == nullptr || mood == currentMood || mood >= CRAB_MOOD_COUNT) return;

  // The widget is DESTROYED AND REBUILT rather than re-pointed at new data.
  // ThorVG's Picture::load refuses to load into an already-loaded picture
  // (`if (paint || surface) return Result::InsufficientCondition;`), and
  // lv_lottie_set_src_data ignores that return code - so calling it a second
  // time silently keeps rendering the first animation. That looked exactly
  // like the mood logic being broken, when the mood was in fact correct.
  if (mascot != nullptr) lv_obj_delete(mascot);

  // Clear the shared buffer. The canvas is ARGB and the new animation only
  // paints where its own shapes fall, so without this the previous mood's
  // pixels survive in the gaps as ghosting.
  memset(mascotBuf, 0, (size_t)kMascotSide * kMascotSide * 4);

  const CrabAsset &a = crab_mood_assets[mood];
  mascot = lv_lottie_create(lv_screen_active());
  // Source before buffer, matching LVGL's own example: set_buffer is what
  // sizes the picture and renders the first frame.
  // Re-parsing the JSON is the deep-recursion path that needs the 32KB stack,
  // so this must stay on state changes only - never per frame.
  lv_lottie_set_src_data(mascot, a.data, a.size);
  lv_lottie_set_buffer(mascot, kMascotSide, kMascotSide, mascotBuf);
  lv_obj_center(mascot);

  // lv_lottie_set_src_data assumes 60fps when deriving the duration
  // (`f_total * 1000 / 60`). These animations are authored at 30fps, so every
  // one of them played at double speed until this correction. Take the real
  // duration from ThorVG instead.
  lv_lottie_t *ld = (lv_lottie_t *)mascot;
  if (ld->anim != nullptr) {
    float dur_s = 0;
    tvg_animation_get_duration(ld->tvg_anim, &dur_s);
    if (dur_s > 0) ld->anim->duration = (int32_t)(dur_s * 1000.0f);
    ld->anim->repeat_cnt = LV_ANIM_REPEAT_INFINITE;
  }

  currentMood = mood;
  // Largest free block, not just total free: the mascot buffer and ThorVG's
  // parse allocations both need contiguous memory, so that is the number that
  // decides whether the next mood loads at all.
  Serial.printf("[ui] crab mood -> %d (%u B asset, heap %u B, largest %u B)\n", (int)mood,
                (unsigned)a.size, (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap());
}

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
// safe as plain integer math - no timezone database needed on the MCU.
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

// Grid opacity, driven by an LVGL animation so the pulse costs a style write
// rather than a re-rasterise.
void grid_opa_cb(void *var, int32_t v) {
  (void)var;
  for (lv_obj_t *l : gridLines) {
    if (l != nullptr) lv_obj_set_style_bg_opa(l, (lv_opa_t)v, LV_PART_MAIN);
  }
}

// Fable fights by firelight: the whole panel washes between near-black and a
// soft red. Colour is mixed here rather than animated as a style property
// because LVGL animates an int, not a colour.
void fire_mix_cb(void *var, int32_t v) {
  lv_obj_set_style_bg_color((lv_obj_t *)var, lv_color_mix(kFireGlow, kFireDim, (uint8_t)v),
                            LV_PART_MAIN);
}

// Backdrops that fill the whole panel. They live here as plain LVGL objects
// rather than inside the Lottie because a 240x240 canvas would need 230KB
// against ~46KB of free heap - and drawing them natively is far cheaper than
// making ThorVG rasterise them.
Stage stage_for(CrabMood m) {
  if (m == CRAB_ROCKING) return Stage::Rock;
  // Opus below high effort, and the dozing rocker: stage lit but not pulsing.
  // The pulse is locked to the strum tempo, so it belongs to the hard-effort
  // performance, not to every appearance of the guitar.
  if (m == CRAB_ROCKING_CALM || m == CRAB_ROCKING_TIRED) return Stage::RockStill;
  if (m == CRAB_FABLE_FIGHT) return Stage::Fire;
  // CRAB_FABLE_TIRED gets no fire: the dozing knight is not fighting anything.
  return Stage::None;
}

// The pulse animation is deleted rather than left running when hidden -
// otherwise it keeps dirtying full-width rects on views that don't show it.
void set_stage(Stage kind) {
  if (stageBg == nullptr || kind == currentStage) return;

  // Always tear the old one down first; the two stages drive different
  // callbacks and leaving both attached would fight over the same object.
  lv_anim_delete(stageBg, grid_opa_cb);
  lv_anim_delete(stageBg, fire_mix_cb);
  gridAnim = nullptr;

  if (kind == Stage::None) {
    lv_obj_add_flag(stageBg, LV_OBJ_FLAG_HIDDEN);
    for (lv_obj_t *l : gridLines) {
      if (l != nullptr) lv_obj_add_flag(l, LV_OBJ_FLAG_HIDDEN);
    }
    currentStage = kind;
    return;
  }

  lv_obj_remove_flag(stageBg, LV_OBJ_FLAG_HIDDEN);
  // Only the rock stages have a grid; fire is a bare wash.
  const bool grid = (kind == Stage::Rock || kind == Stage::RockStill);
  for (lv_obj_t *l : gridLines) {
    if (l == nullptr) continue;
    if (grid) {
      lv_obj_remove_flag(l, LV_OBJ_FLAG_HIDDEN);
    } else {
      lv_obj_add_flag(l, LV_OBJ_FLAG_HIDDEN);
    }
  }

  if (kind == Stage::RockStill) {
    // Lights on, performer asleep. No animation at all, so this costs nothing
    // to leave up.
    lv_obj_set_style_bg_color(stageBg, kStageBg, LV_PART_MAIN);
    grid_opa_cb(stageBg, LV_OPA_30);
    currentStage = kind;
    return;
  }

  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, stageBg);
  lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
  // Both stages beat at the same tempo, and both are locked to their
  // animation: four strums per loop for the guitar, FABLE_STRIKES swings per
  // loop for the sword (see make_crab_lottie.py - change one, change the
  // other). 250ms each way is one pulse per hit across a 2s loop.
  lv_anim_set_duration(&a, kStagePulseMs);
  lv_anim_set_reverse_duration(&a, kStagePulseMs);
  if (kind == Stage::Rock) {
    lv_obj_set_style_bg_color(stageBg, kStageBg, LV_PART_MAIN);
    lv_anim_set_exec_cb(&a, grid_opa_cb);
    lv_anim_set_values(&a, LV_OPA_20, LV_OPA_90);
    gridAnim = lv_anim_start(&a);
  } else {
    lv_anim_set_exec_cb(&a, fire_mix_cb);
    lv_anim_set_values(&a, 0, 255);
    lv_anim_start(&a);
  }
  currentStage = kind;
}

// ThorVG rasterises in software and pegs the CPU near 90% while animating, so
// the mascot only runs on its own view. Hiding alone isn't enough - the LVGL
// animation keeps ticking and re-rendering the canvas - so the animation is
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

  // Work out the target mood BEFORE touching anything, so the stage can be
  // torn down first (see below).
  bool wantMood = true;
#ifdef CRAB_MOOD_DEMO
  // Build with -D CRAB_MOOD_DEMO=1 to cycle every mood on a timer. Real quota
  // states take hours to reach, so this is the only practical way to confirm
  // each animation loads and renders within the heap budget. Deliberately does
  // NOT wait for haveCached - the demo has to work with no host connected.
  CrabMood target = demoMood();
#else
  CrabMood target = haveCached ? mood_for(cachedState) : currentMood;
  wantMood = haveCached;
#endif

  // Release the stage BEFORE parsing the next animation, not after. ThorVG's
  // Lottie parse is the peak allocation in the whole firmware, and leaving the
  // outgoing stage's full-screen background and eight grid bars alive across it
  // cost ~9KB at exactly the wrong moment - enough that the arc's
  // anti-aliasing mask then failed to allocate.
  const Stage wantStage = onMascot ? stage_for(target) : Stage::None;
  if (wantStage != currentStage) set_stage(Stage::None);
  if (wantMood) apply_mood(target);
  set_mascot_active(onMascot);
  set_stage(onMascot ? stage_for(currentMood) : Stage::None);

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
    case View::Session: {
      bool live = quota_window_current(s.session_reset);
      set_quota_view(s.session_pct, s.limits_ok && live);
      set_tokens_row(s.block_tokens, s.block_cents);
      if (live) {
        format_reset_sgt(s.session_reset, resetBuf, sizeof(resetBuf));
      } else {
        snprintf(resetBuf, sizeof(resetBuf), "window reset - awaiting data");
      }
      lv_label_set_text(resetLabel, resetBuf);
      break;
    }

    case View::Weekly: {
      bool live = quota_window_current(s.week_reset);
      set_quota_view(s.week_pct, s.limits_ok && live);
      set_tokens_row(s.week_tokens, s.week_cents);
      if (live) {
        format_reset_far(s.week_reset, nowUtc ? nowUtc : s.ts, resetBuf, sizeof(resetBuf));
      } else {
        snprintf(resetBuf, sizeof(resetBuf), "window reset - awaiting data");
      }
      lv_label_set_text(resetLabel, resetBuf);
      break;
    }

    case View::Details:
    default: {
      // No percentage exists for "today" - it isn't a quota window - so this
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

  // Stage first, so it sits at the bottom of the z-order. LVGL draws children
  // in creation order, and the mascot gets recreated later still (apply_mood),
  // which conveniently keeps it on top.
  stageBg = lv_obj_create(scr);
  lv_obj_remove_style_all(stageBg);
  lv_obj_set_size(stageBg, SCREEN_SIDE, SCREEN_SIDE);
  lv_obj_center(stageBg);
  lv_obj_set_style_radius(stageBg, LV_RADIUS_CIRCLE, LV_PART_MAIN);
  lv_obj_set_style_bg_color(stageBg, kStageBg, LV_PART_MAIN);
  lv_obj_set_style_bg_opa(stageBg, LV_OPA_COVER, LV_PART_MAIN);
  lv_obj_remove_flag(stageBg, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_remove_flag(stageBg, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_flag(stageBg, LV_OBJ_FLAG_HIDDEN);

  for (int i = 0; i < kGridLines; i++) {
    int pos = (SCREEN_SIDE * (i + 1)) / (kGridLines + 1);
    for (int axis = 0; axis < 2; axis++) {
      lv_obj_t *l = lv_obj_create(scr);
      lv_obj_remove_style_all(l);
      if (axis == 0) {
        lv_obj_set_size(l, 2, SCREEN_SIDE);
        lv_obj_align(l, LV_ALIGN_TOP_LEFT, pos, 0);
      } else {
        lv_obj_set_size(l, SCREEN_SIDE, 2);
        lv_obj_align(l, LV_ALIGN_TOP_LEFT, 0, pos);
      }
      lv_obj_set_style_bg_color(l, kStageGrid, LV_PART_MAIN);
      lv_obj_set_style_bg_opa(l, LV_OPA_50, LV_PART_MAIN);
      lv_obj_remove_flag(l, LV_OBJ_FLAG_SCROLLABLE);
      lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
      lv_obj_add_flag(l, LV_OBJ_FLAG_HIDDEN);
      gridLines[i * 2 + axis] = l;
    }
  }

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
  mascotBuf = heap_caps_aligned_alloc(LV_DRAW_BUF_ALIGN, mascotBytes, MALLOC_CAP_8BIT);
  if (mascotBuf == nullptr) {
    // Not fatal - the other three views are the useful ones. Skipping the
    // widget entirely leaves View::Mascot showing just the arc.
    Serial.printf("[ui] mascot disabled: could not allocate %u B (largest free block %u B)\n",
                  (unsigned)mascotBytes, (unsigned)ESP.getMaxAllocHeap());
  } else {
    apply_mood(CRAB_CHILL);  // placeholder until the first UsageState arrives
    Serial.printf("[ui] mascot %dx%d ready (%u B), heap %u B\n", (int)kMascotSide, (int)kMascotSide,
                  (unsigned)mascotBytes, (unsigned)ESP.getFreeHeap());
  }

  refresh_page_dots();
  render_view();  // applies the initial show/hide + paused state

#ifdef CRAB_MOOD_DEMO
  // render_view() otherwise only runs on a host update or a button press, so
  // demoMood()'s clock advanced but nothing ever read it - the demo sat on one
  // mood forever. Give it a tick of its own.
  lv_timer_create([](lv_timer_t *) {
    render_view();
    // Report AFTER a render has happened. The figure logged inside apply_mood
    // is post-parse but pre-render, and ThorVG allocates again while
    // rasterising - that gap is what made a 1.9KB mask allocation appear to
    // fail with 10KB "free".
    Serial.printf("[demo] mood %d  heap %u B  largest %u B  min ever %u B\n", (int)currentMood,
                  (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMaxAllocHeap(),
                  (unsigned)ESP.getMinFreeHeap());
  }, 1000, nullptr);
  // Force the mascot view: the demo is pointless on a text page.
  currentView = View::Mascot;
  render_view();
#endif
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

void ui_set_now(uint32_t now_utc) {
  // Re-render on the tick that crosses a reset boundary, so an expired window
  // stops claiming a stale percentage without waiting for the next BLE push.
  bool wasLive = quota_window_current(cachedState.session_reset);
  nowUtc = now_utc;
  if (haveCached && wasLive && !quota_window_current(cachedState.session_reset)) {
    render_view();
  }
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
  lv_label_set_text(footerLabel, buf);
}
