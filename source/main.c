#include "gba.h"
#include "assets.h"
#include "chain.h"
#include "sound.h"

#define FONT_CBB 3
#define TEXT_SBB 30
#define MAP_SBB 31
#define SHOT_SPEED 5
#define TURN_SPEED 256            // d-pad: fine aim, 8.8 fixed, 256 units per turn
#define FAST_TURN_SPEED 1024      // L/R shoulders: quick spin
#define ROLL_IN_SPEED (3 << 7)    // fast roll-in at level start
#define PAUSE_DIM 9               // 0-16 brightness decrease while paused

// OAM slots
#define OBJ_MOUTH 0
#define OBJ_NEXT  1
#define OBJ_FROG  2
#define OBJ_SHOT  3
#define OBJ_CHAIN 4

// text styles, matching FONT_STYLES in tools/gen_assets.py
#define TXT_PLAIN 0
#define TXT_PANEL 1
#define TXT_HILITE 2

typedef enum { ST_TITLE, ST_PLAY, ST_PAUSE, ST_CLEAR, ST_OVER } State;
enum { MENU_RESUME, MENU_RESTART, MENU_QUIT, MENU_COUNT };

static uint16_t oam[128 * 4];
static Chain chain;
static State state;
static int level, score, level_score, best, timer, frames;
static int menu_sel;
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

static void text_style(int x, int y, const char *s, int style)
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
        map[y * 32 + x] = (style * FONT_NCHARS + t) | (1 << 12);
    }
}

static int text_len(const char *s)
{
    int n = 0;
    while (s[n]) n++;
    return n;
}

static void text_at(int x, int y, const char *s) { text_style(x, y, s, TXT_PLAIN); }

static void text_center(int y, const char *s, int style)
{
    text_style((30 - text_len(s)) / 2, y, s, style);
}

static void format_num(char *buf, int v, int width)
{
    buf[width] = 0;
    for (int i = width - 1; i >= 0; i--) {
        buf[i] = '0' + v % 10;
        v /= 10;
    }
}

static void num_at(int x, int y, int v, int width)
{
    char buf[12];
    format_num(buf, v, width);
    text_at(x, y, buf);
}

// Gold-framed panel on the text layer, centered horizontally.
static void panel(int y, int w, int h)
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

static void draw_hud(void)
{
    text_at(0, 0, "SCORE");
    num_at(6, 0, score, 6);
    text_at(21, 0, "LEVEL");
    num_at(27, 0, level, 2);
}

// ---------------------------------------------------------------- video

static void load_bg(const uint16_t *pal, const uint32_t *tiles)
{
    PAL_BG[0] = pal[0];
    for (int i = BG_FIRST_COLOR; i < 256; i++) PAL_BG[i] = pal[i];
    volatile uint32_t *d = CHARBLOCK(0);
    for (int i = 0; i < BG_IMG_WORDS; i++) d[i] = tiles[i];
}

