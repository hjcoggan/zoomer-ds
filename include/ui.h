// Text layer helpers: font styles, gold-framed panels and menus (BG1).
#ifndef UI_H
#define UI_H

#define FONT_CBB 3
#define TEXT_SBB 30

// text styles, matching FONT_STYLES in tools/gen_assets.py
#define TXT_PLAIN 0
#define TXT_PANEL 1
#define TXT_HILITE 2
#define TXT_GOLD 3

void text_clear(void);
void text_clear_row(int y);
void text_style(int x, int y, const char *s, int style);
void text_at(int x, int y, const char *s);
void text_center(int y, const char *s, int style);
int text_len(const char *s);
void format_num(char *buf, int v, int width);   // zero padded, buf needs width+1
void num_at(int x, int y, int v, int width);

// Gold-framed panel, centred horizontally, rows y..y+h-1.
void panel(int y, int w, int h);

// Panel with an optional header and a list of items, the selected one
// highlighted with a pointer. Returns the panel's height.
int menu_draw(int y, int w, const char *header, const char *const *items, int n, int sel);

#endif
