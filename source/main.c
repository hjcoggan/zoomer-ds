#include "gba.h"
#include "assets.h"
#include "chain.h"
#include "sound.h"
#include "save.h"
#include "ui.h"

#define MAP_SBB 31
#define SHOT_SPEED 5
#define FINE_TURN 256             // 8.8 fixed, 256 units per full turn
#define FAST_TURN 1024
#define ROLL_IN_SPEED (3 << 7)    // fast roll-in at level start
#define PAUSE_DIM 9               // 0-16 brightness decrease behind menus
#define ENDLESS_LAYOUT 0          // endless always uses the same track

// OAM slots
#define OBJ_MOUTH 0
#define OBJ_NEXT  1
#define OBJ_FROG  2
#define OBJ_SHOT  3
#define OBJ_CHAIN 4

typedef enum {
    ST_TITLE, ST_MENU, ST_SETTINGS, ST_CREDITS,
    ST_PLAY, ST_PAUSE, ST_CLEAR, ST_OVER,
} State;
typedef enum { MODE_ADVENTURE, MODE_ENDLESS } Mode;

enum { MAIN_ADVENTURE, MAIN_ENDLESS, MAIN_SETTINGS, MAIN_CREDITS, MAIN_COUNT };
enum { PAUSE_RESUME, PAUSE_RESTART, PAUSE_SETTINGS, PAUSE_QUIT, PAUSE_COUNT };
enum { SET_AIM, SET_BUTTONS, SET_BACK, SET_COUNT };

static uint16_t oam[128 * 4];
static Chain chain;
static const Layout *layout;
static State state, settings_return;
static Mode mode;
static int level, score, level_score, best_at_start, timer, frames, play_frames;
static int menu_sel, rolling_in;
static int credits_scroll, credits_rows;
static int32_t angle;             // 8.8, 0 = up, clockwise
static int cur_color, next_color;
static int shot_active, shot_color;
static int32_t shot_x, shot_y, shot_vx, shot_vy;
static uint32_t rng = 0xC0FFEE;
static uint16_t keys, prev_keys;

static int isin(int a) { return sin_tab[a & 255]; }
static int icos(int a) { return sin_tab[(a + 64) & 255]; }

