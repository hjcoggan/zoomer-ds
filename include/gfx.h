// Both DS screens. Each has a full-colour 256x192 picture (BG3) and an 8x8
// text layer on top (plus a second copy shifted half a letter, for exact
// centring). The bottom (touch) screen also has the 3D layer (BG0) between
// the picture and the text; the top screen has 128 sprites.
#ifndef GFX_H
#define GFX_H

#include <nds.h>
#include <stdint.h>

enum { TOP, BOT };

// text styles, matching FONT_STYLES in tools/gen_assets.py
#define TXT_PLAIN 0
#define TXT_PANEL 1
#define TXT_HILITE 2
#define TXT_GOLD 3
#define TXT_DIM 4
#define TXT_TEAL 5

// progress bar colours
enum { BAR_TEAL, BAR_GOLD, BAR_RED };

void gfx_init(void);
void gfx_picture(int scr, const uint8_t *lz);       // LZ77-compressed 256x192 RGB15 picture

void text_clear(int scr);
void text_clear_rows(int scr, int y0, int y1);      // rows y0..y1-1
void text_style(int scr, int x, int y, const char *s, int style);
void text_center(int scr, int y, const char *s, int style);
void text_center_in(int scr, int x, int w, int y, const char *s, int style);
void text_num(int scr, int x, int y, int v, int width, int style);        // right-aligned, space padded
void text_num0(int scr, int x, int y, int v, int width, int style);       // zero padded
void big_number(int scr, int x, int y, int v, int digits);                 // 16px digits in a 3-row cell, zero padded
void progress_bar(int scr, int x, int y, int tiles, int num, int den, int kind);
void panel(int scr, int x, int y, int w, int h);
void button(int scr, int y, int w, int h, const char *label, int style);   // centred on the screen
int button_left(int w, const char *label);                                 // its pixel x and width
int button_px_width(int w, const char *label);
void text_scroll(int scr, int y);                   // vertical scroll of the text layer

char *put_str(char *p, const char *s);
char *put_num(char *p, int v);
char *put_num0(char *p, int v, int width);

void fade(int to_black);        // both screens, over a few frames
void dim(int scr, int level);   // darken the picture (and 3D/sprites), not the text; 0-16
void shake(int dx, int dy);     // nudge the bottom picture

// top screen sprites: tile is a 32-byte tile index into sub_tiles
void spr(int id, int x, int y, SpriteSize size, int tile, int pal, int prio);
void spr_hide_all(void);

void gfx_frame(void);           // wait for vblank and show this frame's sprites

#endif