static void init_video(void)
{
    REG_DISPCNT = 0x0080;         // forced blank while loading
    REG_BLDCNT = BLD_DARKEN | BLD_BG0 | BLD_BG1 | BLD_OBJ | BLD_BD;
    REG_BLDY = 16;                // start black, screens fade in

    for (int i = 0; i < 16; i++) PAL_BG[16 + i] = font_pal[i];
    for (int i = 0; i < (int)(sizeof(obj_pal) / 2); i++) PAL_OBJ[i] = obj_pal[i];

    volatile uint32_t *d = CHARBLOCK(FONT_CBB);
    for (int i = 0; i < (int)(sizeof(font_tiles) / 4); i++) d[i] = font_tiles[i];
    for (int i = 0; i < (int)(sizeof(obj_tiles) / 4); i++) OBJ_TILES[i] = obj_tiles[i];

    volatile uint16_t *map = SCREENBLOCK(MAP_SBB);
    for (int y = 0; y < 32; y++)
        for (int x = 0; x < 32; x++)
            map[y * 32 + x] = (x < 30 && y < 20) ? y * 30 + x : 0;
    text_clear();

    REG_BG0CNT = BG_PRIO(3) | BG_CBB(0) | BG_SBB(MAP_SBB) | BG_8BPP;
    REG_BG1CNT = BG_PRIO(0) | BG_CBB(FONT_CBB) | BG_SBB(TEXT_SBB);

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

static void draw_objects(void)
{
    int a = angle >> 8;
    int s = isin(a) >> 4, c = icos(a) >> 4;   // 8.8
    int fx = state == ST_TITLE ? TITLE_FROG_X : FROG_X;
    int fy = state == ST_TITLE ? TITLE_FROG_Y : FROG_Y;

    // frog, affine sprite 0
    set_obj(OBJ_FROG, fx - 16, fy - 16, ATTR0_AFFINE, ATTR1_SIZE32 | (0 << 9),
            FROG_TILE | ATTR2_PRIO(1) | ATTR2_PAL(FROG_PALBANK));
    oam[0 * 4 + 3] = c;
    oam[1 * 4 + 3] = s;
    oam[2 * 4 + 3] = -s;
    oam[3 * 4 + 3] = c;

    if (state != ST_TITLE && state != ST_OVER) {
        ball_obj(OBJ_MOUTH, fx + (s * 10 >> 8), fy - (c * 10 >> 8), cur_color);
        ball_obj(OBJ_NEXT, fx - (s * 9 >> 8), fy + (c * 9 >> 8), next_color);
    } else {
        hide_obj(OBJ_MOUTH);
        hide_obj(OBJ_NEXT);
    }

    if (shot_active)
        ball_obj(OBJ_SHOT, shot_x >> 8, shot_y >> 8, shot_color);
    else
        hide_obj(OBJ_SHOT);

    int n = OBJ_CHAIN;
    for (int i = chain.count - 1; i >= 0 && n < 128; i--) {
        int x, y;
        chain_point(chain.pos[i], &x, &y);
        if (x > 236) continue;    // still inside the serpent's mouth
        ball_obj(n++, x, y, chain.color[i]);
    }
    for (; n < 128; n++) hide_obj(n);
}

static void frame(void)
{
    draw_objects();
    vsync();
    for (int i = 0; i < 128 * 4; i++) OAM[i] = oam[i];
    sound_update();
    frames++;
}

static void fade(int to_black)
{
    REG_BLDCNT = BLD_DARKEN | BLD_BG0 | BLD_BG1 | BLD_OBJ | BLD_BD;
    for (int i = 0; i <= 16; i += 2) {
        REG_BLDY = to_black ? i : 16 - i;
        frame();
    }
    if (!to_black) REG_BLDCNT = 0;
}

// ---------------------------------------------------------------- screens

static void go_title(void)
{
    fade(1);
    music_stop();
    load_bg(title_pal, title_tiles);
    chain.count = 0;
    shot_active = 0;
    angle = 0;
    text_clear();
    if (best > 0) {
        char buf[16] = "BEST ";
        format_num(buf + 5, best, 6);
        text_center(17, buf, TXT_PLAIN);
    }
    state = ST_TITLE;
    fade(0);
    music_play();
}

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
    fade(1);
    music_stop();
    int theme = (level - 1) % NUM_THEMES;
    load_bg(theme_pal[theme], theme_tiles[theme]);

    // gentle ramp: more balls and colors every level or two
    int total = 25 + level * 5;
    if (total > 90) total = 90;
    int ncolors = level < 3 ? 3 : level < 6 ? 4 : 5;
    chain_init(&chain, total, ncolors, rand_next(&rng));
    cur_color = rand_next(&rng) % ncolors;
    next_color = rand_next(&rng) % ncolors;
    shot_active = 0;
    angle = 0;
    level_score = score;
    text_clear();
    draw_hud();
    state = ST_PLAY;
    fade(0);
    music_play();
}

static void new_game(void)
{
    level = 1;
    score = 0;
    start_level();
}

static void draw_pause_menu(void)
{
    static const char *const items[MENU_COUNT] = { "RESUME", "RESTART", "QUIT" };
    panel(6, 12, 8);
    text_center(7, "PAUSED", TXT_HILITE);
    for (int i = 0; i < MENU_COUNT; i++) {
        int sel = i == menu_sel;
        text_style(10, 9 + i, sel ? ">" : " ", sel ? TXT_HILITE : TXT_PANEL);
        text_style(11, 9 + i, items[i], sel ? TXT_HILITE : TXT_PANEL);
    }
}

static void pause_game(void)
{
    state = ST_PAUSE;
    menu_sel = MENU_RESUME;
    music_stop();
    REG_BLDCNT = BLD_DARKEN | BLD_BG0 | BLD_OBJ;
    REG_BLDY = PAUSE_DIM;
    draw_pause_menu();
}

