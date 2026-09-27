// Zoomer DS: the track on the touch screen, the dashboard on the top screen.
#include <nds.h>
#include <math.h>
#include "assets.h"
#include "chain.h"
#include "gfx.h"
#include "scene.h"
#include "sound.h"
#include "save.h"

#define FINE_TURN 128             // aim speeds, 32768 a turn
#define FAST_TURN 512
#define SHOT_SPEED (7 << 8)       // 8.8 px/frame
#define ROLL_IN_SPEED 576         // fast roll-in at the start and in endless when short
#define REVERSE_SPEED (-640)
#define SLOW_FRAMES (6 * 60)
#define REVERSE_FRAMES (3 * 60)
#define MOUTH 15                  // mouth and back socket distance from the frog's centre
#define SOCKET 13
#define PAUSE_DIM 10
#define ENDLESS_LAYOUT 0          // endless always uses the same track

#define TOUCH_TURN_MIN 40        // touch aiming: the frog turns towards the stylus
#define TOUCH_TURN_MAX 300        // at a limited speed, so aim takes a moment
#define GUIDE_TOUCH_DOTS 12       // the guide only shows the direction, not the target

// top screen sprites
#define SPR_NOW 0
#define SPR_NEXT 1
#define SOCKET_Y 160              // ball sockets on the dashboard (see tools/gen_assets.py)
#define NOW_X 36
#define NEXT_X 92

typedef enum {
    ST_TITLE, ST_SETTINGS, ST_CREDITS,
    ST_PLAY, ST_PAUSE, ST_CLEAR, ST_DRAIN, ST_OVER,
} State;
typedef enum { MODE_ADVENTURE, MODE_ENDLESS } Mode;

static Chain chain;
static const Layout *layout;
static State state, settings_from;
static Mode mode;
static int level, theme, score, level_score, best_at_start, total_balls;
static int frames, play_frames, timer, menu_sel, rolling_in, streak;
static int aim, touch_target, blink, shake_timer;
static int slow_timer, reverse_timer;
static int cur_color, next_color;
static int shot_active, shot_color;
static int32_t shot_x, shot_y, shot_vx, shot_vy;
static int trail_x[4], trail_y[4];
static int credits_scroll, credits_rows;
static char msg1[20], msg2[24];
static int msg_timer;
static uint32_t rng = 0xC0FFEE;

// input for this frame
static uint32_t pressed, held;
static int touching, tapped, released, tx, ty;
static int aiming_by_touch;

static int isin(int a) { return sinLerp(a); }      // 4.12
static int icos(int a) { return cosLerp(a); }

// ---------------------------------------------------------------- input

#ifdef AUTOTEST
// Debug builds only: a scripted run through every menu and mode.
// Each step: at frame `at`, hold `keys` for `len` frames (KEY_TOUCH touches x, y).
typedef struct { int at; uint32_t keys; int len, x, y; } Step;
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"   // x, y only matter for touches
static const Step script[] = {
    { 120, KEY_DOWN, 1 }, { 130, KEY_DOWN, 1 }, { 140, KEY_A, 1 },          // settings
    { 190, KEY_A, 1 }, { 200, KEY_DOWN, 1 }, { 210, KEY_A, 1 },              // toggle aim, buttons
    { 220, KEY_DOWN, 1 }, { 230, KEY_A, 1 }, { 240, KEY_A, 1 },              // guide off and on
    { 260, KEY_TOUCH, 2, 128, 148 },                                         // tap BACK
    { 320, KEY_DOWN, 1 }, { 330, KEY_A, 1 },                                 // credits
    { 700, KEY_TOUCH, 2, 128, 160 },                                         // tap BACK
    { 760, KEY_TOUCH, 2, 128, 52 },                                          // tap ADVENTURE
    { 900, KEY_TOUCH, 70, 210, 30 },                                         // drag to aim, release to fire
    { 1000, KEY_TOUCH, 70, 30, 170 },
    { 1100, KEY_TOUCH, 2, 128, 96 },                                         // tap the frog: swap
    { 1150, KEY_R, 40 }, { 1200, KEY_LEFT, 20 }, { 1230, KEY_A, 1 }, { 1260, KEY_B, 1 },
    { 1300, KEY_START, 1 }, { 1330, KEY_DOWN, 1 }, { 1340, KEY_DOWN, 1 }, { 1350, KEY_A, 1 },  // pause, settings
    { 1400, KEY_B, 1 }, { 1430, KEY_UP, 1 }, { 1440, KEY_UP, 1 }, { 1450, KEY_A, 1 },          // back, resume
    { 1520, KEY_START, 1 }, { 1540, KEY_DOWN, 1 }, { 1550, KEY_A, 1 },                          // restart
    { 1700, KEY_START, 1 }, { 1720, KEY_DOWN, 1 }, { 1730, KEY_DOWN, 1 }, { 1740, KEY_DOWN, 1 },
    { 1750, KEY_A, 1 },                                                                         // quit
    { 1850, KEY_TOUCH, 2, 128, 84 },                                         // tap ENDLESS; then wait to lose
    { 100000, 0, 0 },
};
#endif

