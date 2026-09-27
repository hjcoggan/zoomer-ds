#include "gfx.h"
#include "assets.h"

// Per screen BG VRAM (bank A bottom, bank C top), both laid out the same:
//   0KB   font tiles
//   28KB  text map (32x32)
//   30KB  "half" text map: the same font, shifted 4 pixels right, so text
//         with an odd number of letters can be centred exactly
//   32KB  the 256x192 picture, 16 bits per pixel
// The bottom screen's BG0 is the 3D engine (bank B holds its textures,
// bank F their palettes). Bank D holds the top screen's sprites.
#define MAP_BASE 14         // 2KB units
#define HALF_MAP_BASE 15
#define BMP_BASE 2          // 16KB units
#define FONT_PAL_BANK 1

static int bg_text[2], bg_half[2], bg_pic[2];
static uint16_t *text_map[2], *half_map[2];
static uint16_t *pic[2];

void gfx_init(void)
{
    powerOn(POWER_ALL_2D | POWER_3D_CORE | POWER_MATRIX);
    lcdMainOnBottom();                          // main engine (with 3D) drives the touch screen
    videoSetMode(MODE_3_3D);
    videoSetModeSub(MODE_5_2D);
    vramSetBankA(VRAM_A_MAIN_BG);
    vramSetBankB(VRAM_B_TEXTURE);
    vramSetBankC(VRAM_C_SUB_BG);
    vramSetBankD(VRAM_D_SUB_SPRITE);
    vramSetBankF(VRAM_F_TEX_PALETTE);
    setBrightness(3, -16);

    bg_pic[BOT] = bgInit(3, BgType_Bmp16, BgSize_B16_256x256, BMP_BASE, 0);
    bg_text[BOT] = bgInit(1, BgType_Text4bpp, BgSize_T_256x256, MAP_BASE, 0);
    bg_half[BOT] = bgInit(2, BgType_Text4bpp, BgSize_T_256x256, HALF_MAP_BASE, 0);
    bg_pic[TOP] = bgInitSub(3, BgType_Bmp16, BgSize_B16_256x256, BMP_BASE, 0);
    bg_text[TOP] = bgInitSub(0, BgType_Text4bpp, BgSize_T_256x256, MAP_BASE, 0);
    bg_half[TOP] = bgInitSub(1, BgType_Text4bpp, BgSize_T_256x256, HALF_MAP_BASE, 0);
    bgSetPriority(0, 2);                        // 3D layer: over the picture, under the text
    for (int s = 0; s < 2; s++) {
        bgSetPriority(bg_pic[s], 3);
        bgSetPriority(bg_text[s], 1);           // top screen sprites at priority 2 sit under the text
        bgSetPriority(bg_half[s], 0);
        text_map[s] = bgGetMapPtr(bg_text[s]);
        half_map[s] = bgGetMapPtr(bg_half[s]);
        bgSetScroll(bg_half[s], -4, 0);
        pic[s] = bgGetGfxPtr(bg_pic[s]);
        dmaCopy(font_tiles, bgGetGfxPtr(bg_text[s]), sizeof(font_tiles));
        text_clear(s);
    }
    dmaCopy(font_pal, BG_PALETTE + FONT_PAL_BANK * 16, sizeof(font_pal));
    dmaCopy(font_pal, BG_PALETTE_SUB + FONT_PAL_BANK * 16, sizeof(font_pal));

    oamInit(&oamSub, SpriteMapping_1D_128, false);
    dmaCopy(sub_pal, SPRITE_PALETTE_SUB, sizeof(sub_pal));
    dmaCopy(sub_tiles, SPRITE_GFX_SUB, sizeof(sub_tiles));
    bgUpdate();
}

void gfx_picture(int scr, const uint8_t *lz)
{
    decompress(lz, pic[scr], LZ77Vram);
}

// ---------------------------------------------------------------- text

static uint16_t cell(int tile) { return tile | (FONT_PAL_BANK << 12); }

static void text_fill(int scr, int x, int y, int w, int h, int tile)
{
    for (int j = y; j < y + h; j++)
        for (int i = x; i < x + w; i++)
            if (i >= 0 && i < 32) text_map[scr][(j & 31) * 32 + i] = cell(tile);
}

void text_clear_rows(int scr, int y0, int y1)
{
    text_fill(scr, 0, y0, 32, y1 - y0, 0);
    for (int j = y0; j < y1; j++)
        for (int i = 0; i < 32; i++) half_map[scr][(j & 31) * 32 + i] = cell(0);
}

void text_clear(int scr) { text_clear_rows(scr, 0, 32); }

static int glyph(char ch)
{
    for (const char *f = FONT_CHARS; *f; f++)
        if (*f == ch) return f - FONT_CHARS;
    return 0;
}

static void put_text(uint16_t *map, int x, int y, const char *s, int style)
{
    for (; *s; s++, x++)
        if (x >= 0 && x < 32) map[(y & 31) * 32 + x] = cell(style * FONT_NCHARS + glyph(*s));
}

void text_style(int scr, int x, int y, const char *s, int style) { put_text(text_map[scr], x, y, s, style); }

static int len(const char *s)
{
    int n = 0;
    while (s[n]) n++;
    return n;
}

// Centre on the pixel column x*8 + w*4, using the half layer when the text
// would otherwise land half a letter off.
void text_center_in(int scr, int x, int w, int y, const char *s, int style)
{
    int start = x * 8 + w * 4 - len(s) * 4;
    if (start & 4) put_text(half_map[scr], (start - 4) / 8, y, s, style);
    else put_text(text_map[scr], start / 8, y, s, style);
}

