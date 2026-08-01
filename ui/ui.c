#include "ui.h"

void ui_init(void) {
  lv_display_t *disp = lv_display_get_default();
  lv_theme_t *theme = lv_theme_default_init(disp, lv_palette_main(LV_PALETTE_BLUE),
                                             lv_palette_main(LV_PALETTE_RED), false, LV_FONT_DEFAULT);
  lv_display_set_theme(disp, theme);

  ui_Screen1_screen_init();
  lv_screen_load(ui_Screen1);
}