static int on_title_screen(void)
{
    return state == ST_TITLE || state == ST_MENU || state == ST_CREDITS ||
           (state == ST_SETTINGS && settings_return == ST_MENU);
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

static void dim_game(int on)
{
    REG_BLDCNT = on ? (BLD_DARKEN | BLD_BG0 | BLD_OBJ) : 0;
    REG_BLDY = PAUSE_DIM;
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
    for (int i = 0; i < 128; i++) hide_obj(i);
    if (state == ST_CREDITS) return;

    int a = angle >> 8;
    int s = isin(a) >> 4, c = icos(a) >> 4;   // 8.8
    int title = on_title_screen();
    int fx = title ? TITLE_FROG_X : layout->frog_x;
    int fy = title ? TITLE_FROG_Y : layout->frog_y;

    // frog, affine sprite 0
    set_obj(OBJ_FROG, fx - 16, fy - 16, ATTR0_AFFINE, ATTR1_SIZE32 | (0 << 9),
            FROG_TILE | ATTR2_PRIO(1) | ATTR2_PAL(FROG_PALBANK));
    oam[0 * 4 + 3] = c;
    oam[1 * 4 + 3] = s;
    oam[2 * 4 + 3] = -s;
    oam[3 * 4 + 3] = c;
    if (title) return;

    if (state != ST_OVER) {
        ball_obj(OBJ_MOUTH, fx + (s * 10 >> 8), fy - (c * 10 >> 8), cur_color);
        ball_obj(OBJ_NEXT, fx - (s * 9 >> 8), fy + (c * 9 >> 8), next_color);
    }
    if (shot_active) ball_obj(OBJ_SHOT, shot_x >> 8, shot_y >> 8, shot_color);

    int n = OBJ_CHAIN;
    int32_t hidden = layout->hide << 8;   // still inside the serpent's mouth
    for (int i = chain.count - 1; i >= 0 && n < 128; i--) {
        if (chain.pos[i] < hidden) break;
        int x, y;
        chain_point(chain.pos[i], &x, &y);
        ball_obj(n++, x, y, chain.color[i]);
    }
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

// ---------------------------------------------------------------- saving

static void record_scores(void)
{
    int changed = 0;
    if (mode == MODE_ADVENTURE) {
        if (score > save.best_score) {
            save.best_score = score;
            changed = 1;
        }
        if (level > save.best_level) {
            save.best_level = level;
            changed = 1;
        }
    } else if (score > save.best_endless) {
        save.best_endless = score;
        changed = 1;
    }
    if (changed) save_write();
}

// ---------------------------------------------------------------- title and menus

static const char *const main_items[MAIN_COUNT] = { "ADVENTURE", "ENDLESS", "SETTINGS", "CREDITS" };
static const char *const pause_items[PAUSE_COUNT] = { "RESUME", "RESTART", "SETTINGS", "QUIT" };

static void draw_title_text(void)
{
    text_clear();
    if (save.best_score > 0 || save.best_endless > 0) {
        char buf[32] = "BEST ";
        format_num(buf + 5, save.best_score, 6);
        const char *e = "  ENDLESS ";
        int k = 11;
        while (*e) buf[k++] = *e++;
        format_num(buf + k, save.best_endless, 6);
        text_center(17, buf, TXT_GOLD);
    }
}

static void draw_main_menu(void)
{
    menu_draw(10, 14, 0, main_items, MAIN_COUNT, menu_sel);
}

static void draw_pause_menu(void)
{
    menu_draw(5, 14, "PAUSED", pause_items, PAUSE_COUNT, menu_sel);
}

static void draw_settings(void)
{
    const char *items[SET_COUNT];
    items[SET_AIM] = save.swap_aim ? "AIM   DPAD FAST" : "AIM   DPAD FINE";
    items[SET_BUTTONS] = save.swap_buttons ? "SHOOT B  SWAP A" : "SHOOT A  SWAP B";
    items[SET_BACK] = "BACK";
    int h = menu_draw(5, 20, "SETTINGS", items, SET_COUNT, menu_sel);
    text_center(5 + h + 1, save.swap_aim ? "L R TO FINE TUNE" : "L R TO SPIN FAST", TXT_GOLD);
}

static void go_title(void)
{
    fade(1);
    music_stop();
    REG_BG1VOFS = 0;
    load_bg(title_pal, title_tiles);
    chain.count = 0;
    shot_active = 0;
    angle = 0;
    draw_title_text();
    state = ST_TITLE;
    fade(0);
    music_play();
}

static int menu_move(uint16_t pressed, int n)
{
    if (pressed & KEY_UP) {
        menu_sel = (menu_sel + n - 1) % n;
        sfx_swap();
        return 1;
    }
    if (pressed & KEY_DOWN) {
        menu_sel = (menu_sel + 1) % n;
        sfx_swap();
        return 1;
    }
    return 0;
}

static void open_settings(State back)
{
    settings_return = back;
    state = ST_SETTINGS;
    menu_sel = SET_AIM;
    text_clear();
    draw_settings();
}

// ---------------------------------------------------------------- credits

static const char *const credit_roles[] = {
    "GAME DIRECTOR", "CREATIVE DIRECTOR", "TECHNICAL DIRECTOR", "PRODUCER",
    "LEAD GAME DESIGNER", "LEVEL DESIGNER", "SYSTEMS DESIGNER", "NARRATIVE DESIGNER",
    "LEAD PROGRAMMER", "GAMEPLAY PROGRAMMER", "ENGINE PROGRAMMER", "GRAPHICS PROGRAMMER",
    "AUDIO PROGRAMMER", "TOOLS PROGRAMMER", "UI PROGRAMMER", "BUILD ENGINEER",
    "ART DIRECTOR", "LEAD ARTIST", "PIXEL ARTIST", "ENVIRONMENT ARTIST",
    "CHARACTER ARTIST", "ANIMATOR", "UI ARTIST", "COMPOSER", "SOUND DESIGNER",
    "QA LEAD", "QA TESTER", "BALANCE TESTER", "LOCALIZATION", "MARKETING",
    "COMMUNITY MANAGER", "FROG WRANGLER", "BALL POLISHER",
};
#define NUM_ROLES ((int)(sizeof(credit_roles) / sizeof(credit_roles[0])))
#define CREDITS_LEAD 20           // blank rows so the list starts below the screen
#define CREDITS_HEAD 4            // "ZUMA GBA", blank, "CREDITS", blank
#define CREDITS_END (CREDITS_LEAD + CREDITS_HEAD + NUM_ROLES * 3 + 3)

// Write virtual credits row r into the (32-row, wrapping) text map.
static void credits_write_row(int r)
{
    text_clear_row(r);
    int i = r - CREDITS_LEAD;
    if (i == 0) text_center(r, "ZUMA GBA", TXT_GOLD);
    if (i == 2) text_center(r, "CREDITS", TXT_PLAIN);
    i -= CREDITS_HEAD;
    if (i < 0) return;
    int role = i / 3;
    if (role < NUM_ROLES) {
        if (i % 3 == 0) text_center(r, credit_roles[role], TXT_PLAIN);
        if (i % 3 == 1) text_center(r, "CLAUDE", TXT_GOLD);
    } else if (r == CREDITS_END) {
        text_center(r, "THANKS FOR PLAYING!", TXT_GOLD);
    }
}

static void start_credits(void)
{
    state = ST_CREDITS;
    text_clear();
    dim_game(1);
    REG_BLDY = 12;
    credits_scroll = 0;
    for (credits_rows = 0; credits_rows < 21; credits_rows++) credits_write_row(credits_rows);
}

static void end_credits(void)
{
    REG_BG1VOFS = 0;
    dim_game(0);
    state = ST_MENU;
    menu_sel = MAIN_CREDITS;
    draw_title_text();
    draw_main_menu();
}

static void update_credits(uint16_t pressed)
{
    if (pressed & (KEY_A | KEY_B | KEY_START)) {
        end_credits();
        return;
    }
    // scroll until the last line sits in the middle of the screen, then hold
    int top = credits_scroll >> 3;
    if ((frames & 1) && top < CREDITS_END - 9) credits_scroll++;
    top = credits_scroll >> 3;
    while (credits_rows <= top + 20) credits_write_row(credits_rows++);
    REG_BG1VOFS = credits_scroll & 255;
}

// ---------------------------------------------------------------- game setup

static int pick_color(void)
{
    unsigned m = chain_colors(&chain);
    if (!m) m = (1u << chain.ncolors) - 1;
    for (;;) {
        int c = rand_next(&rng) % NUM_COLORS;
        if (m & (1u << c)) return c;
    }
}

static void draw_hud(void)
{
    text_at(0, 0, "SCORE");
    num_at(6, 0, score, 6);
    if (mode == MODE_ADVENTURE) {
        text_at(21, 0, "LEVEL");
        num_at(27, 0, level, 2);
    } else {
        int secs = play_frames / 60;
        text_at(19, 0, "TIME");
        num_at(24, 0, secs / 60, 2);
        text_at(26, 0, ":");
        num_at(27, 0, secs % 60, 2);
    }
}

static void begin_play(int layout_index, int theme, int total, int ncolors)
{
    fade(1);
    music_stop();
    layout = &layouts[layout_index];
    chain_set_layout(layout);
    load_bg(level_pal[layout_index][theme], level_tiles[layout_index][theme]);
    chain_init(&chain, total, ncolors, rand_next(&rng));
    cur_color = rand_next(&rng) % ncolors;
    next_color = rand_next(&rng) % ncolors;
    shot_active = 0;
    angle = 0;
    rolling_in = 1;
    play_frames = 0;
    text_clear();
    draw_hud();
    state = ST_PLAY;
    fade(0);
    music_play();
}

static void start_level(void)
{
    mode = MODE_ADVENTURE;
    level_score = score;
    record_scores();                         // remembers the furthest level reached
    // gentle ramp: more balls and colors every level or two
    int total = 25 + level * 5;
    if (total > 90) total = 90;
    int ncolors = level < 3 ? 3 : level < 6 ? 4 : 5;
    begin_play((level - 1) % NUM_LAYOUTS, (level - 1) % NUM_THEMES, total, ncolors);
}

static void new_adventure(void)
{
    mode = MODE_ADVENTURE;
    best_at_start = save.best_score;
    level = 1;
    score = 0;
    start_level();
}

static void start_endless(void)
{
    mode = MODE_ENDLESS;
    best_at_start = save.best_endless;
    score = 0;
    level = 0;
    begin_play(ENDLESS_LAYOUT, rand_next(&rng) % NUM_THEMES, -1, 4);
}

static void restart(void)
{
    if (mode == MODE_ENDLESS) {
        record_scores();
        start_endless();
    } else {
        score = level_score;
        start_level();
    }
}

// ---------------------------------------------------------------- play

static int32_t chain_speed(void)
{
    // roll in quickly until the head is a quarter of the way along
    // (measured from where balls leave the serpent's mouth). Endless mode
    // does this again whenever the chain gets short, so it never sits empty.
    if (mode == MODE_ENDLESS) rolling_in = 1;
    if (rolling_in) {
        int32_t target = (layout->hide + (layout->len - layout->hide) / 4) << 8;
        if (chain.count == 0 || chain.pos[chain.count - 1] < target)
            return ROLL_IN_SPEED;
        rolling_in = 0;
    }
    int speed;
    if (mode == MODE_ENDLESS)
        speed = 34 + play_frames / (60 * 15);   // a little faster every 15 seconds
    else
        speed = 24 + level * 3;
    return speed > 80 ? 80 : speed;             // 8.8 px/frame
}

static void fire(void)
{
    int a = angle >> 8;
    shot_active = 1;
    shot_color = cur_color;
    shot_x = (layout->frog_x << 8) + isin(a) * 10 / 16;
    shot_y = (layout->frog_y << 8) - icos(a) * 10 / 16;
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
        if (hit >= 0 && chain.pos[hit] >= layout->hide << 8) {
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
    record_scores();
    format_num(buf + 6, score, 6);
    panel(5, 18, 8);
    text_center(6, "GAME OVER", TXT_HILITE);
    text_center(8, buf, TXT_PANEL);
    if (score > best_at_start) text_center(9, "NEW BEST!", TXT_HILITE);
    text_center(11, "PRESS START", TXT_PANEL);
}

static void update_play(uint16_t pressed)
{
    if (pressed & KEY_START) {
        state = ST_PAUSE;
        menu_sel = PAUSE_RESUME;
        music_stop();
        dim_game(1);
        draw_pause_menu();
        return;
    }

    uint16_t fine_l = save.swap_aim ? KEY_L : KEY_LEFT;
    uint16_t fine_r = save.swap_aim ? KEY_R : KEY_RIGHT;
    uint16_t fast_l = save.swap_aim ? KEY_LEFT : KEY_L;
    uint16_t fast_r = save.swap_aim ? KEY_RIGHT : KEY_R;
    if (keys & fine_l) angle -= FINE_TURN;
    if (keys & fine_r) angle += FINE_TURN;
    if (keys & fast_l) angle -= FAST_TURN;
    if (keys & fast_r) angle += FAST_TURN;
    angle &= 0xFFFF;

    uint16_t shoot = save.swap_buttons ? KEY_B : KEY_A;
    uint16_t swap = save.swap_buttons ? KEY_A : KEY_B;
    if ((pressed & shoot) && !shot_active) {
        fire();
        sfx_shoot();
    }
    if (pressed & swap) {
        sfx_swap();
        int t = cur_color;
        cur_color = next_color;
        next_color = t;
    }

    update_shot();

    play_frames++;
    if (mode == MODE_ENDLESS && play_frames == 60 * 90) chain.ncolors = 5;

    int before = score;
    ChainState cs = chain_update(&chain, chain_speed(), &score);
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

static void resume_play(void)
{
    dim_game(0);
    text_clear();
    draw_hud();
    state = ST_PLAY;
    music_resume();
}

static void update_pause(uint16_t pressed)
{
    if (menu_move(pressed, PAUSE_COUNT)) draw_pause_menu();
    if (pressed & KEY_B) {
        resume_play();
        return;
    }
    if (!(pressed & (KEY_A | KEY_START))) return;
    switch (menu_sel) {
    case PAUSE_RESUME:
        resume_play();
        break;
    case PAUSE_RESTART:
        restart();
        break;
    case PAUSE_SETTINGS:
        open_settings(ST_PAUSE);
        break;
    case PAUSE_QUIT:
        record_scores();
        go_title();
        break;
    }
}

static void update_settings(uint16_t pressed)
{
    if (menu_move(pressed, SET_COUNT)) draw_settings();
    int back = (pressed & KEY_B) || ((pressed & (KEY_A | KEY_START)) && menu_sel == SET_BACK);
    if (!back && (pressed & (KEY_A | KEY_LEFT | KEY_RIGHT)) && menu_sel != SET_BACK) {
        if (menu_sel == SET_AIM) save.swap_aim ^= 1;
        if (menu_sel == SET_BUTTONS) save.swap_buttons ^= 1;
        save_write();
        sfx_swap();
        draw_settings();
    }
    if (!back) return;
    text_clear();
    if (settings_return == ST_PAUSE) {
        state = ST_PAUSE;
        menu_sel = PAUSE_SETTINGS;
        draw_pause_menu();
    } else {
        state = ST_MENU;
        menu_sel = MAIN_SETTINGS;
        draw_title_text();
        draw_main_menu();
    }
}

static void update_title(uint16_t pressed)
{
    // frog looks around, "PRESS START" blinks
    angle = ((isin(frames) * 20) >> 12) << 8;
    angle &= 0xFFFF;
    if (state == ST_TITLE) {
        text_center(15, (frames & 32) ? "           " : "PRESS START", TXT_PLAIN);
        if (pressed & (KEY_START | KEY_A)) {
            state = ST_MENU;
            menu_sel = MAIN_ADVENTURE;
            text_center(15, "           ", TXT_PLAIN);
            draw_main_menu();
        }
        return;
    }

    // main menu
    if (menu_move(pressed, MAIN_COUNT)) draw_main_menu();
    if (pressed & KEY_B) {
        state = ST_TITLE;
        draw_title_text();
        return;
    }
    if (!(pressed & (KEY_A | KEY_START))) return;
    switch (menu_sel) {
    case MAIN_ADVENTURE:
        new_adventure();
        break;
    case MAIN_ENDLESS:
        start_endless();
        break;
    case MAIN_SETTINGS:
        open_settings(ST_MENU);
        break;
    case MAIN_CREDITS:
        start_credits();
        break;
    }
}

int main(void)
{
    init_video();
    sound_init();
    save_load();
    layout = &layouts[0];
    go_title();

    for (;;) {
        prev_keys = keys;
        keys = ~REG_KEYINPUT & 0x03FF;
        uint16_t pressed = keys & ~prev_keys;
        rand_next(&rng);

        switch (state) {
        case ST_TITLE:
        case ST_MENU:
            update_title(pressed);
            break;
        case ST_SETTINGS:
            update_settings(pressed);
            break;
        case ST_CREDITS:
            update_credits(pressed);
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