static void resume_game(void)
{
    REG_BLDCNT = 0;
    text_clear();
    draw_hud();
    state = ST_PLAY;
    music_resume();
}

static void update_pause(uint16_t pressed)
{
    if (pressed & KEY_UP) {
        menu_sel = (menu_sel + MENU_COUNT - 1) % MENU_COUNT;
        sfx_swap();
        draw_pause_menu();
    }
    if (pressed & KEY_DOWN) {
        menu_sel = (menu_sel + 1) % MENU_COUNT;
        sfx_swap();
        draw_pause_menu();
    }
    if (pressed & KEY_B) {
        resume_game();
        return;
    }
    if (pressed & (KEY_A | KEY_START)) {
        switch (menu_sel) {
        case MENU_RESUME:
            resume_game();
            break;
        case MENU_RESTART:
            score = level_score;
            start_level();
            break;
        case MENU_QUIT:
            go_title();
            break;
        }
    }
}

// ---------------------------------------------------------------- play

static int32_t level_speed(void)
{
    // roll in quickly until the head is a quarter of the way along
    if (chain.count > 0 && chain.pos[chain.count - 1] < (PATH_LEN / 4) << 8 && chain.to_spawn > 0)
        return ROLL_IN_SPEED;
    int speed = 24 + level * 3;           // 8.8 px/frame
    return speed > 64 ? 64 : speed;
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
            int pts = chain_insert(&chain, hit, x, y, shot_color);
            if (pts) sfx_match(chain.combo);
            score += pts;
            shot_active = 0;
            // make sure the frog isn't holding a color that is gone
            unsigned m = chain_colors(&chain);
            if (m && !(m & (1u << cur_color))) cur_color = pick_color();
            if (m && !(m & (1u << next_color))) next_color = pick_color();
            return;
        }
    }
}

static void game_over(void)
{
    char buf[16] = "SCORE ";
    state = ST_OVER;
    sfx_over();
    if (score > best) best = score;
    format_num(buf + 6, score, 6);
    panel(6, 16, 8);
    text_center(7, "GAME OVER", TXT_HILITE);
    text_center(9, buf, TXT_PANEL);
    text_center(11, "PRESS START", TXT_PANEL);
}

static void update_play(uint16_t pressed)
{
    if (pressed & KEY_START) {
        pause_game();
        return;
    }
    if (keys & KEY_LEFT) angle -= TURN_SPEED;
    if (keys & KEY_RIGHT) angle += TURN_SPEED;
    if (keys & KEY_L) angle -= FAST_TURN_SPEED;
    if (keys & KEY_R) angle += FAST_TURN_SPEED;
    angle &= 0xFFFF;

    if ((pressed & KEY_A) && !shot_active) {
        fire();
        sfx_shoot();
    }
    if (pressed & KEY_B) {
        sfx_swap();
        int t = cur_color;
        cur_color = next_color;
        next_color = t;
    }

    update_shot();

    int before = score;
    ChainState cs = chain_update(&chain, level_speed(), &score);
    if (score != before) sfx_match(chain.combo);
    draw_hud();
    if (cs == CHAIN_LOST) {
        game_over();
    } else if (cs == CHAIN_CLEARED) {
        state = ST_CLEAR;
        sfx_clear();
        timer = 150;
        panel(8, 16, 3);
        text_center(9, "LEVEL CLEAR!", TXT_HILITE);
    }
}

static void update_title(uint16_t pressed)
{
    // frog looks around, "PRESS START" blinks
    angle = ((isin(frames) * 20) >> 12) << 8;
    angle &= 0xFFFF;
    text_center(15, (frames & 32) ? "           " : "PRESS START", TXT_PLAIN);
    if (pressed & KEY_START) new_game();
}

int main(void)
{
    init_video();
    sound_init();
    go_title();

    for (;;) {
        prev_keys = keys;
        keys = ~REG_KEYINPUT & 0x03FF;
        uint16_t pressed = keys & ~prev_keys;
        rand_next(&rng);

        switch (state) {
        case ST_TITLE:
            update_title(pressed);
            break;
        case ST_PLAY:
            update_play(pressed);
            break;
        case ST_PAUSE:
            update_pause(pressed);
            break;
        case ST_CLEAR:
            if (--timer <= 0) {
                level++;
                start_level();
            }
            break;
        case ST_OVER:
            if (pressed & KEY_START) go_title();
            break;
        }

        frame();
    }
}
