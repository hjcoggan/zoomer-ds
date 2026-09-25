#include "gba.h"
#include "assets.h"
#include "chain.h"

#define TEXT_SBB 30
#define MAP_SBB 31
#define SHOT_SPEED 5
#define TURN_SPEED 384            // 8.8 fixed, 256 units per full turn
#define ROLL_IN_SPEED (3 << 7)    // fast roll-in at level start

// OAM slots
#define OBJ_MOUTH 0
#define OBJ_NEXT  1
#define OBJ_FROG  2
#define OBJ_SHOT  3
#define OBJ_CHAIN 4

typedef enum { ST_TITLE, ST_PLAY, ST_CLEAR, ST_OVER } State;

static uint16_t oam[128 * 4];
static Chain chain;
static State state;
static int level, score, timer;
static int32_t angle;             // 8.8, 0 = up, clockwise
static int cur_color, next_color;
static int shot_active, shot_color;
static int32_t shot_x, shot_y, shot_vx, shot_vy;
static uint32_t rng = 0xC0FFEE;
static uint16_t keys, prev_keys;

static int isin(int a) { return sin_tab[a & 255]; }
static int icos(int a) { return sin_tab[(a + 64) & 255]; }

// ---------------------------------------------------------------- text

static void text_clear(void)
{
    volatile uint16_t *map = SCREENBLOCK(TEXT_SBB);
    for (int i = 0; i < 32 * 32; i++) map[i] = 0;
}

static void text_at(int x, int y, const char *s)
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
        map[y * 32 + x] = t | (1 << 12);
    }
}

static void text_center(int y, const char *s)
{
    int n = 0;
    while (s[n]) n++;
    text_at((30 - n) / 2, y, s);
}

static void num_at(int x, int y, int v, int width)
{
    char buf[12];
    buf[width] = 0;
    for (int i = width - 1; i >= 0; i--) {
        buf[i] = '0' + v % 10;
        v /= 10;
    }
    text_at(x, y, buf);
}

static void draw_hud(void)
{
    text_at(0, 0, "SCORE");
    num_at(6, 0, score, 6);
    text_at(21, 0, "LEVEL");
    num_at(27, 0, level, 2);
}

// ---------------------------------------------------------------- setup

static void init_video(void)
{
    REG_DISPCNT = 0;
    for (int i = 0; i < 32; i++) PAL_BG[i] = bg_pal[i];
    for (int i = 0; i < (int)(sizeof(obj_pal) / 2); i++) PAL_OBJ[i] = obj_pal[i];

    volatile uint32_t *d = CHARBLOCK(0);
    for (int i = 0; i < (int)(sizeof(bg_tiles) / 4); i++) d[i] = bg_tiles[i];
    d = CHARBLOCK(2);
    for (int i = 0; i < (int)(sizeof(font_tiles) / 4); i++) d[i] = font_tiles[i];
    for (int i = 0; i < (int)(sizeof(obj_tiles) / 4); i++) OBJ_TILES[i] = obj_tiles[i];

    volatile uint16_t *map = SCREENBLOCK(MAP_SBB);
    for (int y = 0; y < 32; y++)
        for (int x = 0; x < 32; x++)
            map[y * 32 + x] = (x < 30 && y < 20) ? y * 30 + x : 0;
    text_clear();

    REG_BG0CNT = BG_PRIO(3) | BG_CBB(0) | BG_SBB(MAP_SBB);
    REG_BG1CNT = BG_PRIO(0) | BG_CBB(2) | BG_SBB(TEXT_SBB);

    for (int i = 0; i < 128; i++) oam[i * 4] = ATTR0_HIDE;
    REG_DISPCNT = DCNT_MODE0 | DCNT_BG0 | DCNT_BG1 | DCNT_OBJ | DCNT_OBJ_1D;
}

static void set_obj(int n, int x, int y, uint16_t a0, uint16_t a1, uint16_t a2)
{
    oam[n * 4 + 0] = (y & 255) | a0;
    oam[n * 4 + 1] = (x & 511) | a1;
    oam[n * 4 + 2] = a2;
}

static void hide_obj(int n) { oam[n * 4] = ATTR0_HIDE; }

static void ball_obj(int n, int x, int y, int color)
{
    if (x < -8 || x > 244 || y < -8 || y > 164) {
        hide_obj(n);
        return;
    }
    set_obj(n, x - 4, y - 4, 0, ATTR1_SIZE8, 0 | ATTR2_PRIO(1) | ATTR2_PAL(color));
}

// ---------------------------------------------------------------- game

static int pick_color(void)
{
    unsigned m = chain_colors(&chain);
    if (!m) m = (1u << chain.ncolors) - 1;
    for (;;) {
        int c = rand_next(&rng) % NUM_COLORS;
        if (m & (1u << c)) return c;
    }
}

static void start_level(void)
{
    int total = 30 + level * 10;
    if (total > 100) total = 100;
    int ncolors = level < 3 ? 4 : 5;
    chain_init(&chain, total, ncolors, rand_next(&rng));
    cur_color = rand_next(&rng) % ncolors;
    next_color = rand_next(&rng) % ncolors;
    shot_active = 0;
    angle = 0;
    text_clear();
    draw_hud();
    state = ST_PLAY;
}

