#include "ui.h"
#include "gba.h"
#include "assets.h"

void text_clear(void)
{
    volatile uint16_t *map = SCREENBLOCK(TEXT_SBB);
    for (int i = 0; i < 32 * 32; i++) map[i] = 0;
}

void text_clear_row(int y)
{
    volatile uint16_t *map = SCREENBLOCK(TEXT_SBB);
    for (int x = 0; x < 32; x++) map[(y & 31) * 32 + x] = 0;
}

void text_style(int x, int y, const char *s, int style)
{
    volatile uint16_t *map = SCREENBLOCK(TEXT_SBB);
    for (; *s; s++, x++) {
        int t = 0;
        for (const char *f = FONT_CHARS; *f; f++) {
            if (*f == *s) {
                t = f - FONT_CHARS;
                break;
            }
        }
        map[(y & 31) * 32 + x] = (style * FONT_NCHARS + t) | (1 << 12);
    }
}

void text_at(int x, int y, const char *s) { text_style(x, y, s, TXT_PLAIN); }

int text_len(const char *s)
{
    int n = 0;
    while (s[n]) n++;
    return n;
}

void text_center(int y, const char *s, int style)
{
    text_style((30 - text_len(s)) / 2, y, s, style);
}

void format_num(char *buf, int v, int width)
{
    buf[width] = 0;
    for (int i = width - 1; i >= 0; i--) {
        buf[i] = '0' + v % 10;
        v /= 10;
    }
}

void num_at(int x, int y, int v, int width)
{
    char buf[12];
    format_num(buf, v, width);
    text_at(x, y, buf);
}

void panel(int y, int w, int h)
{
    volatile uint16_t *map = SCREENBLOCK(TEXT_SBB);
    int x = (30 - w) / 2;
    int fill = TXT_PANEL * FONT_NCHARS;   // blank char on a panel
    for (int j = 0; j < h; j++) {
        for (int i = 0; i < w; i++) {
            int t = fill;
            int top = j == 0, bot = j == h - 1, left = i == 0, right = i == w - 1;
            if (top) t = FRAME_TILE + (left ? 0 : right ? 2 : 1);
            else if (bot) t = FRAME_TILE + (left ? 5 : right ? 7 : 6);
            else if (left) t = FRAME_TILE + 3;
            else if (right) t = FRAME_TILE + 4;
            map[(y + j) * 32 + x + i] = t | (1 << 12);
        }
    }
}

int menu_draw(int y, int w, const char *header, const char *const *items, int n, int sel)
{
    int h = n + (header ? 5 : 4);
    int x = (30 - w) / 2;
    int row = y + 2;
    panel(y, w, h);
    if (header) {
        text_center(y + 1, header, TXT_HILITE);
        row = y + 3;
    }
    for (int i = 0; i < n; i++) {
        int on = i == sel;
        text_style(x + 1, row + i, on ? ">" : " ", on ? TXT_HILITE : TXT_PANEL);
        text_style(x + 2, row + i, items[i], on ? TXT_HILITE : TXT_PANEL);
    }
    return h;
}
