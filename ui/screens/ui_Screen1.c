#include "ui_Screen1.h"

lv_obj_t *ui_Screen1;
lv_obj_t *ui_Screen1_Label1;

void ui_Screen1_screen_init(void) {
  ui_Screen1 = lv_obj_create(NULL);
  lv_obj_clear_flag(ui_Screen1, LV_OBJ_FLAG_SCROLLABLE);

  ui_Screen1_Label1 = lv_label_create(ui_Screen1);
  lv_label_set_text(ui_Screen1_Label1, "Hello World");
  lv_obj_set_style_text_font(ui_Screen1_Label1, &lv_font_montserrat_24, LV_PART_MAIN | LV_STATE_DEFAULT);
  lv_obj_center(ui_Screen1_Label1);
}