static int32_t level_speed(void)
{
    // roll in quickly until the head is a quarter of the way along
    if (chain.count > 0 && chain.pos[chain.count - 1] < (PATH_LEN / 4) << 8 && chain.to_spawn > 0)
        return ROLL_IN_SPEED;
    return 40 + level * 6;
}

static void fire(void)
{
    int a = angle >> 8;
    shot_active = 1;
    shot_color = cur_color;
    shot_x = (FROG_X << 8) + isin(a) * 10 / 16;
    shot_y = (FROG_Y << 8) - icos(a) * 10 / 16;
    shot_vx = isin(a) * SHOT_SPEED / 16;
    shot_vy = -icos(a) * SHOT_SPEED / 16;
    cur_color = next_color;
    next_color = pick_color();
}

static void update_shot(void)
{
    if (!shot_active) return;
    for (int step = 0; step < 2; step++) {
        shot_x += shot_vx / 2;
        shot_y += shot_vy / 2;
        int x = shot_x >> 8, y = shot_y >> 8;
        if (x < -8 || x > 248 || y < -8 || y > 168) {
            shot_active = 0;
            return;
        }
        int hit = chain_hit(&chain, x, y);
        if (hit >= 0) {
            score += chain_insert(&chain, hit, x, y, shot_color);
            shot_active = 0;
            // make sure the frog isn't holding a color that is gone
            unsigned m = chain_colors(&chain);
            if (m && !(m & (1u << cur_color))) cur_color = pick_color();
            if (m && !(m & (1u << next_color))) next_color = pick_color();
            return;
        }
    }
}

static void update_play(void)
{
    if (keys & KEY_LEFT) angle -= TURN_SPEED;
    if (keys & KEY_RIGHT) angle += TURN_SPEED;
    angle &= 0xFFFF;

    uint16_t pressed = keys & ~prev_keys;
    if ((pressed & KEY_A) && !shot_active) fire();
    if (pressed & KEY_B) {
        int t = cur_color;
        cur_color = next_color;
        next_color = t;
    }

    update_shot();

    ChainState cs = chain_update(&chain, level_speed(), &score);
    draw_hud();
    if (cs == CHAIN_LOST) {
        state = ST_OVER;
        text_center(9, "GAME OVER");
        text_center(11, "PRESS START");
    } else if (cs == CHAIN_CLEARED) {
        state = ST_CLEAR;
        timer = 120;
        text_center(9, "LEVEL CLEAR!");
    }
}

static void draw_objects(void)
{
    int a = angle >> 8;
    int s = isin(a) >> 4, c = icos(a) >> 4;   // 8.8

    // frog, affine sprite 0
    set_obj(OBJ_FROG, FROG_X - 16, FROG_Y - 16, ATTR0_AFFINE, ATTR1_SIZE32 | (0 << 9),
            FROG_TILE | ATTR2_PRIO(1) | ATTR2_PAL(FROG_PALBANK));
    oam[0 * 4 + 3] = c;
    oam[1 * 4 + 3] = s;
    oam[2 * 4 + 3] = -s;
    oam[3 * 4 + 3] = c;

    if (state == ST_PLAY || state == ST_CLEAR) {
        ball_obj(OBJ_MOUTH, FROG_X + (s * 10 >> 8), FROG_Y - (c * 10 >> 8), cur_color);
        ball_obj(OBJ_NEXT, FROG_X - (s * 9 >> 8), FROG_Y + (c * 9 >> 8), next_color);
    } else {
        hide_obj(OBJ_MOUTH);
        hide_obj(OBJ_NEXT);
    }

    if (shot_active)
        ball_obj(OBJ_SHOT, shot_x >> 8, shot_y >> 8, shot_color);
    else
        hide_obj(OBJ_SHOT);

    int n = OBJ_CHAIN;
    for (int i = chain.count - 1; i >= 0 && n < 128; i--, n++) {
        int x, y;
        chain_point(chain.pos[i], &x, &y);
        ball_obj(n, x, y, chain.color[i]);
    }
    for (; n < 128; n++) hide_obj(n);
}

int main(void)
{
    init_video();
    state = ST_TITLE;
    text_center(6, "ZUMA GBA");
    text_center(12, "PRESS START");
    chain.count = 0;

    for (;;) {
        prev_keys = keys;
        keys = ~REG_KEYINPUT & 0x03FF;
        uint16_t pressed = keys & ~prev_keys;
        rand_next(&rng);

        switch (state) {
        case ST_TITLE:
            if (pressed & KEY_START) {
                level = 1;
                score = 0;
                start_level();
            }
            break;
        case ST_PLAY:
            update_play();
            break;
        case ST_CLEAR:
            if (--timer <= 0) {
                level++;
                start_level();
            }
            break;
        case ST_OVER:
            if (pressed & KEY_START) {
                level = 1;
                score = 0;
                start_level();
            }
            break;
        }

        draw_objects();
        vsync();
        for (int i = 0; i < 128 * 4; i++) OAM[i] = oam[i];
    }
}