void text_center(int scr, int y, const char *s, int style) { text_center_in(scr, 0, 32, y, s, style); }

char *put_str(char *p, const char *s)
{
    while (*s) *p++ = *s++;
    *p = 0;
    return p;
}

char *put_num(char *p, int v)
{
    char tmp[12];
    int n = 0;
    if (v < 0) {
        *p++ = '-';
        v = -v;
    }
    do {
        tmp[n++] = '0' + v % 10;
        v /= 10;
    } while (v);
    while (n) *p++ = tmp[--n];
    *p = 0;
    return p;
}

char *put_num0(char *p, int v, int width)
{
    for (int i = width - 1; i >= 0; i--) {
        p[i] = '0' + v % 10;
        v /= 10;
    }
    p[width] = 0;
    return p + width;
}

void text_num(int scr, int x, int y, int v, int width, int style)
{
    char buf[12];
    int n = put_num(buf, v) - buf;
    for (int i = 0; i < width - n; i++) text_style(scr, x + i, y, " ", style);
    text_style(scr, x + width - n, y, buf, style);
}

void text_num0(int scr, int x, int y, int v, int width, int style)
{
    char buf[12];
    put_num0(buf, v, width);
    text_style(scr, x, y, buf, style);
}

void big_number(int scr, int x, int y, int v, int digits)
{
    for (int i = 0; i < digits; i++) {
        int cx = x + (digits - 1 - i) * 2;
        int t = BIGDIGIT_TILE(v % 10);
        v /= 10;
        for (int r = 0; r < 3; r++) {
            text_map[scr][(y + r) * 32 + cx] = cell(t + r * 2);
            text_map[scr][(y + r) * 32 + cx + 1] = cell(t + r * 2 + 1);
        }
    }
}

void progress_bar(int scr, int x, int y, int tiles, int num, int den, int kind)
{
    int px = den > 0 ? num * tiles * 8 / den : 0;
    if (px > tiles * 8) px = tiles * 8;
    if (px < 0) px = 0;
    for (int i = 0; i < tiles; i++) {
        int f = px - i * 8;
        f = f < 0 ? 0 : f > 8 ? 8 : f;
        text_map[scr][y * 32 + x + i] = cell(BAR_TILE(f, kind));
    }
}

static void panel_on(uint16_t *map, int x, int y, int w, int h)
{
    int fill = TXT_PANEL * FONT_NCHARS;          // blank char on a panel
    for (int j = 0; j < h; j++) {
        for (int i = 0; i < w; i++) {
            int t = fill;
            int top = j == 0, bot = j == h - 1, left = i == 0, right = i == w - 1;
            if (top) t = FRAME_TILE + (left ? 0 : right ? 2 : 1);
            else if (bot) t = FRAME_TILE + (left ? 5 : right ? 7 : 6);
            else if (left) t = FRAME_TILE + 3;
            else if (right) t = FRAME_TILE + 4;
            if (x + i >= 0 && x + i < 32) map[((y + j) & 31) * 32 + x + i] = cell(t);
        }
    }
}

void panel(int scr, int x, int y, int w, int h) { panel_on(text_map[scr], x, y, w, h); }

// A button centred across the screen. Its width grows by one if needed so the
// label sits exactly in the middle; odd widths go on the half layer.
static int button_width(int w, const char *label) { return w + ((w - len(label)) & 1); }

void button(int scr, int y, int w, int h, const char *label, int style)
{
    w = button_width(w, label);
    if (w & 1) panel_on(half_map[scr], (31 - w) / 2, y, w, h);
    else panel_on(text_map[scr], (32 - w) / 2, y, w, h);
    text_center(scr, y + h / 2, label, style);
}

int button_left(int w, const char *label) { return 128 - button_width(w, label) * 4; }
int button_px_width(int w, const char *label) { return button_width(w, label) * 8; }

void text_scroll(int scr, int y)
{
    bgSetScroll(bg_text[scr], 0, y);
    bgSetScroll(bg_half[scr], -4, y);
    bgUpdate();
}

// ---------------------------------------------------------------- effects

void fade(int to_black)
{
    for (int i = 0; i <= 16; i += 2) {
        setBrightness(3, -(to_black ? i : 16 - i));
        gfx_frame();
    }
}

void dim(int scr, int level)
{
    if (scr == BOT) {
        REG_BLDCNT = level ? (BLEND_FADE_BLACK | BLEND_SRC_BG0 | BLEND_SRC_BG3) : 0;
        REG_BLDY = level;
    } else {
        REG_BLDCNT_SUB = level ? (BLEND_FADE_BLACK | BLEND_SRC_BG3 | BLEND_SRC_SPRITE) : 0;
        REG_BLDY_SUB = level;
    }
}

void shake(int dx, int dy)
{
    bgSetScroll(bg_pic[BOT], dx, dy);
    bgUpdate();
}

// ---------------------------------------------------------------- sprites

void spr(int id, int x, int y, SpriteSize size, int tile, int pal, int prio)
{
    if (x < -64 || x > 256 || y < -64 || y > 192) return;
    oamSet(&oamSub, id, x, y, prio, pal, size, SpriteColorFormat_16Color, SPRITE_GFX_SUB + tile * 16,
           -1, false, false, false, false, false);
}

void spr_hide_all(void) { oamClear(&oamSub, 0, 128); }

void gfx_frame(void)
{
    swiWaitForVBlank();
    oamUpdate(&oamSub);
}