static void read_input(void)
{
    scanKeys();
    pressed = keysDown();
    held = keysHeld();
    uint32_t up = keysUp();
#ifdef AUTOTEST
    {
        static uint32_t prev;
        uint32_t now = 0;
        for (const Step *st = script; st->keys || st->at < 100000; st++) {
            if (frames >= st->at && frames < st->at + st->len) {
                now |= st->keys;
                if (st->keys & KEY_TOUCH) {
                    tx = st->x;
                    ty = st->y;
                }
            }
            if (st->at >= 100000) break;
        }
        // once in endless and lost, press A on the game over screen now and then
        if (state == ST_OVER && frames % 120 == 0) now |= KEY_A;
        held = now;
        pressed = now & ~prev;
        up = prev & ~now;
        prev = now;
    }
#endif
    touching = (held & KEY_TOUCH) != 0;
    tapped = (pressed & KEY_TOUCH) != 0;
    released = (up & KEY_TOUCH) != 0;
#ifndef AUTOTEST
    if (touching) {
        touchPosition tp;
        touchRead(&tp);
        tx = tp.px;
        ty = tp.py;
    }
#endif
    rand_next(&rng);
    rng += held + tx * 7 + ty;
}

static int tapped_in(int x, int y, int w, int h)    // pixels
{
    return tapped && tx >= x && tx < x + w && ty >= y && ty < y + h;
}

// ---------------------------------------------------------------- touch buttons

typedef struct {
    int y, w, h;            // tiles; buttons are centred across the touch screen
    const char *label;
} Button;

static void draw_buttons(const Button *b, int n, int sel)
{
    for (int i = 0; i < n; i++) button(BOT, b[i].y, b[i].w, b[i].h, b[i].label, i == sel ? TXT_HILITE : TXT_PANEL);
}

static int button_tapped(const Button *b)
{
    return tapped_in(button_left(b->w, b->label), b->y * 8, button_px_width(b->w, b->label), b->h * 8);
}

