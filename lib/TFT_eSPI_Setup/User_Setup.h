#define GC9A01_DRIVER      //WARNING Do not connect ILI9488 display SDO to MISO if other devices share the SPI bus (TFT SDO does NOT tristate when CS is high)
#define TFT_RGB_ORDER TFT_RGB
#define TFT_INVERSION_ON   //GC9A01 round panels need colours inverted at the driver level (matches LVGL_TFT_INVERT_COLORS in platformio.ini)

//ESP32
#define TFT_BL   32             //LED back-light control pin
#define TFT_BACKLIGHT_ON HIGH   //Level to turn ON back-light (HIGH or LOW)

#define TFT_MISO 12  //MISO
#define TFT_MOSI 15  //MOSI
#define TFT_SCLK 14  //SCK
#define TFT_CS   5   //Chip select control pin
#define TFT_DC    27   //Data Command control pin
#define TFT_RST   33   //Reset pin (could connect to RST pin)
#define TOUCH_CS 21      //Chip select pin (T_CS) of touch screen

#define LOAD_GLCD    //Font 1. Original Adafruit 8 pixel font needs ~1820 bytes in FLASH
#define LOAD_FONT2   //Font 2. Small 16 pixel high font, needs ~3534 bytes in FLASH, 96 characters
#define LOAD_FONT4   //Font 4. Medium 26 pixel high font, needs ~5848 bytes in FLASH, 96 characters
#define LOAD_FONT6   //Font 6. Large 48 pixel font, needs ~2666 bytes in FLASH, only characters 1234567890-.apm
#define LOAD_FONT7   //Font 7. 7 segment 48 pixel font, needs ~2438 bytes in FLASH, only characters 1234567890-.
#define LOAD_FONT8   //Font 8. Large 75 pixel font needs ~3256 bytes in FLASH, only characters 1234567890-.

#define LOAD_GFXFF   //FreeFonts. Include access to the 48 Adafruit_GFX free fonts FF1 to FF48 and custom fonts
#define SMOOTH_FONT

#define SPI_FREQUENCY  40000000
#define SPI_READ_FREQUENCY  16000000
#define SPI_TOUCH_FREQUENCY  2500000

#define USE_HSPI_PORT