// The button tapped this frame, or chosen with the d-pad and A/Start. -1 if none.
static int buttons_update(const Button *b, int n, int *sel)
{
    for (int i = 0; i < n; i++)
        if (button_tapped(&b[i])) {
            *sel = i;
            draw_buttons(b, n, *sel);
            return i;
        }
    if (n > 1 && (pressed & KEY_UP)) {
        *sel = (*sel + n - 1) % n;
        sfx_move();
        draw_buttons(b, n, *sel);
    }
    if (n > 1 && (pressed & KEY_DOWN)) {
        *sel = (*sel + 1) % n;
        sfx_move();
        draw_buttons(b, n, *sel);
    }
    return (pressed & (KEY_A | KEY_START)) ? *sel : -1;
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

// ---------------------------------------------------------------- dashboard (top screen)

static void message(const char *a, const char *b, int frames_)
{
    put_str(msg1, a);
    put_str(msg2, b);
    msg_timer = frames_;
}

static void draw_dashboard_text(void)
{
    char buf[40], *p;
    text_clear_rows(TOP, 2, 3);
    p = buf;
    if (mode == MODE_ADVENTURE) {
        p = put_str(p, "LEVEL ");
        p = put_num0(p, level, 2);
    } else {
        p = put_str(p, "ENDLESS");
    }
    p = put_str(p, " - ");
    put_str(p, layout->name);
    text_style(TOP, 2, 2, buf, TXT_GOLD);

    text_style(TOP, 2, 4, "SCORE", TXT_TEAL);
    big_number(TOP, 2, 5, score, 7);
    if (mode == MODE_ADVENTURE) {            // right-aligned with the panel's other numbers
        int left = chain.to_spawn + chain.count;
        text_style(TOP, 20, 4, "BALLS LEFT", TXT_TEAL);
        big_number(TOP, 24, 5, left > 999 ? 999 : left, 3);
    } else {
        text_style(TOP, 25, 4, "SPEED", TXT_TEAL);
        big_number(TOP, 26, 5, 1 + play_frames / (60 * 15), 2);
    }
    text_style(TOP, 2, 8, "BEST", TXT_TEAL);
    text_num0(TOP, 7, 8, mode == MODE_ADVENTURE ? save.best_score : save.best_endless, 7, TXT_PLAIN);

    if (mode == MODE_ADVENTURE) {
        text_style(TOP, 2, 11, "PROGRESS", TXT_TEAL);
        // balls gone = the level's balls, less those still to come and those on the track
        int gone = total_balls - chain.to_spawn - chain.count;
        if (gone < 0) gone = 0;
        int pct = total_balls ? gone * 100 / total_balls : 0;
        text_num(TOP, 25, 11, pct, 3, TXT_PLAIN);
        text_style(TOP, 28, 11, "%", TXT_PLAIN);
        progress_bar(TOP, 2, 12, 28, gone, total_balls, chain.to_spawn == 0 ? BAR_GOLD : BAR_TEAL);
    } else {
        int secs = play_frames / 60;
        text_style(TOP, 2, 11, "TIME", TXT_TEAL);
        text_num0(TOP, 24, 11, secs / 60, 2, TXT_PLAIN);
        text_style(TOP, 26, 11, ":", TXT_PLAIN);
        text_num0(TOP, 27, 11, secs % 60, 2, TXT_PLAIN);
        progress_bar(TOP, 2, 12, 28, secs % 15, 15, BAR_TEAL);   // next speed-up
    }

    int danger = chain_danger(&chain);
    text_style(TOP, 2, 15, "DANGER", TXT_TEAL);
    const char *word = danger > 200 ? "HURRY!" : danger > 140 ? " CLOSE" : "  SAFE";
    text_style(TOP, 24, 15, danger > 200 && (frames & 16) ? "      " : word, danger > 140 ? TXT_GOLD : TXT_PLAIN);
    progress_bar(TOP, 2, 16, 28, danger, 256, BAR_RED);

    text_clear_rows(TOP, 19, 23);
    text_style(TOP, 3, 22, "NOW", TXT_TEAL);
    text_style(TOP, 10, 22, "NEXT", TXT_TEAL);
    if (msg_timer > 0) {
        text_center_in(TOP, 15, 16, 19, msg1, TXT_GOLD);
        text_center_in(TOP, 15, 16, 20, msg2, TXT_PLAIN);
    }
    // power-ups in effect
    p = buf;
    *p = 0;
    if (slow_timer > 0) {
        p = put_str(p, "SLOW ");
        p = put_num(p, (slow_timer + 59) / 60);
    }
    if (reverse_timer > 0) {
        if (p != buf) p = put_str(p, " ");
        p = put_str(p, "BACK ");
        p = put_num(p, (reverse_timer + 59) / 60);
    }
    text_center_in(TOP, 15, 16, 22, buf, TXT_GOLD);
}

static void dashboard_sprites(void)
{
    if (state == ST_PLAY || state == ST_PAUSE || state == ST_CLEAR) {
        spr(SPR_NOW, NOW_X - 16, SOCKET_Y - 16, SpriteSize_32x32, SUB_TILE_BALL, cur_color, 2);
        spr(SPR_NEXT, NEXT_X - 16, SOCKET_Y - 16, SpriteSize_32x32, SUB_TILE_BALL, next_color, 2);
    }
}

// ---------------------------------------------------------------- the playfield (3D)

static int ball_frame(int32_t pos) { return (int)(pos * BALL_FRAMES / (BALL_ROLL_PX << 8)); }

static void draw_chain(void)
{
    int32_t hidden = layout->hide << 8;
    for (int i = 0; i < chain.count; i++) {
        if (chain.pos[i] < hidden) continue;
        int x, y;
        chain_point(chain.pos[i], &x, &y);
        draw_ball(x, y, chain.color[i], ball_frame(chain.pos[i]), chain_angle(chain.pos[i]),
                  chain.power[i], 4096);
    }
}

static void draw_guide(void)
{
    if (!save.guide || shot_active || state != ST_PLAY) return;
    int dx = isin(aim), dy = -icos(aim);
    int32_t x = (layout->frog_x << 8) + dx * MOUTH / 16, y = (layout->frog_y << 8) + dy * MOUTH / 16;
    int strong = aiming_by_touch && touching;
    for (int i = 0; i < GUIDE_TOUCH_DOTS; i++) {
        x += dx * 6 / 16;
        y += dy * 6 / 16;
        int px = x >> 8, py = y >> 8;
        if (px < 0 || px > 255 || py < 0 || py > 191) break;
        if (chain_hit(&chain, px, py) >= 0) break;
        if (i < 2) continue;
        int a = (strong ? 22 : 14) - i * (strong ? 18 : 12) / GUIDE_TOUCH_DOTS;
        draw_dot(px, py, ball_rgb[cur_color], a);
    }
}

static void draw_play(void)
{
    int fx = layout->frog_x, fy = layout->frog_y;
    draw_chain();
    draw_guide();
    draw_frog(fx, fy, aim, blink > 0);
    if (state != ST_DRAIN && state != ST_OVER) {
        int dx = isin(aim), dy = -icos(aim);
        draw_ball(fx + dx * MOUTH / 4096, fy + dy * MOUTH / 4096, cur_color, 0, aim + 8192, 0, 4096);
        draw_ball(fx - dx * SOCKET / 4096, fy - dy * SOCKET / 4096, next_color, 0, aim + 8192, 0, 2900);
    }
    if (shot_active) {
        int angle = aim + 8192;
        for (int i = 3; i >= 1; i--)
            draw_dot(trail_x[i], trail_y[i], ball_rgb[shot_color], 14 - i * 4);
        draw_ball(shot_x >> 8, shot_y >> 8, shot_color, frames / 2, angle, 0, 4096);
    }
}

// Title parade: two rows of balls rolling along the top and bottom borders.
static void draw_parade(void)
{
    for (int row = 0; row < 2; row++) {
        int y = row ? 170 : 22;
        int dir = row ? -1 : 1;
        int off = (frames * 3 / 2) % 14;
        for (int i = -1; i < 20; i++) {
            int x = row ? 256 - (i * 14 + off) : i * 14 + off;
            int col = ((i - frames * 3 / 2 / 14 * dir) % 5 + 5) % 5;
            draw_ball(x, y, (col * 3 + row) % NUM_COLORS, (x * BALL_FRAMES / BALL_ROLL_PX) * dir,
                      row ? 16384 : 0, 0, 4096);
        }
    }
}

static void render(void)
{
    int shx = 0, shy = 0;
    if (shake_timer > 0) {
        shx = (int)(rand_next(&rng) % 7) - 3;
        shy = (int)(rand_next(&rng) % 7) - 3;
    }
    shake(-shx, -shy);
    scene_begin(shx, shy);
    if (state == ST_TITLE || (state == ST_SETTINGS && settings_from == ST_TITLE) || state == ST_CREDITS)
        draw_parade();
    else
        draw_play();
    fx_draw();
    scene_end();

    spr_hide_all();
    if (state >= ST_PLAY) dashboard_sprites();
}

static void frame(void)
{
    render();
    gfx_frame();
    sound_update();
    fx_update();
    frames++;
    if (shake_timer > 0) shake_timer--;
    if (blink > 0) blink--;
    else if (rand_next(&rng) % 200 == 0) blink = 8;
}

// ---------------------------------------------------------------- screens

static const Button title_buttons[] = {
    { 5, 16, 3, "ADVENTURE" }, { 9, 16, 3, "ENDLESS" }, { 13, 16, 3, "SETTINGS" }, { 17, 16, 3, "CREDITS" },
};
enum { MAIN_ADVENTURE, MAIN_ENDLESS, MAIN_SETTINGS, MAIN_CREDITS, MAIN_COUNT };

static void draw_title_text(void)
{
    char buf[40], *p;
    text_clear(TOP);
    text_scroll(TOP, 0);
    if (save.best_score > 0 || save.best_endless > 0) {
        p = put_str(buf, "BEST ");
        p = put_num0(p, save.best_score, 7);
        p = put_str(p, "  LEVEL ");
        put_num0(p, save.best_level, 2);
        text_center(TOP, 17, buf, TXT_GOLD);
        p = put_str(buf, "ENDLESS ");
        put_num0(p, save.best_endless, 7);
        text_center(TOP, 19, buf, TXT_GOLD);
    } else {
        text_center(TOP, 18, "TOUCH A BUTTON TO PLAY", TXT_GOLD);
    }
    if (!save_available) text_center(TOP, 20, "NO SD CARD - SCORES WON'T SAVE", TXT_PLAIN);
    text_clear(BOT);
    draw_buttons(title_buttons, MAIN_COUNT, menu_sel);
}

static void go_title(void)
{
    fade(1);
    music_stop();
    dim(BOT, 0);
    dim(TOP, 0);
    gfx_picture(TOP, title_top_pic);
    gfx_picture(BOT, title_bottom_pic);
    fx_clear();
    shake_timer = 0;
    state = ST_TITLE;
    menu_sel = MAIN_ADVENTURE;
    draw_title_text();
    fade(0);
    music_play(0);
}

// ---- settings

enum { SET_AIM, SET_BUTTONS, SET_GUIDE, SET_BACK, SET_COUNT };

static void settings_buttons(Button *b)
{
    b[SET_AIM] = (Button){ 4, 22, 3, save.dpad_fast ? "AIM   DPAD FAST" : "AIM   DPAD FINE" };
    b[SET_BUTTONS] = (Button){ 8, 22, 3, save.swap_buttons ? "SHOOT  B BUTTON" : "SHOOT  A BUTTON" };
    b[SET_GUIDE] = (Button){ 12, 22, 3, save.guide ? "AIM GUIDE  ON" : "AIM GUIDE  OFF" };
    b[SET_BACK] = (Button){ 17, 12, 3, "BACK" };
}

static void draw_controls_help(void)
{
    text_clear(TOP);
    panel(TOP, 3, 6, 26, 16);
    text_center(TOP, 7, "CONTROLS", TXT_HILITE);
    text_style(TOP, 5, 9, "STYLUS  HOLD TO AIM", TXT_PANEL);
    text_style(TOP, 5, 10, "        LIFT TO SHOOT", TXT_PANEL);
    text_style(TOP, 5, 11, "        TAP FROG: SWAP", TXT_PANEL);
    text_style(TOP, 5, 13, save.dpad_fast ? "DPAD    SPIN FAST" : "DPAD    AIM FINE", TXT_PANEL);
    text_style(TOP, 5, 14, save.dpad_fast ? "L  R    AIM FINE" : "L  R    SPIN FAST", TXT_PANEL);
    text_style(TOP, 5, 15, save.swap_buttons ? "B       SHOOT" : "A       SHOOT", TXT_PANEL);
    text_style(TOP, 5, 16, save.swap_buttons ? "A       SWAP BALLS" : "B       SWAP BALLS", TXT_PANEL);
    text_style(TOP, 5, 17, "START   PAUSE", TXT_PANEL);
    text_center(TOP, 19, "POWER BALLS: SLOW,", TXT_DIM);
    text_center(TOP, 20, "REVERSE AND BOMB", TXT_DIM);
}

static void open_settings(State from)
{
    Button b[SET_COUNT];
    settings_from = from;
    state = ST_SETTINGS;
    menu_sel = SET_AIM;
    text_clear(BOT);
    settings_buttons(b);
    draw_buttons(b, SET_COUNT, menu_sel);
    draw_controls_help();
    dim(TOP, 8);
}

static void draw_pause_menu(void);

static void update_settings(void)
{
    Button b[SET_COUNT];
    settings_buttons(b);
    int pick = buttons_update(b, SET_COUNT, &menu_sel);
    if ((pressed & KEY_B) || pick == SET_BACK) {
        sfx_select();
        dim(TOP, 0);
        if (settings_from == ST_PAUSE) {
            state = ST_PAUSE;
            menu_sel = 2;
            text_clear(TOP);                    // wipe the controls help
            draw_dashboard_text();
            draw_pause_menu();
            dim(TOP, PAUSE_DIM);
        } else {
            state = ST_TITLE;
            menu_sel = MAIN_SETTINGS;
            draw_title_text();
        }
        return;
    }
    if (pick < 0 && !(pressed & (KEY_LEFT | KEY_RIGHT))) return;
    if (pick < 0) pick = menu_sel;
    if (pick == SET_AIM) save.dpad_fast ^= 1;
    if (pick == SET_BUTTONS) save.swap_buttons ^= 1;
    if (pick == SET_GUIDE) save.guide ^= 1;
    save_write();
    sfx_select();
    text_clear(BOT);
    settings_buttons(b);
    draw_buttons(b, SET_COUNT, menu_sel);
    draw_controls_help();
}

// ---- credits

static const struct { const char *role, *name; } credits[] = {
    { "GAME DIRECTOR", "HEATH" }, { "CREATIVE DIRECTOR", "CLAUDE" },
    { "TECHNICAL DIRECTOR", "CLAUDE" }, { "PRODUCER", "CLAUDE" },
    { "LEAD GAME DESIGNER", "CLAUDE" }, { "LEVEL DESIGNER", "CLAUDE" },
    { "SYSTEMS DESIGNER", "CLAUDE" }, { "NARRATIVE DESIGNER", "CLAUDE" },
    { "LEAD PROGRAMMER", "CLAUDE" }, { "GAMEPLAY PROGRAMMER", "CLAUDE" },
    { "ENGINE PROGRAMMER", "CLAUDE" }, { "3D PROGRAMMER", "CLAUDE" },
    { "AUDIO PROGRAMMER", "CLAUDE" }, { "TOOLS PROGRAMMER", "CLAUDE" },
    { "UI PROGRAMMER", "CLAUDE" }, { "PORTING ENGINEER", "CLAUDE" },
    { "BUILD ENGINEER", "CLAUDE" }, { "ART DIRECTOR", "CLAUDE" },
    { "LEAD ARTIST", "CLAUDE" }, { "PIXEL ARTIST", "CLAUDE" },
    { "ENVIRONMENT ARTIST", "CLAUDE" }, { "CHARACTER ARTIST", "CLAUDE" },
    { "VFX ARTIST", "CLAUDE" }, { "ANIMATOR", "CLAUDE" },
    { "UI ARTIST", "CLAUDE" }, { "COMPOSER", "CLAUDE" },
    { "SOUND DESIGNER", "CLAUDE" }, { "QA LEAD", "CLAUDE" },
    { "QA TESTER", "HEATH" }, { "BALANCE TESTER", "HEATH" },
    { "LOCALIZATION", "CLAUDE" }, { "MARKETING", "CLAUDE" },
    { "COMMUNITY MANAGER", "CLAUDE" }, { "FROG WRANGLER", "CLAUDE" },
    { "BALL POLISHER", "CLAUDE" }, { "STYLUS TESTER", "CLAUDE" },
};
#define NUM_ROLES ((int)(sizeof(credits) / sizeof(credits[0])))
#define CREDITS_LEAD 24           // blank rows so the list starts below the screen
#define CREDITS_HEAD 4            // "ZOOMER DS", blank, "CREDITS", blank
#define CREDITS_END (CREDITS_LEAD + CREDITS_HEAD + NUM_ROLES * 3 + 3)

static const Button back_button = { 18, 12, 3, "BACK" };

// Write virtual credits row r into the (32-row, wrapping) top text map.
static void credits_write_row(int r)
{
    text_clear_rows(TOP, r & 31, (r & 31) + 1);
    int i = r - CREDITS_LEAD;
    if (i == 0) text_center(TOP, r, "ZOOMER DS", TXT_GOLD);
    if (i == 2) text_center(TOP, r, "CREDITS", TXT_PLAIN);
    i -= CREDITS_HEAD;
    if (i < 0) return;
    int role = i / 3;
    if (role < NUM_ROLES) {
        if (i % 3 == 0) text_center(TOP, r, credits[role].role, TXT_PLAIN);
        if (i % 3 == 1) text_center(TOP, r, credits[role].name, TXT_GOLD);
    } else if (r == CREDITS_END) {
        text_center(TOP, r, "THANKS FOR PLAYING!", TXT_GOLD);
    }
}

static void start_credits(void)
{
    state = ST_CREDITS;
    text_clear(TOP);
    text_clear(BOT);
    dim(TOP, 11);
    button(BOT, back_button.y, back_button.w, back_button.h, back_button.label, TXT_HILITE);
    credits_scroll = 0;
    for (credits_rows = 0; credits_rows < 25; credits_rows++) credits_write_row(credits_rows);
    text_scroll(TOP, 0);
}

static void update_credits(void)
{
    if (button_tapped(&back_button) || (pressed & (KEY_A | KEY_B | KEY_START))) {
        sfx_select();
        dim(TOP, 0);
        state = ST_TITLE;
        menu_sel = MAIN_CREDITS;
        draw_title_text();
        return;
    }
    // scroll until the last line sits in the middle of the screen, then hold
    int top = credits_scroll >> 3;
    if ((frames & 1) && top < CREDITS_END - 11) credits_scroll++;
    top = credits_scroll >> 3;
    while (credits_rows <= top + 24) credits_write_row(credits_rows++);
    text_scroll(TOP, credits_scroll & 255);
}

// ---------------------------------------------------------------- starting play

static int pick_color(void)
{
    unsigned m = chain_colors(&chain);
    if (!m) m = (1u << chain.ncolors) - 1;
    for (;;) {
        int c = rand_next(&rng) % NUM_COLORS;
        if (m & (1u << c)) return c;
    }
}

static void begin_play(int layout_index, int theme_index, int song, int total, int ncolors, int pow_chance)
{
    fade(1);
    music_stop();
    dim(BOT, 0);
    dim(TOP, 0);
    theme = theme_index;
    layout = &layouts[layout_index];
    chain_set_layout(layout);
    gfx_picture(BOT, level_pic[layout_index][theme]);
    gfx_picture(TOP, dash_pic[theme]);
    chain_init(&chain, total, ncolors, rand_next(&rng));
    chain.pow_chance = pow_chance;
    total_balls = total;
    cur_color = rand_next(&rng) % ncolors;
    next_color = rand_next(&rng) % ncolors;
    shot_active = 0;
    aim = 0;
    rolling_in = 1;
    play_frames = 0;
    streak = 0;
    slow_timer = reverse_timer = 0;
    shake_timer = 0;
    fx_clear();
    text_clear(BOT);
    text_clear(TOP);
    text_scroll(TOP, 0);
    state = ST_PLAY;
    message(mode == MODE_ADVENTURE ? "GET READY!" : "ENDLESS!", theme_names[theme], 150);
    draw_dashboard_text();
    fade(0);
    music_play(song);
}

static void start_level(void)
{
    mode = MODE_ADVENTURE;
    level_score = score;
    record_scores();                         // remembers the furthest level reached
    int total = 30 + level * 5;
    if (total > 110) total = 110;
    int ncolors = level < 3 ? 3 : level < 6 ? 4 : 5;
    int li = (level - 1) % NUM_LAYOUTS, ti = (level - 1) % NUM_THEMES;
    begin_play(li, ti, ti % NUM_SONGS, total, ncolors, level < 2 ? 0 : 22);
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
    int ti = rand_next(&rng) % NUM_THEMES;
    begin_play(ENDLESS_LAYOUT, ti, rand_next(&rng) % NUM_SONGS, -1, 4, 18);
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
    if (reverse_timer > 0) return REVERSE_SPEED;
    // roll in quickly until the head is a quarter of the way along the visible
    // track; endless does this again whenever the chain gets short
    if (mode == MODE_ENDLESS) rolling_in = 1;
    if (rolling_in) {
        if (chain.to_spawn != 0 && chain_danger(&chain) < 64) return ROLL_IN_SPEED;
        rolling_in = 0;
    }
    // don't leave the track sitting empty while new balls creep out of the serpent
    if (chain.to_spawn != 0 && chain_danger(&chain) < 20) return ROLL_IN_SPEED;
    int speed;
    if (mode == MODE_ENDLESS)
        speed = 51 + play_frames / (60 * 15);      // a little faster every 15 seconds
    else
        speed = 36 + level * 4;
    if (speed > 120) speed = 120;
    if (slow_timer > 0) speed /= 3;
    return speed;                                  // 8.8 px/frame
}

static void fire(void)
{
    int dx = isin(aim), dy = -icos(aim);
    shot_active = 1;
    shot_color = cur_color;
    shot_x = (layout->frog_x << 8) + dx * MOUTH / 16;
    shot_y = (layout->frog_y << 8) + dy * MOUTH / 16;
    shot_vx = dx * (SHOT_SPEED >> 8) / 16;
    shot_vy = dy * (SHOT_SPEED >> 8) / 16;
    for (int i = 0; i < 4; i++) {
        trail_x[i] = shot_x >> 8;
        trail_y[i] = shot_y >> 8;
    }
    cur_color = next_color;
    next_color = pick_color();
    sfx_shoot(layout->frog_x);
}

static void swap_balls(void)
{
    int t = cur_color;
    cur_color = next_color;
    next_color = t;
    sfx_swap();
}

static void fix_colors(void)
{
    // make sure the frog isn't holding a colour that is gone
    unsigned m = chain_colors(&chain);
    if (m && !(m & (1u << cur_color))) cur_color = pick_color();
    if (m && !(m & (1u << next_color))) next_color = pick_color();
}

static void update_shot(void)
{
    if (!shot_active) return;
    for (int i = 3; i > 0; i--) {
        trail_x[i] = trail_x[i - 1];
        trail_y[i] = trail_y[i - 1];
    }
    trail_x[0] = shot_x >> 8;
    trail_y[0] = shot_y >> 8;
    for (int step = 0; step < 4; step++) {
        shot_x += shot_vx / 4;
        shot_y += shot_vy / 4;
        int x = shot_x >> 8, y = shot_y >> 8;
        if (x < -12 || x > 268 || y < -12 || y > 204) {
            shot_active = 0;
            streak = 0;
            return;
        }
        int hit = chain_hit(&chain, x, y);
        if (hit >= 0) {
            int pts = chain_insert(&chain, hit, x, y, shot_color);
            shot_active = 0;
            if (pts) {
                streak++;
                if (streak >= 3) {
                    int bonus = 50 * streak;
                    char b[16];
                    put_num(put_str(b, "+"), bonus);
                    pts += bonus;
                    message("CHAIN BONUS", b, 90);
                    fx_stars(x, y, 8);
                }
            } else {
                streak = 0;
                sfx_insert(x);
            }
            score += pts;
            fix_colors();
            return;
        }
    }
}

// Turn this frame's chain events into effects, sounds and power-ups.
static void handle_events(void)
{
    for (int i = 0; i < chain.npopped; i++) {
        Popped *p = &chain.popped[i];
        fx_burst(p->x, p->y, ball_rgb[p->color], 7);
        if (p->power == POW_BOMB) {
            fx_ring(p->x, p->y, RGB15(31, 20, 6), 1);
            fx_burst(p->x, p->y, RGB15(31, 24, 8), 24);
            shake_timer = 24;
        } else if (p->power) {
            fx_ring(p->x, p->y, RGB15(20, 28, 31), 1);
            fx_stars(p->x, p->y, 6);
        }
    }
    for (int i = 0; i < chain.nmatches; i++) {
        Match *m = &chain.matches[i];
        fx_popup(m->x, m->y - 8, m->points, m->combo);
        fx_ring(m->x, m->y, RGB15(31, 31, 31), 0);
        sfx_match(m->combo, m->x);
        if (m->combo >= 2) {
            char b[16];
            put_num(put_str(b, "COMBO X"), m->combo);
            message(b, m->combo >= 4 ? "AMAZING!" : m->combo >= 3 ? "GREAT!" : "NICE!", 90);
            fx_stars(m->x, m->y, 4 + m->combo * 2);
        }
    }
    if (chain.contacts && !chain.nmatches) sfx_contact();
    if (chain.triggered & (1u << POW_SLOW)) {
        slow_timer = SLOW_FRAMES;
        message("SLOW DOWN!", "THE CHAIN CRAWLS", 90);
        sfx_power(POW_SLOW);
    }
    if (chain.triggered & (1u << POW_REVERSE)) {
        reverse_timer = REVERSE_FRAMES;
        message("REVERSE!", "BACK IT GOES", 90);
        sfx_power(POW_REVERSE);
    }
    if (chain.triggered & (1u << POW_BOMB)) {
        message("BOOM!", "BOMB BLAST", 90);
        sfx_power(POW_BOMB);
    }
    chain_events_clear(&chain);
}

static const Button pause_buttons[] = {
    { 4, 16, 3, "RESUME" }, { 8, 16, 3, "RESTART" }, { 12, 16, 3, "SETTINGS" }, { 16, 16, 3, "QUIT" },
};
enum { PAUSE_RESUME, PAUSE_RESTART, PAUSE_SETTINGS, PAUSE_QUIT, PAUSE_COUNT };

static void draw_pause_menu(void)
{
    text_clear(BOT);
    draw_buttons(pause_buttons, PAUSE_COUNT, menu_sel);
    text_center_in(TOP, 15, 16, 19, "PAUSED", TXT_GOLD);
}

static void pause_game(void)
{
    state = ST_PAUSE;
    menu_sel = PAUSE_RESUME;
    music_stop();
    dim(BOT, PAUSE_DIM);
    dim(TOP, PAUSE_DIM);
    aiming_by_touch = 0;
    draw_pause_menu();
}

static void resume_play(void)
{
    dim(BOT, 0);
    dim(TOP, 0);
    text_clear(BOT);
    state = ST_PLAY;
    music_resume();
}

static const Button over_buttons[] = { { 13, 14, 3, "RETRY" }, { 17, 14, 3, "MENU" } };

static void game_over(void)
{
    char buf[24];
    state = ST_OVER;
    record_scores();
    menu_sel = 0;
    dim(BOT, 8);
    text_clear(BOT);
    panel(BOT, 5, 3, 22, 9);
    text_center(BOT, 5, "GAME OVER", TXT_HILITE);
    put_num0(put_str(buf, "SCORE "), score, 7);
    text_center(BOT, 7, buf, TXT_PANEL);
    if (score > best_at_start) text_center(BOT, 9, "NEW BEST!", TXT_HILITE);
    else if (mode == MODE_ADVENTURE) {
        put_num(put_str(buf, "REACHED LEVEL "), level);
        text_center(BOT, 9, buf, TXT_PANEL);
    }
    draw_buttons(over_buttons, 2, menu_sel);
}

static void update_aim(void)
{
    uint32_t fine_l = save.dpad_fast ? KEY_L : KEY_LEFT;
    uint32_t fine_r = save.dpad_fast ? KEY_R : KEY_RIGHT;
    uint32_t fast_l = save.dpad_fast ? KEY_LEFT : KEY_L;
    uint32_t fast_r = save.dpad_fast ? KEY_RIGHT : KEY_R;
    if (held & fine_l) aim -= FINE_TURN;
    if (held & fine_r) aim += FINE_TURN;
    if (held & fast_l) aim -= FAST_TURN;
    if (held & fast_r) aim += FAST_TURN;

    int fx = layout->frog_x, fy = layout->frog_y;
    if (tapped) {
        int dx = tx - fx, dy = ty - fy;
        if (dx * dx + dy * dy < 22 * 22) {
            swap_balls();                          // tap the frog to swap
            aiming_by_touch = 0;
        } else {
            aiming_by_touch = 1;
            touch_target = aim;
        }
    }
    if (aiming_by_touch && touching) {
        int dx = tx - fx, dy = ty - fy;
        if (dx * dx + dy * dy > 8 * 8)
            touch_target = (int)(atan2f((float)dx, (float)-dy) * (32768.0f / (2.0f * 3.14159265f))) & 32767;
        // turn the short way round, quickly at first and slowing as it lines up
        int diff = ((touch_target - aim + 16384) & 32767) - 16384;
        int step = (diff < 0 ? -diff : diff) / 6;
        if (step < TOUCH_TURN_MIN) step = TOUCH_TURN_MIN;
        if (step > TOUCH_TURN_MAX) step = TOUCH_TURN_MAX;
        if (diff > step) aim += step;
        else if (diff < -step) aim -= step;
        else aim = touch_target;
    }
    aim &= 32767;
    if (aiming_by_touch && released) {
        aiming_by_touch = 0;
        if (!shot_active) fire();
    }
}

#ifdef AUTOPLAY
// Debug builds only: aim at the nearest visible ball of the frog's colour and fire.
static void autoplay(void)
{
    if (shot_active || frames % 24) return;
    int best = -1, best_d = 1 << 30;
    for (int i = 0; i < chain.count; i++) {
        if (chain.pos[i] < layout->hide << 8 || chain.color[i] != cur_color) continue;
        int x, y;
        chain_point(chain.pos[i], &x, &y);
        int dx = x - layout->frog_x, dy = y - layout->frog_y, d = dx * dx + dy * dy;
        if (d < best_d) {
            best_d = d;
            best = i;
        }
    }
    if (best < 0) {
        swap_balls();
        return;
    }
    int x, y;
    chain_point(chain.pos[best], &x, &y);
    aim = (int)(atan2f((float)(x - layout->frog_x), (float)(layout->frog_y - y)) * (32768.0f / 6.2831853f)) & 32767;
    fire();
}
#endif

static void update_play(void)
{
#ifdef AUTOPLAY
    autoplay();
#endif
    if (pressed & KEY_START) {
        pause_game();
        return;
    }
    update_aim();
    uint32_t shoot = save.swap_buttons ? KEY_B : KEY_A;
    uint32_t swap = save.swap_buttons ? KEY_A : KEY_B;
    if ((pressed & shoot) && !shot_active) fire();
    if (pressed & swap) swap_balls();

    update_shot();

    play_frames++;
    if (mode == MODE_ENDLESS && play_frames == 60 * 90) {
        chain.ncolors = 5;
        message("NEW COLOUR!", "PURPLE JOINS IN", 120);
    }
    if (slow_timer > 0) slow_timer--;
    if (reverse_timer > 0) reverse_timer--;
    if (msg_timer > 0) msg_timer--;

    ChainState cs = chain_update(&chain, chain_speed(), &score);
    handle_events();
    fix_colors();

    int danger = chain_danger(&chain);
    if (danger > 200 && play_frames % 40 == 0) sfx_danger();
    draw_dashboard_text();

    if (cs == CHAIN_LOST) {
        state = ST_DRAIN;
        timer = 0;
        sfx_over();
        message("OH NO!", "INTO THE PIT", 999);
        draw_dashboard_text();
    } else if (cs == CHAIN_CLEARED) {
        state = ST_CLEAR;
        timer = 180;
        sfx_clear();
        message("LEVEL CLEAR!", "WELL DONE", 999);
        draw_dashboard_text();
    }
}

// The rest of the chain pours into the pit.
static void update_drain(void)
{
    int score_dummy = 0;
    shot_active = 0;
    chain_update(&chain, 6 << 8, &score_dummy);
    chain_events_clear(&chain);
    while (chain.count > 0 && (chain.pos[chain.count - 1] >> 8) >= layout->len - 1) {
        int x, y;
        chain_point(chain.pos[chain.count - 1], &x, &y);
        fx_burst(x, y, ball_rgb[chain.color[chain.count - 1]], 3);
        if (chain.count % 3 == 0) sfx_swallow();
        chain.count--;
    }
    chain.to_spawn = 0;
    if (chain.count == 0 && ++timer > 40) game_over();
}

static void update_clear(void)
{
    if (timer % 12 == 0) {
        int x = 30 + (int)(rand_next(&rng) % 196), y = 30 + (int)(rand_next(&rng) % 130);
        fx_stars(x, y, 10);
        fx_ring(x, y, ball_rgb[rand_next(&rng) % NUM_COLORS], 1);
    }
    if (--timer <= 0 || (timer < 120 && (tapped || (pressed & (KEY_A | KEY_START))))) {
        level++;
        start_level();
    }
}

static void update_pause(void)
{
    if (pressed & KEY_B) {
        resume_play();
        return;
    }
    int pick = buttons_update(pause_buttons, PAUSE_COUNT, &menu_sel);
    switch (pick) {
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

static void update_over(void)
{
    int pick = buttons_update(over_buttons, 2, &menu_sel);
    if (pick == 0) restart();
    if (pick == 1) go_title();
}

static void update_title(void)
{
#ifdef AUTOPLAY
    if (frames > 90) {
        best_at_start = save.best_score;
        score = 0;
        if (AUTOPLAY == 0) {
            start_endless();
        } else {
            level = AUTOPLAY;
            start_level();
        }
        return;
    }
#endif
    int pick = buttons_update(title_buttons, MAIN_COUNT, &menu_sel);
    switch (pick) {
    case MAIN_ADVENTURE:
        sfx_select();
        new_adventure();
        break;
    case MAIN_ENDLESS:
        sfx_select();
        start_endless();
        break;
    case MAIN_SETTINGS:
        sfx_select();
        open_settings(ST_TITLE);
        break;
    case MAIN_CREDITS:
        sfx_select();
        start_credits();
        break;
    }
}

int main(void)
{
#ifdef DEBUGHUD
    defaultExceptionHandler();
#endif
    gfx_init();
    scene_init();
    sound_init();
    save_load();
    layout = &layouts[0];
    go_title();

    for (;;) {
        read_input();
        switch (state) {
        case ST_TITLE:
            update_title();
            break;
        case ST_SETTINGS:
            update_settings();
            break;
        case ST_CREDITS:
            update_credits();
            break;
        case ST_PLAY:
            update_play();
            break;
        case ST_PAUSE:
            update_pause();
            break;
        case ST_CLEAR:
            update_clear();
            break;
        case ST_DRAIN:
            update_drain();
            break;
        case ST_OVER:
            update_over();
            break;
        }
#ifdef DEBUGHUD
        text_num(TOP, 24, 0, frames, 7, TXT_PLAIN);
#endif
        frame();
    }
}
